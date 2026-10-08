"""
clean_data.py - make the intraday files consistent with the (adjusted) daily series.

Two problems seen in the raw intraday feed (Nubra PROD, Oct 2026):
  1. Spike bars: a single bar whose close is >40% away from BOTH neighbours (e.g. MARUTI at Rs 5.50
     on 2025-11-06 10:00; many symbols on 2026-01-05). The bar is dropped. In aggregated timeframes
     the same tick can survive only as a crazy high/low, so highs/lows are clamped to a sane band
     around open/close.
  2. Corporate actions: the daily series is adjusted (no jump on KOTAKBANK's split day) but the
     intraday series is not. Every intraday day is rescaled so its last close matches the daily
     close of that day whenever the two differ by more than 3% (volume scaled inversely).

Usage:  python scripts\clean_data.py            (all timeframes, public + hidden)
        python scripts\clean_data.py --dry-run  (report only)
"""
import argparse
import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TFS = ["1h", "30m", "15m", "5m", "1m"]


def daily_closes(symbol):
    for folder in ("data/hidden", "data/daily"):
        p = os.path.join(ROOT, folder, symbol + ".csv")
        if os.path.exists(p):
            d = pd.read_csv(p)
            return dict(zip(d["date"], d["close"]))
    return {}


def clean_frame(df, dclose):
    n0 = len(df)
    c = df["close"].astype(float)
    prev, nxt = c.shift(1), c.shift(-1)
    spike = ((c / prev - 1).abs() > 0.4) & ((c / nxt - 1).abs() > 0.4)
    # runs of consecutive bad bars escape the neighbour rule: also drop any bar whose close is more
    # than 40% away from that DAY's median close (a real stock does not move 40% intraday and come back)
    day_median = c.groupby(df["date"]).transform("median")
    spike = spike | ((c / day_median - 1).abs() > 0.4)
    df = df[~spike.fillna(False)].copy()
    # a spike can also sit in the OPEN alone (close normal): the engine fills at opens, so fix it
    bad_open = (df["open"] / df["close"] - 1).abs() > 0.4
    df.loc[bad_open, "open"] = df.loc[bad_open, "close"]
    lo_ok = df[["open", "close"]].min(axis=1)
    hi_ok = df[["open", "close"]].max(axis=1)
    bad_low = df["low"] < 0.6 * lo_ok
    bad_high = df["high"] > 1.6 * hi_ok
    df.loc[bad_low, "low"] = lo_ok[bad_low]
    df.loc[bad_high, "high"] = hi_ok[bad_high]
    rescaled = 0
    if dclose:
        last = df.groupby("date")["close"].last()
        for day, last_close in last.items():
            ref = dclose.get(day)
            if ref is None or last_close <= 0:
                continue
            factor = ref / last_close
            if abs(factor - 1) > 0.03:
                m = df["date"] == day
                for col in ("open", "high", "low", "close"):
                    df.loc[m, col] = (df.loc[m, col] * factor).round(2)
                df.loc[m, "volume"] = (df.loc[m, "volume"] / factor).round().astype("int64")
                rescaled += 1
    return df, n0 - len(df), int(bad_low.sum() + bad_high.sum() + bad_open.sum()), rescaled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    totals = {"files": 0, "spikes": 0, "clamped": 0, "days_rescaled": 0}
    for tf in TFS:
        for folder in (os.path.join(ROOT, "data", tf), os.path.join(ROOT, "data", "hidden", tf)):
            for path in sorted(glob.glob(os.path.join(folder, "*.csv.gz"))):
                symbol = os.path.basename(path).split(".csv")[0]
                df = pd.read_csv(path)
                out, spikes, clamped, rescaled = clean_frame(df, daily_closes(symbol))
                totals["files"] += 1
                totals["spikes"] += spikes; totals["clamped"] += clamped; totals["days_rescaled"] += rescaled
                if (spikes or clamped or rescaled) and not a.dry_run:
                    out.to_csv(path, index=False, compression="gzip")
                if spikes or clamped or rescaled:
                    print(f"{tf:>3} {os.path.relpath(folder, ROOT):16s} {symbol:12s} spikes={spikes} clamped={clamped} days_rescaled={rescaled}")
            if not a.dry_run and os.path.isdir(folder):
                with open(os.path.join(folder, "_CLEANED.txt"), "w") as f:
                    f.write("spike bars dropped; highs/lows clamped; days rescaled to the adjusted daily close (scripts/clean_data.py)\n")
    print("TOTAL", totals, "(dry run, nothing written)" if a.dry_run else "")


if __name__ == "__main__":
    sys.exit(main())
