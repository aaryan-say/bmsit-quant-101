"""
data.py  -  where the candles come from.  Two sources, same output shape:

    load_cached()   -> reads ../cache/*.csv written by ../build_cache.py  (no login, instant)
    refresh_live()  -> logs in to Nubra PROD (data only) via ../screener.py, re-fetches, re-saves the cache
    load_live()     -> reads ../cache/_live.csv, the table the screener's live step (CHECKPOINT 5)
                       rewrites every 2 seconds while it runs; None if it is not there

Output shape (both): {"RELIANCE": DataFrame(date index; open, high, low, close in rupees; volume), ...}
plus a small dict of metadata (when the cache was built and from where).
"""
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION2 = os.path.dirname(HERE)                  # ../  (where screener.py and cache/ live)
CACHE_DIR = os.path.join(SESSION2, "cache")
META_FILE = os.path.join(CACHE_DIR, "_meta.json")

if SESSION2 not in sys.path:                      # so  import screener / build_cache  works
    sys.path.insert(0, SESSION2)


def load_cached():
    frames = {}
    if os.path.isdir(CACHE_DIR):
        for fn in sorted(os.listdir(CACHE_DIR)):
            if fn.endswith(".csv") and not fn.startswith("_"):
                df = pd.read_csv(os.path.join(CACHE_DIR, fn), parse_dates=["date"]).set_index("date").sort_index()
                frames[fn[:-4]] = df
    return frames, load_meta()


def load_meta():
    try:
        with open(META_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh_live():
    """Fetch fresh daily candles from Nubra PROD (data login, see ../common_login.py) and update the cache."""
    import screener            # noqa: E402  (from ../, added to sys.path above)
    import build_cache         # noqa: E402
    nubra = screener.checkpoint_1_login()          # PROD, read-only
    frames = screener.fetch_watchlist_live(nubra)
    if frames:
        build_cache.save_frames(frames, "NUBRA-PROD", "refreshed from the dashboard")
    return frames, load_meta()


LIVE_FILE = os.path.join(CACHE_DIR, "_live.csv")


def load_live():
    """The live ranked table written by  python screener.py  (CHECKPOINT 5), or None."""
    try:
        age = __import__("time").time() - os.path.getmtime(LIVE_FILE)
        return pd.read_csv(LIVE_FILE), age
    except OSError:
        return None, None
