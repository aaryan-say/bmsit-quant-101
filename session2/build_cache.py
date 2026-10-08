"""
build_cache.py  -  fetch daily candles for the screener watchlist and save them as CSVs
                   in session2/cache/  (one file per symbol, prices in RUPEES, dated).

Presenter runs this the night before:
    python build_cache.py                 # logs in to PROD (data only), fetches ~100 calendar days, saves CSVs
    python build_cache.py --uat           # same from the UAT sandbox (very little history there; not recommended)
Anyone can make fake practice data (clearly labelled) without an account:
    python build_cache.py --synthetic

Output:
    cache/RELIANCE.csv ... one per symbol: date,open,high,low,close,volume
    cache/_meta.json       when it was built, from where (NUBRA-PROD, NUBRA-UAT or SYNTHETIC), which symbols

The screener (--offline) and the dashboard read these files.
"""
import argparse
import json
import os
import random
from datetime import datetime, timedelta

import pandas as pd

import screener                                     # reuse the fetch code, keep one source of truth

CACHE_DIR = screener.CACHE_DIR


def save_frames(frames: dict, source: str, notes: str = ""):
    os.makedirs(CACHE_DIR, exist_ok=True)
    for symbol, df in frames.items():
        path = os.path.join(CACHE_DIR, f"{symbol}.csv")
        df.reset_index().to_csv(path, index=False)
        print(f"  saved {os.path.relpath(path, screener.HERE)}  ({len(df)} rows, "
              f"{df.index.min().date()} .. {df.index.max().date()})")
    meta = {"source": source, "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "symbols": sorted(frames), "notes": notes}
    with open(screener.META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  wrote _meta.json (source={source})")


def synthetic_frames(symbols=screener.WATCHLIST, days=70, seed=7):
    """
    Random-walk daily candles. Purely for practising the maths; prices are NOT real.
    We deliberately bend two stocks far from their mean so the screener has something to find.
    """
    rng = random.Random(seed)
    base = {s: rng.uniform(200, 3000) for s in symbols}
    dates = pd.bdate_range(end=datetime.today().date(), periods=days)      # weekdays only
    frames = {}
    for i, s in enumerate(symbols):
        price, rows = base[s], []
        drift = -0.035 if i == 0 else (0.035 if i == 1 else 0.0)            # stock 0 sinks, stock 1 rises
        for d in dates:
            o = price
            c = o * (1 + rng.gauss(drift if d >= dates[-5] else 0.0, 0.010))   # last 5 days bend hard
            hi, lo = max(o, c) * (1 + rng.uniform(0, 0.005)), min(o, c) * (1 - rng.uniform(0, 0.005))
            rows.append({"date": d, "open": round(o, 2), "high": round(hi, 2), "low": round(lo, 2),
                         "close": round(c, 2), "volume": rng.randint(500_000, 5_000_000)})
            price = c
        frames[s] = pd.DataFrame(rows).set_index("date")
    return frames


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="write random practice data instead of logging in")
    ap.add_argument("--uat", action="store_true", help="pull from the UAT sandbox instead of PROD (PROD is the default: real, complete history)")
    a = ap.parse_args()

    if a.synthetic:
        print("Writing SYNTHETIC practice data (random numbers, not real prices)...")
        save_frames(synthetic_frames(), "SYNTHETIC", "random walk for practice; run build_cache.py for real data")
        return

    if a.uat:
        nubra = screener.checkpoint_1_login_uat()
        source = "NUBRA-UAT"
    else:
        # Market DATA always comes from PROD (complete history). Orders stay in UAT.
        nubra = screener.checkpoint_1_login()       # common_login.get_prod_client(): ../data session or session2/prod/
        source = "NUBRA-PROD"
    frames = screener.fetch_watchlist_live(nubra)
    if not frames:
        raise SystemExit("no candles returned; nothing saved")
    save_frames(frames, source, "daily candles via historical_data(), interval 1d, intraDay False, paise/100")
    print("Done. Now try:  python screener.py --offline")


if __name__ == "__main__":
    main()
