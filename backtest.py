"""bmsit-quant-101 backtester: ONE strategy, ALL symbols, equal-weight portfolio, next-bar-open execution.

    python backtest.py strategies\\ma_crossover.py            # run one strategy
    python backtest.py --all                                 # run every strategy, print a leaderboard
    python backtest.py strategies\\mine.py --long-only --start 2026-01-01 --no-plot --json

Strategy contract: the file defines  def strategy(df) -> pd.Series  giving the DESIRED position per row:
+1 long, 0 flat, -1 short (floats allowed, clipped to [-1, 1]). It may also declare TIMEFRAME = "15m"
(default "1d"); the engine then loads data/<timeframe> (data/daily for 1d). The position is applied at the
NEXT bar's open, so a signal computed from this bar's close can never earn this bar's return.
Requires only pandas + matplotlib (numpy comes with pandas). Python 3.10+.
"""
import argparse, glob, importlib.util, json, math, os, sys, traceback, warnings
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX_SYMBOLS = {"NIFTY"}            # never traded; used as benchmark line + helper column nifty_close
TIMEFRAMES = ["1d", "1h", "30m", "15m", "5m", "1m"]
TRADING_DAYS_PER_YEAR = 246          # NSE; intraday bars/year = median bars per day x 246
CAPITAL = 1_000_000.0                # Rs 10 lakh split equally across symbols
COST_PER_ORDER = 20.0                # Rs per order (every position change is one order)
SLIPPAGE = 0.0005                    # 0.05% of traded value on every position change


class StrategyError(Exception):
    """Friendly, student-facing error (no traceback needed)."""


# ----------------------------------------------------------------------------------------- data
def data_dir_for(timeframe, data_arg=None):
    """Folder for a timeframe: data/daily (1d) or data/<tf>; with --data X: X (1d) or X/<tf> if it exists."""
    if timeframe not in TIMEFRAMES:
        raise StrategyError(f"TIMEFRAME must be one of {TIMEFRAMES}, got {timeframe!r}")
    if data_arg:
        base = data_arg if os.path.isdir(data_arg) else os.path.join(ROOT, data_arg)
        cands = [base] if timeframe == "1d" else [os.path.join(base, timeframe), base]
    else:
        cands = [os.path.join(ROOT, "data", "daily" if timeframe == "1d" else timeframe)]
    for c in cands:
        if os.path.isdir(c) and (glob.glob(os.path.join(c, "*.csv")) or glob.glob(os.path.join(c, "*.csv.gz"))):
            return os.path.abspath(c)
    have = sorted(d for d in glob.glob(os.path.join(ROOT, "data", "*")) + glob.glob(os.path.join(ROOT, "data", "hidden", "*"))
                  if os.path.isdir(d) and glob.glob(os.path.join(d, "*.csv*")))
    raise StrategyError(f"no data for timeframe {timeframe!r}: looked in {cands}.\nFolders with data: "
                        + (", ".join(os.path.relpath(h, ROOT) for h in have) or "none")
                        + f"\nFetch it with  scripts\\fetch_data.py --interval {timeframe}  "
                        f"or test with  scripts\\make_synthetic.py --intervals {timeframe}")


def load_data(data_dir):
    """Return ({symbol: DataFrame}, {index: DataFrame}). Columns: date, open..volume [, datetime] [, nifty_close]."""
    frames = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "*.csv")) + glob.glob(os.path.join(data_dir, "*.csv.gz"))):
        df = pd.read_csv(path)
        need = ["date", "open", "high", "low", "close", "volume"]
        if any(c not in df.columns for c in need):
            raise StrategyError(f"{path}: expected columns {need}, got {list(df.columns)}")
        df["date"] = pd.to_datetime(df["date"])
        key = "date"
        if "datetime" in df.columns:                       # intraday: one row per bar, date = trading day
            df["datetime"] = pd.to_datetime(df["datetime"]); key = "datetime"; need = ["datetime"] + need
        df = df[need].drop_duplicates(key).sort_values(key).reset_index(drop=True)
        frames[os.path.basename(path).split(".csv")[0]] = df
    if not frames:
        raise StrategyError(f"no CSV files in {data_dir}")
    index = {s: frames.pop(s) for s in list(frames) if s in INDEX_SYMBOLS}
    if "NIFTY" in index:   # optional helper column for regime filters; absent if NIFTY data is missing
        nifty = index["NIFTY"][[time_col(index["NIFTY"]), "close"]].rename(columns={"close": "nifty_close"})
        for s, df in frames.items():
            frames[s] = df.merge(nifty, on=time_col(df), how="left").ffill()
    return frames, index


def time_col(df):
    return "datetime" if "datetime" in df.columns else "date"


def bars_per_year(frames, timeframe):
    if timeframe == "1d":
        return TRADING_DAYS_PER_YEAR
    df = next(iter(frames.values()))
    return float(df.groupby("date").size().median()) * TRADING_DAYS_PER_YEAR


def read_eval_start(data_dir):
    """<data>/_EVAL_START.txt tells the engine to score only from that date (indicators still warm up)."""
    p = os.path.join(data_dir, "_EVAL_START.txt")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else None


# ----------------------------------------------------------------------------------- strategy I/O
def load_strategy(path):
    """Return (name, strategy function, timeframe)."""
    if not os.path.exists(path) and os.path.exists(os.path.join(ROOT, path)):
        path = os.path.join(ROOT, path)
    if not os.path.isfile(path):
        raise StrategyError(f"strategy file not found: {path}  (save ChatGPT's file under strategies\\ first)")
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(f"strategy_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        raise StrategyError(f"{path} failed to import:\n{traceback.format_exc(limit=-1)}")
    if not callable(getattr(mod, "strategy", None)):
        raise StrategyError(f"{path} must define  def strategy(df): ...  (see PROMPT.md)")
    tf = str(getattr(mod, "TIMEFRAME", "1d")).strip().lower()
    if tf not in TIMEFRAMES:
        raise StrategyError(f"{path}: TIMEFRAME = {tf!r} is not one of {TIMEFRAMES}")
    return name, mod.strategy, tf


def get_positions(strategy, df, long_only=False, check_lookahead=True, nan_count=None):
    """Call the strategy on a COPY of df, validate the result, return a clean float Series on df.index."""
    try:
        out = strategy(df.copy())
    except Exception:
        raise StrategyError(f"your strategy raised an exception:\n{traceback.format_exc(limit=-2)}")
    if isinstance(out, pd.DataFrame) and out.shape[1] == 1:
        out = out.iloc[:, 0]
    if isinstance(out, (list, np.ndarray)) and len(out) == len(df):
        out = pd.Series(np.asarray(out, dtype=float), index=df.index)
    if not isinstance(out, pd.Series):
        raise StrategyError(f"strategy must return a pandas Series, got {type(out).__name__}")
    if len(out) != len(df) or not out.index.equals(df.index):
        raise StrategyError("strategy returned a Series whose index differs from df.index "
                            "(did you drop rows, reindex, set_index('date') or use future indexes?)")
    pos = pd.to_numeric(out, errors="coerce").astype(float)
    if nan_count is not None:
        nan_count[0] += int(pos.isna().sum())
    pos = pos.fillna(0.0).clip(0.0 if long_only else -1.0, 1.0)
    if check_lookahead:   # causality test: removing future rows must not change past positions
        k = len(df) * 2 // 3
        try:
            early = get_positions(strategy, df.iloc[:k].copy(), long_only, check_lookahead=False)
        except StrategyError:
            early = None   # a strategy that needs > 2/3 of the data to run cannot be checked this way
        if early is not None and not np.allclose(early.to_numpy(), pos.iloc[:k].to_numpy(), atol=1e-9):
            raise StrategyError(
                "LOOK-AHEAD DETECTED: your positions for past bars changed when future rows were removed.\n"
                "Typical causes: shift(-1), rolling(center=True), bfill(), or whole-sample statistics like\n"
                "df['close'].mean()/max()/rank(). Use only rolling/expanding/shift(+n) of PAST rows.\n"
                "Intraday: detect the day's last bar from the clock (df['datetime'].dt.hour/minute), not shift(-1).\n"
                "(If you deliberately use randomness, set a seed. To bypass: --unsafe-skip-lookahead-check)")
    return pos


# ----------------------------------------------------------------------------------- simulation
def simulate(df, held, capital, cost_per_order=COST_PER_ORDER, slippage=SLIPPAGE):
    """One symbol. held[i] = position in force from open[i] to open[i+1]. Returns (equity at each open, trade PnLs)."""
    o = df["open"].to_numpy(float).tolist()          # plain floats: the loop is ~3x faster than numpy scalars
    h = held.to_numpy(float).tolist()
    n = len(o)
    equity = [0.0] * n
    eq, prev, entry_eq, trades = capital, 0.0, capital, []
    bust = False
    for i in range(n):
        hi = h[i]
        if bust:                                                   # sleeve wiped out: nothing left to trade
            equity[i] = 0.0
            continue
        if hi != prev:                                             # we trade at this bar's open
            closing = prev != 0.0 and (hi == 0.0 or (hi > 0) != (prev > 0))
            opening = hi != 0.0 and (prev == 0.0 or (hi > 0) != (prev > 0))
            eq -= cost_per_order + slippage * abs(hi - prev) * eq       # Rs 20 + 0.05% of traded value
            if closing:
                trades.append(eq - entry_eq)
            if opening:
                entry_eq = eq
        if eq <= 0.0:                                              # costs (or a short squeeze) ate the capital
            eq, bust = 0.0, True
            if hi != 0.0:
                trades.append(eq - entry_eq)
        equity[i] = eq
        if i + 1 < n:                                              # earn open[i] -> open[i+1] return
            eq *= 1.0 + hi * (o[i + 1] / o[i] - 1.0)
        prev = hi
    if prev != 0.0 and not bust:
        trades.append(eq - entry_eq)                               # still open at the end: mark to market
    return pd.Series(equity, index=df[time_col(df)].to_numpy()), trades, bust


def metrics(equity, bpy=TRADING_DAYS_PER_YEAR):
    """Annualised with `bpy` bars per year (246 for daily; bars-per-day x 246 intraday)."""
    r = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total = max(equity.iloc[-1] / equity.iloc[0] - 1.0, -1.0)   # -100% = everything lost (sleeves stop at 0)
    bars = max(len(equity) - 1, 1)
    sharpe = float(r.mean() / r.std() * math.sqrt(bpy)) if len(r) > 1 and r.std() > 0 else 0.0
    return {"total_return_pct": 100 * total, "cagr_pct": 100 * ((1 + total) ** (bpy / bars) - 1),
            "max_drawdown_pct": 100 * float((equity / equity.cummax() - 1.0).min()), "sharpe": sharpe}


def run_backtest(strategy, frames, name="strategy", long_only=False, start=None, end=None, capital=CAPITAL,
                 cost_per_order=COST_PER_ORDER, slippage=SLIPPAGE, check_lookahead=True, index_frames=None,
                 timeframe="1d"):
    """Run one strategy over every symbol. Returns dict(scorecard, equity, benchmark, per_symbol, nifty)."""
    per_sym, bpy = capital / len(frames), bars_per_year(frames, timeframe)
    curves, bench, rows, all_trades, nan_count, exposed, total_bars = {}, {}, [], [], [0], 0, 0
    first_symbol, busted = True, []
    for sym, df in frames.items():
        pos = get_positions(strategy, df, long_only, check_lookahead and first_symbol, nan_count)
        first_symbol = False
        held = pos.shift(1).fillna(0.0)                     # decided at close[i-1] -> in force from open[i]
        mask = pd.Series(True, index=df.index)
        if start: mask &= df["date"] >= pd.Timestamp(start)
        if end:   mask &= df["date"] <= pd.Timestamp(end)
        d, h = df[mask].reset_index(drop=True), held[mask].reset_index(drop=True)
        if len(d) < 2:
            raise StrategyError(f"{sym}: fewer than 2 rows in the window {start}..{end}")
        eq, trades, bust = simulate(d, h, per_sym, cost_per_order, slippage)
        bh, _, _ = simulate(d, pd.Series(1.0, index=d.index), per_sym, cost_per_order, slippage)
        curves[sym], bench[sym] = eq, bh
        all_trades += trades
        if bust:
            busted.append(sym)
        exposed += int((h != 0).sum()); total_bars += len(h)
        rows.append({"symbol": sym, "return_pct": 100 * (eq.iloc[-1] / per_sym - 1), "buy_hold_pct":
                     100 * (bh.iloc[-1] / per_sym - 1), "trades": len(trades),
                     "win_rate_pct": 100 * np.mean([t > 0 for t in trades]) if trades else float("nan"),
                     "max_drawdown_pct": metrics(eq, bpy)["max_drawdown_pct"]})
    if nan_count[0]:
        print(f"note: {nan_count[0]} NaN positions treated as flat (normal during indicator warm-up)")
    if busted:
        print(f"WARNING: {len(busted)} symbol sleeve(s) went to zero (costs/losses ate the capital): "
              f"{', '.join(busted[:6])}{'...' if len(busted) > 6 else ''}. Trade less.")
    equity = pd.DataFrame(curves).ffill().sum(axis=1).sort_index()
    benchmark = pd.DataFrame(bench).ffill().sum(axis=1).sort_index()
    per_symbol = pd.DataFrame(rows).sort_values("return_pct", ascending=False).reset_index(drop=True)
    sc = {"strategy": name, "timeframe": timeframe, **metrics(equity, bpy),
          "benchmark_return_pct": metrics(benchmark, bpy)["total_return_pct"], "trades": len(all_trades),
          "win_rate_pct": 100 * float(np.mean([t > 0 for t in all_trades])) if all_trades else 0.0,
          "exposure_pct": 100 * exposed / total_bars, "best_symbol": per_symbol.iloc[0]["symbol"],
          "worst_symbol": per_symbol.iloc[-1]["symbol"], "symbols": len(frames), "bars": len(equity),
          "busted_symbols": len(busted),
          "bars_per_year": bpy, "period": f"{equity.index[0]:%Y-%m-%d} to {equity.index[-1]:%Y-%m-%d}",
          "long_only": long_only}
    nifty = None
    if index_frames and "NIFTY" in index_frames:
        ix = index_frames["NIFTY"]
        n = ix.set_index(time_col(ix))["open"].reindex(equity.index).ffill().bfill()
        nifty = n / n.iloc[0] * equity.iloc[0]
    return {"scorecard": sc, "equity": equity, "benchmark": benchmark, "per_symbol": per_symbol, "nifty": nifty}


# ----------------------------------------------------------------------------------------- output
def submit_line(sc):
    return (f"SUBMIT | {sc['strategy']} | tf={sc['timeframe']} | sharpe={sc['sharpe']:.2f} | "
            f"cagr={sc['cagr_pct']:.1f}% | maxdd={sc['max_drawdown_pct']:.1f}% | trades={sc['trades']}")


def print_scorecard(sc):
    print(f"\n=== {sc['strategy']}  [{sc['timeframe']}]  ({sc['period']}, {sc['symbols']} symbols, {sc['bars']} bars"
          f"{', long-only' if sc['long_only'] else ''}) ===")
    rows = [("Total return", f"{sc['total_return_pct']:.2f}%"), ("CAGR", f"{sc['cagr_pct']:.2f}%"),
            ("Max drawdown", f"{sc['max_drawdown_pct']:.2f}%"),
            (f"Sharpe (annualised, {sc['bars_per_year']:.0f} bars/yr)", f"{sc['sharpe']:.2f}"),
            ("Win rate / trade", f"{sc['win_rate_pct']:.1f}%"), ("Trades", str(sc["trades"])),
            ("Exposure", f"{sc['exposure_pct']:.1f}%"), ("Buy & hold (same universe)", f"{sc['benchmark_return_pct']:.2f}%"),
            ("Best / worst symbol", f"{sc['best_symbol']} / {sc['worst_symbol']}")]
    for k, v in rows:
        print(f"  {k:<36}{v:>12}")
    print("\n" + submit_line(sc))


def save_outputs(res, results_dir, plot=True):
    os.makedirs(results_dir, exist_ok=True)
    sc = res["scorecard"]; name = sc["strategy"]
    csv_path = os.path.join(results_dir, f"{name}_symbols.csv")
    res["per_symbol"].round(2).to_csv(csv_path, index=False)
    png_path = None
    if plot:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        png_path = os.path.join(results_dir, f"{name}.png")
        eq, bh = res["equity"] / res["equity"].iloc[0], res["benchmark"] / res["benchmark"].iloc[0]
        fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), dpi=120, sharex=True, facecolor="#fcfcfb",
                                      gridspec_kw={"height_ratios": [3, 1]})
        series = [(eq, "#2a78d6", name, "-"), (bh, "#eb6834", "buy & hold (equal-weight)", "-")]
        if res["nifty"] is not None:
            series.append((res["nifty"] / res["nifty"].iloc[0], "#1baf7a", "NIFTY (index, reference)", ":"))
        for s, c, lbl, ls in series:
            ax.plot(s.index, s.values, color=c, lw=2, ls=ls, label=lbl)
            ax.annotate(f" {lbl.split(' (')[0]} {100 * (s.iloc[-1] - 1):+.1f}%", (s.index[-1], s.iloc[-1]),
                        color="#0b0b0b", fontsize=8, va="center")
        dd = 100 * (res["equity"] / res["equity"].cummax() - 1)
        ax2.fill_between(dd.index, dd.values, 0, color="#2a78d6", alpha=0.25, lw=0)
        ax2.plot(dd.index, dd.values, color="#2a78d6", lw=1)
        ax.set_title(f"{name} [{sc['timeframe']}]: growth of Rs 1  |  Sharpe {sc['sharpe']:.2f}  CAGR {sc['cagr_pct']:.1f}%  "
                     f"MaxDD {sc['max_drawdown_pct']:.1f}%  trades {sc['trades']}", fontsize=11, loc="left")
        ax.legend(frameon=False, fontsize=9, loc="upper left"); ax2.set_ylabel("drawdown %", fontsize=9)
        for a in (ax, ax2):
            a.set_facecolor("#fcfcfb"); a.grid(color="#e1e0d9", lw=0.8); a.tick_params(colors="#52514e", labelsize=8)
            for sp in a.spines.values(): sp.set_color("#c3c2b7")
        fig.tight_layout(); fig.savefig(png_path, facecolor="#fcfcfb"); plt.close(fig)
    return png_path, csv_path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("strategy", nargs="?", help="path to a strategy .py file (defines strategy(df))")
    p.add_argument("--all", action="store_true", help="run every strategies/**/*.py and print a leaderboard")
    p.add_argument("--data", default=None, help="data folder; default data/daily or data/<TIMEFRAME>. "
                   "With data/hidden the engine picks data/hidden/<TIMEFRAME> automatically")
    p.add_argument("--long-only", action="store_true", help="clip positions to [0, 1] (no shorts)")
    p.add_argument("--start"); p.add_argument("--end", help="score only inside this window (YYYY-MM-DD)")
    p.add_argument("--capital", type=float, default=CAPITAL)
    p.add_argument("--no-plot", action="store_true"); p.add_argument("--json", action="store_true")
    p.add_argument("--unsafe-skip-lookahead-check", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if not a.strategy and not a.all:
        p.error("give a strategy file or --all")
    cache, board = {}, []
    try:
        files = [a.strategy] if a.strategy else sorted(glob.glob(os.path.join(ROOT, "strategies", "**", "*.py"), recursive=True))
        for f in files:
            if os.path.basename(f).startswith("_"):
                continue
            try:
                name, strat, tf = load_strategy(f)
                folder = data_dir_for(tf, a.data)
                if folder not in cache:                              # load each timeframe's data once
                    cache[folder] = load_data(folder)
                frames, index_frames = cache[folder]
                start = a.start or read_eval_start(folder)
                if start and not a.start:
                    print(f"scoring from {start} ({os.path.relpath(folder, ROOT)}/_EVAL_START.txt); earlier bars only warm up indicators")
                res = run_backtest(strat, frames, name, a.long_only, start, a.end, a.capital, timeframe=tf,
                                   check_lookahead=not a.unsafe_skip_lookahead_check, index_frames=index_frames)
                png, csv = save_outputs(res, os.path.join(ROOT, "results"), plot=not a.no_plot)
            except StrategyError as e:
                if not a.all:
                    raise
                print(f"\n{os.path.basename(f)}: FAILED - {str(e).splitlines()[0]}"); continue
            sc = res["scorecard"]
            if a.json:
                print(json.dumps({**sc, "data": folder, "png": png, "per_symbol_csv": csv}, indent=2, default=str))
            else:
                print_scorecard(sc); print(f"data: {os.path.relpath(folder, ROOT)}   saved: {png or '(no plot)'}  {csv}")
            board.append(sc)
        if a.all and board:
            print("\n=== LEADERBOARD (sorted by Sharpe) ===")
            print(f"  {'strategy':<24}{'tf':>5}{'sharpe':>8}{'cagr%':>9}{'maxdd%':>9}{'trades':>8}{'win%':>7}")
            for sc in sorted(board, key=lambda s: s["sharpe"], reverse=True):
                print(f"  {sc['strategy']:<24}{sc['timeframe']:>5}{sc['sharpe']:>8.2f}{sc['cagr_pct']:>9.1f}"
                      f"{sc['max_drawdown_pct']:>9.1f}{sc['trades']:>8}{sc['win_rate_pct']:>7.1f}")
    except StrategyError as e:
        sys.exit(f"\nERROR: {e}")


if __name__ == "__main__":
    warnings.simplefilter("ignore", FutureWarning)
    main()
