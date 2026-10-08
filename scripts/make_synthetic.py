"""Generate a clearly labelled SYNTHETIC dataset so the whole pipeline can be tested without a login.

    python scripts\\make_synthetic.py                       # every timeframe that is not already real data
    python scripts\\make_synthetic.py --intervals 15m,1h    # only these
    python scripts\\make_synthetic.py --force               # overwrite even folders marked NUBRA-PROD

Daily prices are geometric random walks with a shared "market factor" (so stocks co-move like a real
index basket). Intraday bars are random bridges between each day's open and close, so they are
consistent with the daily file; if REAL daily data (data/hidden or data/daily from fetch_data.py) is
present, the intraday bridges are built on those real opens/closes. Same file names and schema as
fetch_data.py so swapping in real Nubra PROD data later needs no other change.
NOT real prices: every generated folder gets a _SOURCE.txt saying SYNTHETIC. A folder whose
_SOURCE.txt says NUBRA-PROD is never overwritten unless --force.
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import (APPROX_PRICE, BAR_MINUTES, HIDDEN_END, HIDDEN_START, INDEX_SYMBOL,  # noqa: E402
                      INTERVALS, INTRADAY_START, NIFTY50, PUBLIC_END, PUBLIC_START, SESSION_MINUTES,
                      SESSION_OPEN_MIN, data_folders)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = 20260630

# Approximate NSE holidays in the window (weekday ones only). Exactness does not matter for synthetic data.
HOLIDAYS = ["2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05",
            "2025-12-25", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
            "2026-04-14", "2026-05-01", "2026-09-14"]


def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    days = pd.bdate_range(start, end)
    return days[~days.isin(pd.to_datetime(HOLIDAYS))]


def tick(a):
    return np.round(np.round(np.asarray(a) / 0.05) * 0.05, 2)   # NSE tick size 0.05


# ----------------------------------------------------------------------------- daily
def make_daily(rng, dates, start_price, market_ret, beta, idio_vol, drift) -> pd.DataFrame:
    """Daily OHLCV from a factor model: r = drift + beta*market + idiosyncratic noise."""
    n = len(dates)
    daily = drift / 252 + beta * market_ret + rng.normal(0, idio_vol / np.sqrt(252), n)
    close = start_price * np.exp(np.cumsum(daily))
    prev_close = np.concatenate([[start_price], close[:-1]])
    open_ = prev_close * (1 + rng.normal(0, 0.004, n))                 # overnight gap noise
    intra = np.abs(rng.normal(0, 0.006, n))                            # extra intraday range
    high = np.maximum(open_, close) * (1 + intra)
    low = np.minimum(open_, close) * (1 - intra)
    base_vol = 2.5e9 / start_price                                      # cheaper stocks trade more shares
    volume = (base_vol * np.exp(rng.normal(0, 0.45, n)) * (1 + 8 * np.abs(daily))).astype("int64")
    return pd.DataFrame({"date": dates, "open": tick(open_), "high": tick(high), "low": tick(low),
                         "close": tick(close), "volume": volume})


def load_real_daily(sym):
    """Prefer real daily data (full range in data/hidden, else data/daily) as the base for intraday bars."""
    for folder in (os.path.join(ROOT, "data", "hidden"), os.path.join(ROOT, "data", "daily")):
        p, src = os.path.join(folder, f"{sym}.csv"), os.path.join(folder, "_SOURCE.txt")
        if os.path.exists(p) and os.path.exists(src) and open(src, encoding="utf-8").read().startswith("NUBRA-PROD"):
            df = pd.read_csv(p, parse_dates=["date"])
            return df if len(df) else None
    return None


# ----------------------------------------------------------------------------- intraday
def make_minute_bars(rng, daily: pd.DataFrame) -> pd.DataFrame:
    """375 one-minute bars per day: a Brownian bridge from the day's open to its close (consistent with daily)."""
    d = daily[daily["date"] >= pd.Timestamp(INTRADAY_START)].reset_index(drop=True)
    n_days, n = len(d), SESSION_MINUTES
    o, c = d["open"].to_numpy(float)[:, None], d["close"].to_numpy(float)[:, None]
    k = np.arange(1, n + 1)[None, :] / n
    noise = np.cumsum(rng.normal(0, 1, (n_days, n)), axis=1)
    bridge = noise - k * noise[:, -1:]                                  # starts and ends at 0
    scale = 0.004 * (o + c) / 2 / np.sqrt(n) * 2.5                      # ~1% intraday wiggle
    closes = o + (c - o) * k + scale * bridge
    opens = np.concatenate([o, closes[:, :-1]], axis=1)
    wick = np.abs(rng.normal(0, 0.0004, (n_days, n)))
    highs = np.maximum(opens, closes) * (1 + wick)
    lows = np.minimum(opens, closes) * (1 - wick)
    u = np.linspace(-1, 1, n)[None, :]
    w = (1 + 3 * u ** 2) * np.exp(rng.normal(0, 0.5, (n_days, n)))      # U-shaped volume profile
    vols = np.floor(w / w.sum(axis=1, keepdims=True) * d["volume"].to_numpy(float)[:, None]).astype("int64")
    minutes = SESSION_OPEN_MIN + np.arange(n)
    dt = (np.repeat(d["date"].to_numpy(), n) + np.tile(pd.to_timedelta(minutes, unit="m").to_numpy(), n_days))
    return pd.DataFrame({"datetime": dt, "date": np.repeat(d["date"].to_numpy(), n), "open": tick(opens.ravel()),
                         "high": tick(highs.ravel()), "low": tick(lows.ravel()), "close": tick(closes.ravel()),
                         "volume": vols.ravel(), "_minute": np.tile(np.arange(n), n_days)})


def aggregate(m1: pd.DataFrame, step: int) -> pd.DataFrame:
    """Aggregate 1-minute bars into `step`-minute bars (the last bar of the day may be shorter)."""
    if step == 1:
        return m1.drop(columns="_minute")
    bar = (m1["_minute"] // step).clip(upper=(SESSION_MINUTES - 1) // step)
    g = m1.groupby([m1["date"], bar], sort=True)
    out = g.agg(datetime=("datetime", "first"), open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index()
    return out[["datetime", "date", "open", "high", "low", "close", "volume"]]


# ----------------------------------------------------------------------------- write
def is_real(folder):
    src = os.path.join(folder, "_SOURCE.txt")
    return os.path.exists(src) and open(src, encoding="utf-8").read().startswith("NUBRA-PROD")


def write_set(frames: dict, folder: str, ext: str, start: str, end: str, label: str, interval: str) -> int:
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):                                        # remove stale files (not subfolders)
        if f.endswith(".csv") or f.endswith(".csv.gz") or f.startswith("_"):
            os.remove(os.path.join(folder, f))
    size = 0
    for sym, df in frames.items():
        part = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))].copy()
        part["date"] = part["date"].dt.strftime("%Y-%m-%d")
        if "datetime" in part:
            part["datetime"] = part["datetime"].dt.strftime("%Y-%m-%d %H:%M")
        p = os.path.join(folder, f"{sym}{ext}")
        part.to_csv(p, index=False, compression="gzip" if ext.endswith(".gz") else None)
        size += os.path.getsize(p)
    with open(os.path.join(folder, "_SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"SYNTHETIC\ngenerated: {datetime.now():%Y-%m-%d %H:%M}\nby: scripts/make_synthetic.py\n"
                f"interval: {interval}\nrange: {start} to {end}\nsymbols: {len(frames)}\n{label}\n"
                "NOT real prices. Random walks for pipeline testing only.\n")
    return size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--intervals", default=",".join(INTERVALS), help="comma list, e.g. 1d,15m (default: all)")
    ap.add_argument("--force", action="store_true", help="overwrite folders marked NUBRA-PROD too")
    a = ap.parse_args()
    wanted = [i.strip() for i in a.intervals.split(",") if i.strip()]
    bad = [i for i in wanted if i not in INTERVALS]
    if bad:
        sys.exit(f"unknown interval(s) {bad}; choose from {INTERVALS}")

    rng = np.random.default_rng(SEED)
    dates = trading_days(PUBLIC_START, HIDDEN_END)
    market = rng.normal(0.08 / 252, 0.14 / np.sqrt(252), len(dates))   # index-like factor
    daily = {}
    nifty = make_daily(rng, dates, APPROX_PRICE[INDEX_SYMBOL], market, 1.0, 0.01, 0.0)
    nifty["volume"] = 0
    daily[INDEX_SYMBOL] = nifty
    for cands in NIFTY50:
        sym = cands[0]
        daily[sym] = make_daily(rng, dates, APPROX_PRICE.get(sym, 1000), market, rng.uniform(0.6, 1.4),
                                rng.uniform(0.16, 0.38), rng.normal(0.0, 0.10))

    if "1d" in wanted:
        pub, hid, ext = data_folders("1d", ROOT)
        if (is_real(pub) or is_real(hid)) and not a.force:
            print("1d: data/daily or data/hidden hold REAL (NUBRA-PROD) data -> skipped (use --force to overwrite)")
        else:
            write_set(daily, pub, ext, PUBLIC_START, PUBLIC_END, "public set (students)", "1d")
            write_set(daily, hid, ext, PUBLIC_START, HIDDEN_END, f"presenter set; judged from {HIDDEN_START}", "1d")
            open(os.path.join(hid, "_EVAL_START.txt"), "w").write(HIDDEN_START + "\n")
            print(f"1d: SYNTHETIC written to {pub} and {hid} ({len(daily)} symbols, {len(dates)} days)")

    intraday = [i for i in wanted if i != "1d"]
    if not intraday:
        return
    # Base for intraday bars: real daily data if available (so bars tie to real opens/closes), else synthetic.
    base, real_n = {}, 0
    for sym in daily:
        r = load_real_daily(sym)
        base[sym] = r if r is not None else daily[sym]
        real_n += r is not None
    if real_n:   # keep the intraday universe identical to the real daily one (drop symbols that did not resolve)
        dropped = [s for s in base if load_real_daily(s) is None]
        base = {s: df for s, df in base.items() if s not in dropped}
        print(f"intraday base: {real_n} symbols on REAL daily opens/closes; dropped (not in real data): {dropped or 'none'}")
    else:
        print(f"intraday base: {len(base)} symbols on synthetic daily")
    minute = {sym: make_minute_bars(rng, df) for sym, df in base.items()}
    for iv in intraday:
        pub, hid, ext = data_folders(iv, ROOT)
        if (is_real(pub) or is_real(hid)) and not a.force:
            print(f"{iv}: {pub} or {hid} hold REAL data -> skipped (use --force)")
            continue
        frames = {sym: aggregate(m1, BAR_MINUTES[iv]) for sym, m1 in minute.items()}
        s1 = write_set(frames, pub, ext, INTRADAY_START, PUBLIC_END, "public set (students)", iv)
        s2 = write_set(frames, hid, ext, INTRADAY_START, HIDDEN_END, f"presenter set; judged from {HIDDEN_START}", iv)
        open(os.path.join(hid, "_EVAL_START.txt"), "w").write(HIDDEN_START + "\n")
        rows = len(frames[INDEX_SYMBOL])
        print(f"{iv}: SYNTHETIC {len(frames)} symbols x {rows} bars -> {pub} ({s1 / 1e6:.1f} MB), "
              f"{hid} ({s2 / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
