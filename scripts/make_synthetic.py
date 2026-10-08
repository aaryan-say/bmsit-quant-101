"""Generate a clearly labelled SYNTHETIC dataset so the whole pipeline can be tested without a login.

    python scripts\\make_synthetic.py            # writes data/daily (public) and data/hidden (presenter)

Prices are geometric random walks with a shared "market factor" (so stocks co-move like a real index
basket) plus stock-specific noise. Same file names and schema as fetch_data.py so swapping in real
Nubra PROD data later needs no other change. NOT real prices: data/daily/_SOURCE.txt says SYNTHETIC.
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe import (APPROX_PRICE, HIDDEN_END, HIDDEN_START, INDEX_SYMBOL, NIFTY50,  # noqa: E402
                      PUBLIC_END, PUBLIC_START)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = 20260630

# Approximate NSE holidays in the window (weekday ones only). Exactness does not matter for synthetic data.
HOLIDAYS = ["2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05",
            "2025-12-25", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
            "2026-04-14", "2026-05-01", "2026-09-14"]


def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    days = pd.bdate_range(start, end)
    return days[~days.isin(pd.to_datetime(HOLIDAYS))]


def make_ohlcv(rng: np.random.Generator, dates: pd.DatetimeIndex, start_price: float,
               market_ret: np.ndarray, beta: float, idio_vol: float, drift: float) -> pd.DataFrame:
    """Daily OHLCV from a factor model: r = drift + beta*market + idiosyncratic noise."""
    n = len(dates)
    daily = drift / 252 + beta * market_ret + rng.normal(0, idio_vol / np.sqrt(252), n)
    close = start_price * np.exp(np.cumsum(daily))
    prev_close = np.concatenate([[start_price], close[:-1]])
    gap = rng.normal(0, 0.004, n)                       # overnight gap noise
    open_ = prev_close * (1 + gap)
    intra = np.abs(rng.normal(0, 0.006, n))             # extra intraday range beyond open->close
    high = np.maximum(open_, close) * (1 + intra)
    low = np.minimum(open_, close) * (1 - intra)
    base_vol = 2.5e9 / start_price                       # cheaper stocks trade more shares
    volume = (base_vol * np.exp(rng.normal(0, 0.45, n)) * (1 + 8 * np.abs(daily))).astype("int64")
    tick = lambda a: np.round(np.round(a / 0.05) * 0.05, 2)  # NSE tick size 0.05
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "open": tick(open_), "high": tick(high),
                         "low": tick(low), "close": tick(close), "volume": volume})


def write_set(frames: dict, folder: str, start: str, end: str, label: str) -> None:
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):                          # remove stale files from older runs
        if f.endswith(".csv") or f.startswith("_"):
            os.remove(os.path.join(folder, f))
    for sym, df in frames.items():
        part = df[(df["date"] >= start) & (df["date"] <= end)]
        part.to_csv(os.path.join(folder, f"{sym}.csv"), index=False)
    with open(os.path.join(folder, "_SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"SYNTHETIC\ngenerated: {datetime.now():%Y-%m-%d %H:%M}\nby: scripts/make_synthetic.py\n"
                f"range: {start} to {end}\nsymbols: {len(frames)}\n{label}\n"
                "NOT real prices. Geometric random walks for pipeline testing only.\n")


def main() -> None:
    rng = np.random.default_rng(SEED)
    dates = trading_days(PUBLIC_START, HIDDEN_END)
    n = len(dates)
    market = rng.normal(0.08 / 252, 0.14 / np.sqrt(252), n)     # index-like factor: ~8% drift, 14% vol
    frames = {}
    nifty = make_ohlcv(rng, dates, APPROX_PRICE[INDEX_SYMBOL], market, 1.0, 0.01, 0.0)
    nifty["volume"] = 0                                          # index has no traded volume
    frames[INDEX_SYMBOL] = nifty
    for cands in NIFTY50:
        sym = cands[0]
        beta = rng.uniform(0.6, 1.4)
        idio_vol = rng.uniform(0.16, 0.38)                       # stock-specific annual vol
        drift = rng.normal(0.0, 0.10)                            # some trend up, some down
        frames[sym] = make_ohlcv(rng, dates, APPROX_PRICE.get(sym, 1000), market, beta, idio_vol, drift)
    daily_dir = os.path.join(ROOT, "data", "daily")
    hidden_dir = os.path.join(ROOT, "data", "hidden")
    write_set(frames, daily_dir, PUBLIC_START, PUBLIC_END, "public set (students)")
    # The hidden folder holds public + out-of-sample rows so indicators have warm-up; the engine
    # reads _EVAL_START.txt and only scores from that date.
    write_set(frames, hidden_dir, PUBLIC_START, HIDDEN_END, f"presenter set; judged from {HIDDEN_START}")
    with open(os.path.join(hidden_dir, "_EVAL_START.txt"), "w", encoding="utf-8") as f:
        f.write(HIDDEN_START + "\n")
    pub_rows = len(dates[(dates >= PUBLIC_START) & (dates <= PUBLIC_END)])
    print(f"SYNTHETIC data written: {len(frames)} symbols ({len(frames) - 1} stocks + {INDEX_SYMBOL})")
    print(f"  {daily_dir}: {pub_rows} trading days {PUBLIC_START}..{PUBLIC_END}")
    print(f"  {hidden_dir}: {n} trading days {PUBLIC_START}..{HIDDEN_END} (scored from {HIDDEN_START})")


if __name__ == "__main__":
    main()
