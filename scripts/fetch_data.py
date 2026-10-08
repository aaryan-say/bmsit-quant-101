"""PRESENTER ONLY: replace the synthetic data with real NSE candles from Nubra PROD.

    python scripts\\fetch_data.py --env-dir <folder with .env and auth_data.db*>              # daily (1d)
    python scripts\\fetch_data.py --env-dir <folder> --interval 15m                           # one timeframe
    python scripts\\fetch_data.py --resolve-only                # just check which symbols the master knows

Needs:  pip install nubra-sdk pandas   and a Nubra PROD login. The SDK keeps its token in auth_data.db*
and reads PHONE_NO / MPIN from .env, both in the CURRENT WORKING DIRECTORY, so this script chdir()s to
--env-dir first. First-ever login asks phone -> OTP (SMS) -> MPIN interactively; later runs only need the
MPIN (from .env or typed). Secrets are never printed.

Layout written (see scripts/universe.py::data_folders):
    1d     data/daily/<SYMBOL>.csv        2025-07-01..2026-06-30 (public)
           data/hidden/<SYMBOL>.csv       2025-07-01..2026-09-30 (git-ignored; scored from 2026-07-01)
    other  data/<tf>/<SYMBOL>.csv.gz      2025-09-04..2026-06-30 (public; PROD keeps intraday from 2025-09-04)
           data/hidden/<tf>/<SYMBOL>.csv.gz  2025-09-04..2026-09-30 (git-ignored; scored from 2026-07-01)
Each folder gets _SOURCE.txt (NUBRA-PROD + fetch date); hidden folders also get _EVAL_START.txt.
Columns: date,open,high,low,close,volume (intraday files also have a leading datetime column, IST).
API facts used (SDK docs + PROD checks on 2026-10-08): historical_data(type STOCK|INDEX, values <= 5
symbols per call, UTC ISO dates, prices in paise, 60 requests/minute, one call returns a whole window
even for 1m; intraday volume is cumulative within the day).
"""
import argparse
import glob
import os
import sys
import time
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from universe import (BAR_MINUTES, HIDDEN_END, HIDDEN_START, INDEX_SYMBOL, INTERVALS,  # noqa: E402
                      INTRADAY_START, NIFTY50, PUBLIC_END, PUBLIC_START, SESSION_MINUTES, data_folders)

for _s in (sys.stdout, sys.stderr):   # SDK prints emoji; avoid cp1252 crashes on Windows
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MIN_GAP_S = 1.1            # docs: 60 historical requests per minute
FIELDS = ["open", "high", "low", "close", "cumulative_volume"]
MIN_STOCKS_TO_WRITE = 30   # refuse to wipe an existing dataset if the fetch clearly failed


# ----------------------------------------------------------------------------- login
def _env_has(env_file, key):
    if os.environ.get(key):
        return True
    try:
        with open(env_file, encoding="utf-8") as f:
            return any(l.strip().startswith(key) and "=" in l and l.split("=", 1)[1].strip(" \"'\r\n") for l in f)
    except OSError:
        return False


def connect(env_dir):
    """Authenticated PROD client. Mirrors the workshop's data/common.py helper."""
    env_dir = os.path.abspath(env_dir)
    os.chdir(env_dir)                                   # SDK reads .env and auth_data.db* from the CWD
    env_file = os.path.join(env_dir, ".env")
    has_session = bool(glob.glob(os.path.join(env_dir, "auth_data.db*")))
    tty = sys.stdin.isatty()
    if not has_session and not tty:
        sys.exit(f"No saved Nubra session (auth_data.db*) in {env_dir} and this is not an interactive "
                 "terminal, so the OTP login cannot run. Open a terminal and run this script there.")
    if has_session and not _env_has(env_file, "MPIN") and not tty:
        sys.exit(f"MPIN is needed to re-verify the saved session but {env_file} has no MPIN and this is not "
                 "an interactive terminal. Add MPIN=\"....\" to .env or run in a terminal.")
    print(f"auth dir: {env_dir}  (session file: {'found' if has_session else 'none -> OTP login'}, "
          f".env: {'found' if os.path.exists(env_file) else 'none -> interactive prompts'})")
    from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv   # late import: fast --help
    nubra = InitNubraSdk(NubraEnv.PROD, env_creds=os.path.exists(env_file))
    if not nubra.token_data.get("session_token"):
        sys.exit("Login did not complete (no session token).")
    return nubra


# ----------------------------------------------------------------------------- resolve symbols
def resolve(nubra):
    """Return ([resolved stock symbols], [unresolved candidate tuples]) using the NSE instruments master."""
    from nubra_python_sdk.refdata.instruments import InstrumentData
    df = InstrumentData(nubra).get_instruments_dataframe(exchange="NSE")
    names = set(df["stock_name"].astype(str).str.upper()) if "stock_name" in df else set()
    nubra_names = set(df["nubra_name"].astype(str).str.upper()) if "nubra_name" in df else set()
    ok, missing = [], []
    for cands in NIFTY50:
        hit = next((c for c in cands if c in names or f"STOCK_{c}.NSECM" in nubra_names), None)
        (ok.append(hit) if hit else missing.append(cands))
    print(f"instruments master: {len(df)} NSE rows; resolved {len(ok)}/{len(NIFTY50)} universe symbols")
    for cands in missing:
        print(f"  !! not in instruments master, skipped: {' / '.join(cands)}")
    return ok, missing


# ----------------------------------------------------------------------------- fetch
def _series(points):
    points = points or []
    idx = pd.to_datetime([p.timestamp for p in points], unit="ns", utc=True)
    return pd.Series([p.value for p in points], index=idx, dtype="float64")


class Throttle:
    def __init__(self):
        self.last = 0.0

    def wait(self):
        gap = MIN_GAP_S - (time.time() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.last = time.time()


def fetch_batch(md, throttle, kind, symbols, start, end, interval):
    """One historical_data call (<= 5 symbols, one date window). Returns {symbol: DataFrame} with IST index."""
    req = {"exchange": "NSE", "type": kind, "values": symbols, "fields": FIELDS,
           "startDate": f"{start}T00:00:00.000Z", "endDate": f"{end}T23:59:59.000Z",
           "interval": interval, "intraDay": False, "realTime": False}
    out = {}
    for attempt in range(3):
        throttle.wait()
        try:
            resp = md.historical_data(req)
            break
        except Exception as e:  # network / validation; never includes secrets
            print(f"  {kind} {symbols} {start}..{end}: {type(e).__name__}: {e} (try {attempt + 1}/3)")
            time.sleep(2 * (attempt + 1))
    else:
        return out
    for chart in getattr(resp, "result", None) or []:
        for item in chart.values:
            for sym, sc in item.items():
                if not sc.close:
                    continue
                df = pd.DataFrame({"open": _series(sc.open), "high": _series(sc.high), "low": _series(sc.low),
                                   "close": _series(sc.close), "volume": _series(sc.cumulative_volume)})
                df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)   # IST bar start
                if interval == "1d":
                    df.index = df.index.normalize()                                # IST trading day
                out[sym.upper()] = df
    return out


def fetch_all(md, kind, symbols, interval, batch_size):
    """Candles for the full window, fetched per batch and per sub-window (public, hidden)."""
    throttle, frames = Throttle(), {}
    first = PUBLIC_START if interval == "1d" else INTRADAY_START
    windows = [(first, PUBLIC_END), (HIDDEN_START, HIDDEN_END)]
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        for start, end in windows:
            got = fetch_batch(md, throttle, kind, batch, start, end, interval)
            for sym, df in got.items():
                frames[sym] = pd.concat([frames[sym], df]) if sym in frames else df
            print(f"  {kind} {interval} batch {i // batch_size + 1}: {start}..{end}: "
                  + ", ".join(f"{s}={len(got[s]) if s in got else 0}" for s in batch))
    clean = {}
    for sym, df in frames.items():
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[(df.index >= pd.Timestamp(first)) & (df.index < pd.Timestamp(HIDDEN_END) + pd.Timedelta(days=1))]
        for c in ("open", "high", "low", "close"):
            df[c] = (df[c] / 100.0).round(2)                      # paise -> rupees
        vol = df["volume"].fillna(0)
        if interval != "1d":
            # docs: sub-daily cumulative_volume must be differenced. It resets each day, so a negative
            # diff (or the first row) means "first bar of the day": use the raw cumulative value there.
            d = vol.diff()
            vol = d.where(d >= 0, vol).fillna(vol)
        df["volume"] = vol.astype("int64")
        df.index.name = "datetime" if interval != "1d" else "date"
        df = df.reset_index()
        if interval != "1d":
            df.insert(1, "date", df["datetime"].dt.normalize())
        clean[sym] = df
    return clean


# ----------------------------------------------------------------------------- write
def write_set(frames, folder, ext, start, end, label, fetched, interval):
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):                                   # remove synthetic / stale files (not subfolders)
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
        f.write(f"NUBRA-PROD\nfetched: {fetched}\nby: scripts/fetch_data.py (Nubra Python SDK, historical_data {interval})\n"
                f"interval: {interval}\nrange: {start} to {end}\nsymbols: {len(frames)}\n{label}\n"
                "Prices in rupees (paise/100), unadjusted; timestamps are IST (bar start).\n")
    return size


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-dir", default=ROOT, help="folder holding .env and auth_data.db* (default: repo root)")
    ap.add_argument("--interval", default="1d", choices=INTERVALS, help="candle size (default 1d)")
    ap.add_argument("--batch", type=int, default=None,
                    help="symbols per request (default: 5 for 1d, 1 for intraday; docs max 5)")
    ap.add_argument("--resolve-only", action="store_true", help="only report which symbols resolve")
    a = ap.parse_args()
    iv = a.interval
    batch = a.batch or (5 if iv == "1d" else 1)
    batch = max(1, min(5, batch))
    nubra = connect(a.env_dir)
    stocks, _missing = resolve(nubra)
    if a.resolve_only:
        return
    from nubra_python_sdk.marketdata.market_data import MarketData
    md = MarketData(nubra)
    first = PUBLIC_START if iv == "1d" else INTRADAY_START
    n_req = 2 * (-(-len(stocks) // batch) + 1)
    print(f"\nfetching {len(stocks)} stocks + {INDEX_SYMBOL} at {iv} ({first}..{HIDDEN_END}), "
          f"{n_req} requests at 1.1 s apart (~{n_req * 1.1 / 60:.1f} min)")
    frames = fetch_all(md, "STOCK", stocks, iv, batch)
    frames.update(fetch_all(md, "INDEX", [INDEX_SYMBOL], iv, 1))
    bars_per_day = 1 if iv == "1d" else -(-SESSION_MINUTES // BAR_MINUTES[iv])
    expected = bars_per_day * (310 if iv == "1d" else 265)        # trading days in the full window
    print(f"\nrows per symbol (expect ~{expected} for the full window):")
    bad = []
    for sym in sorted(frames):
        df = frames[sym]
        rng = f"{df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d}"
        ok = len(df) >= 0.6 * expected and df["date"].max() >= pd.Timestamp(HIDDEN_END) - pd.Timedelta(days=7)
        flag = "" if ok else "  <-- CHECK"
        print(f"  {sym:<12}{len(df):>7}  {rng}{flag}")
        if flag:
            bad.append(sym)
    n_stocks = len([s for s in frames if s != INDEX_SYMBOL])
    if n_stocks < MIN_STOCKS_TO_WRITE:
        sys.exit(f"\nOnly {n_stocks} stocks returned data; NOT overwriting the existing dataset.")
    fetched = datetime.now().strftime("%Y-%m-%d %H:%M")
    pub, hid, ext = data_folders(iv, ROOT)
    s1 = write_set(frames, pub, ext, first, PUBLIC_END, "public set (students)", fetched, iv)
    s2 = write_set(frames, hid, ext, first, HIDDEN_END, f"presenter set; judged from {HIDDEN_START}", fetched, iv)
    with open(os.path.join(hid, "_EVAL_START.txt"), "w", encoding="utf-8") as f:
        f.write(HIDDEN_START + "\n")
    print(f"\nwrote {len(frames)} symbols at {iv}:\n  {pub}  ({s1 / 1e6:.1f} MB)\n  {hid}  ({s2 / 1e6:.1f} MB)"
          f"\n  _SOURCE.txt = NUBRA-PROD, {fetched}")
    if bad:
        print(f"symbols flagged CHECK (short history or stale end date): {', '.join(bad)}")
    print("Next: python backtest.py --all   and   python tests\\test_engine.py")


if __name__ == "__main__":
    main()
