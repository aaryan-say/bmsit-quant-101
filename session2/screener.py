"""
screener.py  -  the students' PROJECT: a mean-reversion screener for 15 NSE stocks,
                 with a LIVE WebSocket step and one (optional) sandbox order.

THE QUESTION WE ANSWER
    "Which stocks are unusually far from their own recent average price, right now?"
    Mean reversion = the bet that a price stretched far from its average tends to come back.

WHAT WE COMPUTE, PER STOCK, FROM DAILY CANDLES
    mean20 = average of the last 20 closing prices          ("where the price usually is")
    std20  = standard deviation of those 20 closes          ("how much it normally wobbles")
    z      = (last close - mean20) / std20                  ("how many wobbles away are we?")
    ret20  = last close / close 20 days ago - 1              ("20-day return, in %")
    label  = stretched LOW (z < -2), stretched HIGH (z > 2), else normal

TWO ENVIRONMENTS, ON PURPOSE  (see common_login.py)
    PROD  -> market DATA only: the daily candles and the live stream. Read-only.
    UAT   -> the sandbox: the ONE optional order goes here. Fake money.
    UAT has almost no price history, so the maths needs PROD data; a bug must cost nothing,
    so the order goes to UAT.

HOW TO RUN
    python screener.py                  PROD login, candles, maths, table, then 60 s of live updates
    python screener.py --offline        no login: candles from session2/cache/*.csv (CHECKPOINTS 2-4, 6)
    python screener.py --watch 0        live step runs until Ctrl+C (default 60 seconds)
    python screener.py --trade          + UAT login; ONE sandbox LIMIT BUY the first time a stock's
                                          live z crosses DOWN through -2 (CHECKPOINT 7)
    python screener.py --trade --trade-now   presenter rehearsal: skip the crossing, take the most
                                          negative z right now (risk rules still apply)
    python screener.py --offline --csv out.csv   also save the ranked table

The file is a ladder of numbered CHECKPOINTS.  Finish one, run, see output, move on.
    CHECKPOINT 1  login (PROD for data; UAT only with --trade)
    CHECKPOINT 2  daily candles for ONE stock
    CHECKPOINT 3  the maths for ONE stock
    CHECKPOINT 4  loop over the watchlist + ranked table
    CHECKPOINT 5  LIVE: WebSocket index stream, z recomputed on every tick, table reprinted every 2 s
    CHECKPOINT 6  risk rules: max 1 order per run, qty 1, only z < -2, never a stretched high
    CHECKPOINT 7  (optional, --trade) ONE UAT limit order when live z crosses down through -2
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
META_FILE = os.path.join(CACHE_DIR, "_meta.json")
LIVE_FILE = os.path.join(CACHE_DIR, "_live.csv")      # the live table, rewritten every refresh (dashboards read it)

# 15 liquid NSE stocks. ETERNAL (Zomato) is in on purpose: PROD has its full history.
# ALTERNATES maps a symbol to a fallback name if the instruments master does not know it.
WATCHLIST = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "TATAMOTORS", "ITC",
             "BHARTIARTL", "LT", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "MARUTI", "ETERNAL"]
ALTERNATES = {}   # e.g. {"ETERNAL": "ZOMATO"} if a symbol was renamed

WINDOW = 20                 # "20-day" everything
LOOKBACK_DAYS = 100         # calendar days of history to ask for (~65 trading days)
PAISE = 100                 # NSE prices come back as integer paise; 250000 paise = Rs 2500.00
MAX_SYMBOLS_PER_CALL = 5    # docs: historical_data takes at most 5 symbols per request
MIN_GAP_SECONDS = 1.1       # docs: 60 historical requests per minute -> never faster than ~1/s
Z_LIMIT = 2.0               # "stretched" threshold
REFRESH_SECONDS = 2.0       # live table is reprinted at most this often
IST = timezone(timedelta(hours=5, minutes=30))


# =====================================================================================
# CHECKPOINT 1 - LOGIN  (docs: Authentication; our helpers hide the .env plumbing)
# =====================================================================================
def checkpoint_1_login():
    """PROD client for market DATA (candles + stream). Read-only: never passed to NubraTrader."""
    from common_login import get_prod_client
    return get_prod_client()


def checkpoint_1_login_uat():
    """UAT client for the ONE optional sandbox order (--trade). Token saved in session2/."""
    from common_login import get_client
    return get_client()


# =====================================================================================
# CHECKPOINT 2 - DAILY CANDLES FOR ONE (OR UP TO FIVE) STOCKS  (docs: Historical Market Data)
# =====================================================================================
def _utc(dt):
    """Docs: dates must be UTC strings like 2025-04-19T11:01:57.000Z"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _points_to_series(points):
    """List of TimeSeriesPoint(timestamp ns, value int) -> pandas Series indexed by UTC time."""
    points = points or []
    return pd.Series([p.value for p in points],
                     index=pd.to_datetime([p.timestamp for p in points], unit="ns", utc=True),
                     dtype="float64")


def fetch_daily(md, symbols, lookback_days=LOOKBACK_DAYS):
    """
    One historical_data() call for up to 5 symbols. Returns {symbol: DataFrame} where each
    DataFrame has columns open, high, low, close (RUPEES) and volume, indexed by trading date.
    """
    assert len(symbols) <= MAX_SYMBOLS_PER_CALL, "docs: max 5 symbols per historical request"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    resp = md.historical_data({
        "exchange": "NSE",
        "type": "STOCK",
        "values": list(symbols),
        "fields": ["open", "high", "low", "close", "cumulative_volume"],
        "startDate": _utc(start),
        "endDate": _utc(end),
        "interval": "1d",
        "intraDay": False,        # False = include PAST days (True would mean "today only")
        "realTime": False,        # docs: "To be declared" -> always False
    })
    out = {}
    if resp is None or not getattr(resp, "result", None):
        return out
    for chart in resp.result:                       # docs access pattern
        for item in chart.values or []:
            for symbol, s in item.items():
                if not s.close:
                    continue
                df = pd.DataFrame({"open": _points_to_series(s.open), "high": _points_to_series(s.high),
                                   "low": _points_to_series(s.low), "close": _points_to_series(s.close),
                                   "volume": _points_to_series(s.cumulative_volume)})
                # trading DATE in India time; the SDK gives UTC nanoseconds
                df.index = df.index.tz_convert("Asia/Kolkata").normalize().tz_localize(None)
                df = df[~df.index.duplicated(keep="last")].sort_index()
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] / PAISE                 # paise -> rupees
                # cumulative_volume: for 1d candles the docs say it can be used as-is
                df["volume"] = df["volume"].fillna(0).astype("int64")
                df.index.name = "date"
                out[symbol] = df
    return out


def resolve_watchlist(nubra, watchlist=WATCHLIST):
    """
    Check each symbol exists in the NSE instruments master (docs: Get Instruments).
    get_instrument_by_symbol() returns an Instrument object, or a {"msg": ...} dict if unknown.
    Returns a list of (requested_name, resolved_name_or_None).
    """
    from nubra_python_sdk.refdata.instruments import InstrumentData
    instruments = InstrumentData(nubra)
    resolved = []
    for name in watchlist:
        candidates = [name] + ([ALTERNATES[name]] if name in ALTERNATES else [])
        found = None
        for cand in candidates:
            try:
                inst = instruments.get_instrument_by_symbol(cand, exchange="NSE")
            except Exception as e:                      # master not loadable right now
                inst = {"msg": f"{type(e).__name__}: {e}"}
            if not isinstance(inst, dict):              # dict == "not found" message
                found = inst.stock_name
                break
        resolved.append((name, found))
        print(f"  {name:<12} -> {found or 'NOT IN INSTRUMENTS MASTER'}")
    if resolved and all(found is None for _, found in resolved):
        # Every name failed: that is the master being unavailable (nightly rebuild, network), not 15 typos.
        # historical_data() takes plain symbols, so carry on with the names as written.
        print("  instruments master unavailable right now; using the watchlist names as-is")
        resolved = [(name, name) for name, _ in resolved]
    return resolved


def fetch_watchlist_live(nubra, watchlist=WATCHLIST):
    """CHECKPOINT 2 for everyone: resolve names, then fetch in batches of 5, politely."""
    from nubra_python_sdk.marketdata.market_data import MarketData
    md = MarketData(nubra)
    print("Resolving symbols against the instruments master...")
    names = [r for _, r in resolve_watchlist(nubra, watchlist) if r]
    frames, last_call = {}, 0.0

    def politely(batch):
        nonlocal last_call
        wait = MIN_GAP_SECONDS - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)                              # respect 60 requests / minute
        last_call = time.time()
        return fetch_daily(md, batch)

    for i in range(0, len(names), MAX_SYMBOLS_PER_CALL):
        batch = names[i:i + MAX_SYMBOLS_PER_CALL]
        try:
            got = politely(batch)
        except Exception as e:
            # one unknown ticker makes the API reject the WHOLE batch (400 "ticker not found"),
            # so retry the names one at a time and let only the bad one fail
            print(f"  historical_data failed for {batch}: {type(e).__name__}: {e}")
            print("  retrying that batch one symbol at a time...")
            got = {}
            for sym in batch:
                try:
                    got.update(politely([sym]))
                except Exception as e1:
                    print(f"  {sym:<12} failed on its own: {type(e1).__name__}: {str(e1)[:80]}")
        for sym in batch:
            n = len(got.get(sym, []))
            print(f"  {sym:<12} {n} daily candles")
        frames.update(got)
    return frames


# =====================================================================================
# CHECKPOINT 3 - THE MATHS FOR ONE STOCK
# =====================================================================================
def checkpoint_3_maths(df: pd.DataFrame, window: int = WINDOW, live_close: float = None) -> dict:
    """
    df must have a 'close' column in rupees, oldest row first, newest row last.
    live_close (optional, CHECKPOINT 5): today's price from the stream. It REPLACES the newest
    close if that candle is today's (still forming), otherwise it is APPENDED as today's row.

    MEAN (average): add up the last 20 closes and divide by 20. It is the "centre".
    STANDARD DEVIATION: how far, typically, each close sits from that centre.
        1. for each of the 20 closes take (close - mean)
        2. square it (so negatives do not cancel positives)
        3. average those squares (pandas divides by n-1 = 19, the "sample" version)
        4. square-root to get back to rupees
    Z-SCORE: (last close - mean) / std. Unit-free. z = -2 means "two wobbles below normal".
        About 95% of the time a well-behaved series sits between -2 and +2, so |z| > 2 is rare.
    20-DAY RETURN: (today's close / close 20 trading days ago) - 1, shown in %.
    """
    close = df["close"].dropna()
    if live_close is not None:
        close = close.copy()
        today = pd.Timestamp(datetime.now(IST).date())
        if pd.Timestamp(close.index[-1]).normalize() == today:
            close.iloc[-1] = live_close                 # today's candle is still forming: overwrite it
        else:
            close.loc[today] = live_close               # history ends yesterday: today is a new row
    if len(close) < window + 1:
        raise ValueError(f"need at least {window + 1} closes, have {len(close)}")
    last20 = close.tail(window)                 # the most recent 20 closes
    mean20 = last20.mean()
    std20 = last20.std()                        # pandas default ddof=1 (sample std)
    last_close = close.iloc[-1]
    z = (last_close - mean20) / std20 if std20 > 0 else 0.0
    ret20 = (last_close / close.iloc[-1 - window] - 1.0) * 100.0
    return {"last_close": round(last_close, 2), "mean20": round(mean20, 2), "std20": round(std20, 2),
            "z": round(z, 2), "ret20_pct": round(ret20, 2), "label": label_for(z), "as_of": close.index[-1]}


def label_for(z: float) -> str:
    if z < -Z_LIMIT:
        return "stretched LOW (z < -2)"
    if z > Z_LIMIT:
        return "stretched HIGH (z > 2)"
    return "normal"


# =====================================================================================
# CHECKPOINT 4 - LOOP OVER THE WATCHLIST + RANKED TABLE
# =====================================================================================
def checkpoint_4_table(frames: dict, live: dict = None) -> pd.DataFrame:
    """{symbol: candles} -> ranked table. live = {symbol: price in rupees} from CHECKPOINT 5, or None."""
    rows = []
    for symbol, df in frames.items():
        try:
            m = checkpoint_3_maths(df, live_close=(live or {}).get(symbol))
        except ValueError as e:
            if live is None:                            # say it once, in CHECKPOINT 4, not on every live refresh
                print(f"  skip {symbol}: {e}")
            continue
        rows.append({"symbol": symbol, "last_close": m["last_close"], "mean20": m["mean20"],
                     "std20": m["std20"], "z": m["z"], "ret20_pct": m["ret20_pct"], "label": m["label"],
                     "as_of": m["as_of"].date() if hasattr(m["as_of"], "date") else m["as_of"]})
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.sort_values("z").reset_index(drop=True)        # most negative z first


def print_table(table: pd.DataFrame):
    if table.empty:
        print("(no rows)")
        return
    show = table.copy()
    show.insert(0, "rank", range(1, len(show) + 1))
    pd.set_option("display.width", 160)
    print(show.to_string(index=False, formatters={
        "last_close": "{:,.2f}".format, "mean20": "{:,.2f}".format, "std20": "{:,.2f}".format,
        "z": "{:+.2f}".format, "ret20_pct": "{:+.2f}%".format}))


# =====================================================================================
# CHECKPOINT 6 - RISK RULES  (written before 7 on purpose: rules first, then the order)
# =====================================================================================
ORDER_QTY = 1                    # one share, always
MAX_ORDERS_PER_RUN = 1           # one order, always
_orders_sent_this_run = 0


def checkpoint_6_risk_check(row) -> bool:
    """Return True only if ALL rules pass. Each rule prints WHY it refuses."""
    if _orders_sent_this_run >= MAX_ORDERS_PER_RUN:
        print("RISK: already sent the one allowed order this run. Refusing.")
        return False
    if abs(row["z"]) < Z_LIMIT:
        print(f"RISK: |z| = {abs(row['z']):.2f} < {Z_LIMIT}: {row['symbol']} is not stretched. Refusing.")
        return False
    if row["z"] > 0:
        print(f"RISK: z = {row['z']:+.2f} is stretched HIGH; this screener only BUYS lows. Refusing.")
        return False
    return True


# =====================================================================================
# CHECKPOINT 5 - LIVE: the realtime index stream  (docs: Realtime Data -> Index Data)
# =====================================================================================
class LiveScreener:
    """
    Keeps the latest live price per symbol (from the PROD WebSocket) and rebuilds the ranked
    table on demand.  Docs facts used here:
      - data_type="index" also streams STOCK symbols on NSE (the name is historical)
      - subscription key = plain symbol + exchange="NSE"; weight 1 each -> 15 of the 50,000 budget
      - index_value is an int in exchange-native units: paise on NSE, so / 100
      - the socket runs in its own thread; keep_running() would block, so we poll a dict instead
    """
    def __init__(self, frames: dict):
        self.frames = frames
        self.live = {}                       # symbol -> latest price in RUPEES
        self.updates = 0                     # how many messages arrived
        self.last_msg_at = None              # wall clock of the latest message
        self.lock = threading.Lock()         # the callback runs on the socket thread
        self.table = checkpoint_4_table(frames)      # start from the historical table
        self.prev_z = dict(zip(self.table["symbol"], self.table["z"])) if not self.table.empty else {}

    def on_index_data(self, msg):
        """Callback for every stream message (docs shape: indexname, index_value, timestamp, ...)."""
        if not msg.indexname or not msg.index_value:
            return
        with self.lock:
            self.live[msg.indexname] = msg.index_value / PAISE
            self.updates += 1
            self.last_msg_at = time.time()

    def refresh(self):
        """Recompute mean20/std20/z with the live closes. Returns (table, crossings) where crossings
        is the list of symbols whose z just crossed DOWN through -2 (was >= -2, now < -2)."""
        with self.lock:
            live = dict(self.live)
        table = checkpoint_4_table(self.frames, live)
        crossings = []
        for sym, z in zip(table["symbol"], table["z"]):
            before = self.prev_z.get(sym)
            if before is not None and before >= -Z_LIMIT and z < -Z_LIMIT:
                crossings.append(sym)
            self.prev_z[sym] = z
        self.table = table
        return table, crossings


def print_live_table(ls: LiveScreener, table: pd.DataFrame, seconds_left):
    """Compact version of the table for the live loop; clears the screen on a real terminal."""
    if sys.stdout.isatty():
        print("\x1b[2J\x1b[H", end="")                # ANSI clear screen + home
    stamp = datetime.now(IST).strftime("%H:%M:%S")
    last = datetime.fromtimestamp(ls.last_msg_at, IST).strftime("%H:%M:%S") if ls.last_msg_at else "-"
    left = "until Ctrl+C" if seconds_left is None else f"{max(0, int(seconds_left))} s left"
    print(f"CHECKPOINT 5 LIVE  {stamp} IST  |  {ls.updates} updates, last {last}  |  {len(ls.live)}/{len(ls.frames)} symbols ticking  |  {left}")
    if not ls.live:
        print("no updates yet (market closed? the NSE index stream only ticks 09:15-15:30 IST). Showing the last known table.")
    print(f"{'#':>2} {'symbol':<11}{'live':>10}{'mean20':>10}{'std20':>9}{'z':>7}  {'label':<24}")
    for i, r in enumerate(table.itertuples(index=False), 1):
        tick = "*" if r.symbol in ls.live else " "
        print(f"{i:>2} {r.symbol:<10}{tick}{r.last_close:>10,.2f}{r.mean20:>10,.2f}{r.std20:>9,.2f}{r.z:>+7.2f}  {r.label:<24}")
    print("* = has a live price; others still show their last daily close")
    try:
        table.to_csv(LIVE_FILE, index=False)          # dashboards can read this file
    except OSError:
        pass


def checkpoint_5_live(nubra_prod, frames: dict, watch_seconds: float, on_cross=None):
    """
    Subscribe to the index stream for every symbol we have candles for, then every 2 seconds:
    recompute the table with live closes, print it, and hand any -2 cross-down to on_cross(symbol).
    watch_seconds: how long to watch (0 or None = until Ctrl+C).
    """
    from nubra_python_sdk.ticker import websocketdata
    ls = LiveScreener(frames)
    symbols = list(frames)
    errors = []
    socket = websocketdata.NubraDataSocket(
        client=nubra_prod,
        on_index_data=ls.on_index_data,                       # stream-specific callback (docs)
        on_connect=lambda m: print(f"  socket: {m}"),
        on_close=lambda r: print(f"  socket closed: {r}"),
        on_error=lambda e: errors.append(str(e)),
    )
    socket.connect()                                          # starts the socket thread
    socket.subscribe(symbols, data_type="index", exchange="NSE")   # weight 1 x 15 symbols
    print(f"  subscribed {len(symbols)} symbols on the NSE index stream (weight {len(symbols)} of 50,000). "
          f"Table refreshes every {REFRESH_SECONDS:.0f} s" + (" until Ctrl+C." if not watch_seconds else f" for {watch_seconds:.0f} s."))
    time.sleep(1.0)
    t_end = None if not watch_seconds else time.time() + watch_seconds
    try:
        while True:
            table, crossings = ls.refresh()
            print_live_table(ls, table, None if t_end is None else t_end - time.time())
            for err in errors[-3:]:
                print(f"  socket error: {err}")
            for sym in crossings:
                print(f"SIGNAL: {sym} z crossed DOWN through -{Z_LIMIT:.0f} (now {ls.prev_z[sym]:+.2f})")
                if on_cross:
                    on_cross(table[table["symbol"] == sym].iloc[0], ls.live.get(sym))
            if t_end is not None and time.time() >= t_end:
                break
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nstopped by user (Ctrl+C)")
    finally:
        try:
            socket.close()
        except Exception:
            pass
    return ls


# =====================================================================================
# CHECKPOINT 7 - ONE UAT LIMIT ORDER  (docs: Place Single Order, Get Margin, Get Orders)
# =====================================================================================
def checkpoint_7_trade(nubra_uat, row, live_price_rupees=None):
    """
    Called when a symbol's live z crossed DOWN through -2 (or by --trade-now for rehearsal).
    Risk rules (CHECKPOINT 6) run first and print why they refuse. On success: one LIMIT BUY,
    qty 1, at the last traded price rounded to the tick size, in UAT, then read back.
    """
    global _orders_sent_this_run
    print(f"\nCHECKPOINT 7: candidate {row['symbol']} z={row['z']:+.2f} label={row['label']}")
    if not checkpoint_6_risk_check(row):
        return

    from nubra_python_sdk.refdata.instruments import InstrumentData
    from nubra_python_sdk.marketdata.market_data import MarketData
    from nubra_python_sdk.trading.trading_data import NubraTrader
    instruments = InstrumentData(nubra_uat)
    md = MarketData(nubra_uat)
    trader = NubraTrader(nubra_uat)

    # 1) resolve the instrument IN UAT -> ref_id + tick_size (docs: ref_ids differ per environment, never invent one)
    inst = instruments.get_instrument_by_symbol(row["symbol"], exchange="NSE")
    if isinstance(inst, dict):
        print(f"cannot resolve {row['symbol']} in UAT: {inst}. Refusing.")
        return
    ref_id, tick = int(inst.ref_id), int(inst.tick_size)

    # 2) last traded price in PAISE: the docs' pattern is quote(); UAT can be stale, so fall back to the live price
    ltp = 0
    try:
        q = md.quote(ref_id=ref_id, levels=1)
        ltp = int(q.orderBook.last_traded_price) if q and q.orderBook and q.orderBook.last_traded_price else 0
    except Exception as e:
        print(f"quote() failed ({type(e).__name__}: {e})")
    if ltp <= 0 and live_price_rupees:
        ltp = int(round(live_price_rupees * PAISE))
        print(f"UAT quote empty; using the live stream price Rs {live_price_rupees:,.2f}")
    if ltp <= 0:
        print("no traded price available (market closed / UAT stale). Refusing to send a blind order.")
        return
    price = int(round(ltp / max(tick, 1)) * max(tick, 1))   # multiple of tick_size, integer paise

    payload = {
        "refId": ref_id,
        "qty": ORDER_QTY,
        "side": "BUY",
        "deliveryType": "IDAY",
        "priceType": "LIMIT",
        "validityType": "DAY",
        "isMultiLeg": False,
        "executionMode": "ENTRY",
        "entryPrice": price,
        "stratTags": ["bmsit-screener"],       # one tag, hyphens only
    }

    # 3) docs: "Use get_margin() before placement when funds requirement matters"
    try:
        funds = trader.get_margin({"requestType": "NEW", "orders": [payload]})
        print(f"funds required (paise): {funds.totalFundsRequired}  -> Rs {funds.totalFundsRequired / PAISE:,.2f}")
    except Exception as e:
        print(f"get_margin failed (continuing): {type(e).__name__}: {e}")

    # 4) place it (UAT)
    print(f"ORDER SENT (UAT) BUY {ORDER_QTY} x {row['symbol']} LIMIT @ Rs {price / PAISE:,.2f} (ref_id {ref_id})")
    try:
        result = trader.create_order(payload)
    except Exception as e:
        print(f"ORDER FAILED: {type(e).__name__}: {e}")
        return
    _orders_sent_this_run += 1                     # counts even if rejected: one attempt per run
    order = result.orders[0]
    print(f"ORDER ID {order.intentOrderId} status {order.status}"
          + (f" ({order.rejectionMsg})" if order.rejectionMsg else ""))

    # 5) read it back (docs: Get Orders -> get_order(intentOrderId)); never assume, read the status
    time.sleep(0.5)
    found = trader.get_order(int(order.intentOrderId))
    if found:
        o = found[0]
        print(f"ORDER STATUS (get_order) {o.intentOrderId} -> {o.status}, filled {o.filledQty}/{o.orderQty}"
              + (f", reason: {o.rejectionMsg}" if o.rejectionMsg else ""))


# =====================================================================================
# OFFLINE CACHE  (made by build_cache.py from PROD; lets students without a login do steps 2-4, 6)
# =====================================================================================
def load_cache(cache_dir=CACHE_DIR):
    frames = {}
    if not os.path.isdir(cache_dir):
        return frames
    wanted = set(cache_meta(cache_dir).get("symbols") or [])     # files from an OLDER build are ignored
    for fn in sorted(os.listdir(cache_dir)):
        if fn.endswith(".csv") and not fn.startswith("_"):
            if wanted and fn[:-4] not in wanted:
                print(f"  ignoring {fn}: not part of the latest cache build (stale file)")
                continue
            df = pd.read_csv(os.path.join(cache_dir, fn), parse_dates=["date"]).set_index("date").sort_index()
            frames[fn[:-4]] = df
    return frames


def cache_meta(cache_dir=CACHE_DIR):
    try:
        with open(os.path.join(cache_dir, "_meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# =====================================================================================
# MAIN
# =====================================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="no login: read session2/cache/*.csv (built from PROD)")
    ap.add_argument("--watch", type=float, default=60, help="CHECKPOINT 5: seconds to watch the live stream (0 = until Ctrl+C)")
    ap.add_argument("--trade", action="store_true", help="CHECKPOINT 7: also log in to UAT and place ONE sandbox order on a z cross-down")
    ap.add_argument("--trade-now", action="store_true", help="with --trade: skip the crossing, try the most negative z right now (rehearsal)")
    ap.add_argument("--csv", help="also save the ranked table to this path")
    a = ap.parse_args()

    if a.offline:
        meta = cache_meta()
        frames = load_cache()
        if not frames:
            sys.exit("cache is empty. Run  python build_cache.py  (needs the PROD data login) "
                     "or  python build_cache.py --synthetic  (anyone, fake data).")
        src = meta.get("source", "unknown")
        print(f"OFFLINE: {len(frames)} cached stocks from session2/cache  (source: {src}, built {meta.get('built_at', '?')})")
        if src == "SYNTHETIC":
            print("!! SYNTHETIC DATA - random numbers for practice, NOT real prices !!")
        prod = None
    else:
        print("CHECKPOINT 1: login to PROD for market data (read-only)")
        prod = checkpoint_1_login()
        print("CHECKPOINT 2: daily candles for the watchlist")
        frames = fetch_watchlist_live(prod)
        if not frames:
            frames = load_cache()                           # rate-limited or API hiccup: last night's cache
            if not frames:
                sys.exit("no candles came back and the cache is empty; cannot continue")
            print(f"  no candles came back from the API; using {len(frames)} cached stocks from session2/cache instead")

    # CHECKPOINT 3 shown for the first stock, so students see the numbers once in isolation
    first = next(iter(frames))
    print(f"\nCHECKPOINT 3: maths for {first}")
    for k, v in checkpoint_3_maths(frames[first]).items():
        print(f"  {k:<10} {v}")

    print("\nCHECKPOINT 4: ranked table (most negative z first)")
    table = checkpoint_4_table(frames)
    print_table(table)
    if a.csv:
        table.to_csv(a.csv, index=False)
        print(f"saved {a.csv}")

    # CHECKPOINT 7 needs a UAT login; get it BEFORE the live loop so the order can fire on a crossing
    uat = None
    if a.trade:
        if prod is None:
            sys.exit("--trade needs the live stream (drop --offline) and a UAT login.")
        print("\nCHECKPOINT 1b: login to UAT for the one sandbox order")
        uat = checkpoint_1_login_uat()

    if prod is None:
        print("\nCHECKPOINT 5 (live stream) skipped: --offline has no login. CHECKPOINT 6 rules are still loaded.")
        return

    print("\nCHECKPOINT 5: live. Every tick replaces today's close, z is recomputed, the table re-ranks.")
    if a.trade and a.trade_now:
        # rehearsal: no need to wait for a crossing; the risk rules still decide
        ls = checkpoint_5_live(prod, frames, min(a.watch or 10, 10))
        row = ls.table.iloc[0]
        checkpoint_7_trade(uat, row, ls.live.get(row["symbol"]))
        return

    def on_cross(row, live_price):
        if uat is None:
            print(f"  (no --trade: would consider {row['symbol']} for one UAT order; not sent)")
            return
        checkpoint_7_trade(uat, row, live_price)

    checkpoint_5_live(prod, frames, a.watch, on_cross=on_cross)
    if a.trade and _orders_sent_this_run == 0:
        print("\nCHECKPOINT 7: no order sent. Why: no stock's live z crossed DOWN through -2 during the watch window "
              "(the rule is a crossing, not 'is below -2'). Run longer with --watch 0, or rehearse with --trade-now.")


if __name__ == "__main__":
    main()
