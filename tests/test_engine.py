"""Engine tests. Run either way:

    python -m pytest tests            (if pytest is installed)
    python tests\\test_engine.py       (plain Python, no pytest needed)

What they prove:
  1. the example strategies run and produce a finite scorecard
  2. NEXT-BAR execution: a strategy that turns long on the bar of a known jump does NOT earn that jump
  3. costs reduce returns (same trades, lower equity)
  4. an obvious look-ahead strategy (shift(-1)) is rejected; if you force it through, its Sharpe is absurd
     -> this is exactly why the competition is judged on hidden July-September data
  5. guards: index mismatch rejected, NaN -> flat, |position| clipped to 1, df mutation harmless, --long-only
  6. timeframes: TIMEFRAME in the strategy file picks data/<tf> (data/hidden/<tf> for the hidden re-run),
     a missing folder gives a plain error, and Sharpe/CAGR are annualised by bars per year (246 x bars/day)
Intraday tests use whatever data/15m holds (synthetic from scripts\\make_synthetic.py or real) and are
skipped with a message if that folder is absent.
"""
import math
import os
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backtest as bt  # noqa: E402

_CACHE = {}


def data(tf="1d"):
    if tf not in _CACHE:
        _CACHE[tf], _ = bt.load_data(bt.data_dir_for(tf))
    return _CACHE[tf]


def has_tf(tf):
    try:
        bt.data_dir_for(tf)
        return True
    except bt.StrategyError:
        print(f"  (skipped: no data/{tf}; run scripts\\make_synthetic.py --intervals {tf})")
        return False


def jump_frame(n=60, jump_day=30, jump=0.20):
    """Flat price 100, then on `jump_day` the close jumps +20% (open still 100); flat at 120 afterwards."""
    dates = pd.bdate_range("2026-01-01", periods=n)
    px = np.where(np.arange(n) >= jump_day, 100 * (1 + jump), 100.0)
    open_ = px.copy()
    open_[jump_day] = 100.0           # the jump happens DURING jump_day (open 100 -> close 120)
    return pd.DataFrame({"date": dates, "open": open_, "high": px, "low": open_, "close": px, "volume": 1000})


def run(strategy, frames, **kw):
    return bt.run_backtest(strategy, frames, name="t", check_lookahead=False, **kw)["scorecard"]


def example(name):
    return bt.load_strategy(os.path.join(ROOT, "strategies", name + ".py"))


# ------------------------------------------------------------------ 1. examples run
def test_examples_run():
    for f in ("ma_crossover", "zscore_meanrev", "breakout"):
        name, strat, tf = example(f)
        assert tf == "1d"
        sc = bt.run_backtest(strat, data(), name)["scorecard"]
        assert sc["trades"] > 10 and math.isfinite(sc["sharpe"]) and -100 < sc["max_drawdown_pct"] <= 0
        assert "SUBMIT |" in bt.submit_line(sc) and "tf=1d" in bt.submit_line(sc)


# ------------------------------------------------------------------ 2. next-bar execution
def test_next_bar_execution_does_not_capture_signal_bar_jump():
    df = jump_frame()
    long_from_jump_day = lambda d: pd.Series((np.arange(len(d)) >= 30).astype(float), index=d.index)
    long_from_day_before = lambda d: pd.Series((np.arange(len(d)) >= 29).astype(float), index=d.index)
    sc_late = run(long_from_jump_day, {"X": df}, cost_per_order=0, slippage=0)
    sc_early = run(long_from_day_before, {"X": df}, cost_per_order=0, slippage=0)
    # signal on the jump day is executed at the NEXT open (already 120) -> no profit from the jump
    assert abs(sc_late["total_return_pct"]) < 1e-9, sc_late
    # a signal the day BEFORE is executed at the jump day's open (100) -> earns the +20%
    assert abs(sc_early["total_return_pct"] - 20.0) < 1e-6, sc_early


# ------------------------------------------------------------------ 3. costs
def test_costs_reduce_returns():
    _, strat, _ = example("ma_crossover")
    gross = run(strat, data(), cost_per_order=0, slippage=0)
    net = run(strat, data())
    assert net["trades"] == gross["trades"]
    assert net["total_return_pct"] < gross["total_return_pct"] - 0.5     # hundreds of trades cost real money


# ------------------------------------------------------------------ 4. look-ahead
def lookahead_strategy(df):
    return np.sign(df["close"].shift(-1) - df["close"])         # "buy if the next bar is up": cheating


def test_lookahead_is_rejected():
    try:
        bt.run_backtest(lookahead_strategy, data(), "cheat")
    except bt.StrategyError as e:
        assert "LOOK-AHEAD" in str(e)
    else:
        raise AssertionError("look-ahead strategy was not rejected")


def test_lookahead_scores_suspiciously_high_if_forced():
    cheat = run(lookahead_strategy, data())
    _, strat, _ = example("zscore_meanrev")
    honest = run(strat, data())
    # Even with next-day execution and costs, knowing tomorrow's close is a licence to print money.
    assert cheat["sharpe"] > 5 and cheat["sharpe"] > 3 * abs(honest["sharpe"]), (cheat, honest)


# ------------------------------------------------------------------ 5. guards
def test_index_mismatch_rejected():
    bad = lambda d: pd.Series(1.0, index=d.index[1:])
    try:
        bt.get_positions(bad, data()["RELIANCE"])
    except bt.StrategyError as e:
        assert "index" in str(e)
    else:
        raise AssertionError("mismatched index accepted")


def test_nan_is_flat_and_positions_clipped():
    df = data()["RELIANCE"]
    pos = bt.get_positions(lambda d: pd.Series([np.nan, 5.0, -7.0] + [0.5] * (len(d) - 3), index=d.index), df)
    assert list(pos.iloc[:3]) == [0.0, 1.0, -1.0] and pos.iloc[3] == 0.5
    pos = bt.get_positions(lambda d: pd.Series(-1.0, index=d.index), df, long_only=True)
    assert (pos == 0).all()


def test_mutating_df_is_harmless():
    frames = data()
    before = frames["RELIANCE"]["close"].copy()

    def vandal(d):
        d["close"] = 0.0
        d.drop(columns=["open"], inplace=True)
        return pd.Series(0.0, index=d.index)

    run(vandal, frames)
    assert frames["RELIANCE"]["close"].equals(before)


def test_long_only_flag():
    _, strat, _ = example("ma_crossover")
    both, lo = run(strat, data()), run(strat, data(), long_only=True)
    assert lo["exposure_pct"] < both["exposure_pct"] and lo["trades"] < both["trades"]


# ------------------------------------------------------------------ 6. timeframes
def test_timeframe_declared_in_file_and_folder_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "tf_test.py")
        with open(p, "w") as f:
            f.write("import pandas as pd\nTIMEFRAME = '15m'\n"
                    "def strategy(df):\n    return pd.Series(0.0, index=df.index)\n")
        name, _, tf = bt.load_strategy(p)
        assert (name, tf) == ("tf_test", "15m")
        with open(p, "w") as f:
            f.write("import pandas as pd\nTIMEFRAME = '2h'\ndef strategy(df): return df['close']*0\n")
        try:
            bt.load_strategy(p)
        except bt.StrategyError as e:
            assert "TIMEFRAME" in str(e)
        else:
            raise AssertionError("bad TIMEFRAME accepted")
    assert bt.data_dir_for("1d").endswith("daily")
    # a missing timeframe folder gives a plain message listing what exists
    with tempfile.TemporaryDirectory() as empty:
        try:
            bt.data_dir_for("5m", empty)
        except bt.StrategyError as e:
            assert "no data for timeframe '5m'" in str(e) and "Folders with data" in str(e)
        else:
            raise AssertionError("missing folder accepted")
    if has_tf("15m"):
        assert bt.data_dir_for("15m").endswith("15m")
        hidden = os.path.join(ROOT, "data", "hidden")
        if os.path.isdir(os.path.join(hidden, "15m")):
            assert bt.data_dir_for("15m", hidden).endswith(os.path.join("hidden", "15m"))


def test_bars_per_year_and_annualisation():
    assert bt.bars_per_year(data(), "1d") == 246
    # synthetic intraday frame: 25 bars per day -> 6150 bars/year; Sharpe scales with sqrt(bars/year)
    dts = pd.date_range("2026-01-05 09:15", periods=25 * 40, freq="15min")
    dts = dts[dts.indexer_between_time("09:15", "15:15")][:25 * 40]
    rng = np.random.default_rng(0)
    px = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.001, len(dts))))
    df = pd.DataFrame({"datetime": dts, "date": dts.normalize(), "open": px, "high": px, "low": px,
                       "close": px, "volume": 1})
    assert bt.bars_per_year({"X": df}, "15m") == 25 * 246
    always_long = lambda d: pd.Series(1.0, index=d.index)
    sc15 = run(always_long, {"X": df}, timeframe="15m", cost_per_order=0, slippage=0)
    sc1d = run(always_long, {"X": df}, timeframe="1d", cost_per_order=0, slippage=0)   # same bars, daily factor
    assert abs(sc15["sharpe"] / sc1d["sharpe"] - math.sqrt(25)) < 1e-9
    assert sc15["bars_per_year"] == 6150 and "tf=15m" in bt.submit_line(sc15)


def test_intraday_example_runs():
    if not has_tf("15m"):
        return
    name, strat, tf = example("first_green_15m")
    assert tf == "15m"
    frames = data("15m")
    df = next(iter(frames.values()))
    assert "datetime" in df.columns and df["date"].min() >= pd.Timestamp("2025-09-04")
    sc = bt.run_backtest(strat, frames, name, timeframe=tf)["scorecard"]
    assert sc["trades"] > 10 and sc["bars_per_year"] > 246 and math.isfinite(sc["sharpe"])
    # the strategy is flat at the day's last bar: no position is ever held across a 15:15 bar
    pos = bt.get_positions(strat, df, check_lookahead=True)
    assert (pos[df["datetime"].dt.hour * 60 + df["datetime"].dt.minute >= 15 * 60] == 0).all()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:300]}")
    print("\nall tests passed" if not failed else f"\n{failed} test(s) FAILED")
    sys.exit(1 if failed else 0)
