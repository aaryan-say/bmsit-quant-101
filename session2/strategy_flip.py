"""
strategy_flip.py  -  the live DEMO: "candle colour flip" on NIFTY, 1-minute candles.
UAT (sandbox) ONLY.  No stop-loss, no target: this is a teaching toy, not advice.

THE IDEA IN ONE BREATH
    Look at the last two COMPLETED 1-minute candles of NIFTY.
    red  -> green  (the newest candle is green, the one before it was red)  =>  BUY the ATM CALL (CE)
    green -> red   (the newest candle is red,  the one before it was green) =>  BUY the ATM PUT  (PE)
    Before buying a new leg, SELL (square off) whatever leg this script bought earlier.
    Quantity = 10 lots  (qty = 10 * lot_size, lot_size comes from the instrument).

A green candle: close > open.   A red candle: close < open.   Equal = flat (ignored).

HOW WE GET CANDLES  (WebSocket stream, the way a real system watches the market)
    Default: the realtime OHLCV WebSocket,
             socket.subscribe(["NIFTY"], data_type="ohlcv", interval="1m", exchange="NSE").
             Docs: it "delivers continuously updating OHLCV candle buckets" for "the CURRENT
             interval candle", with bucket_timestamp = candle start.  A candle is complete
             the moment a message with a NEW bucket_timestamp arrives: no polling, no waiting.
             The socket runs in a background thread; completed candles are handed to the strategy.
    Fallback (--source poll): every 5 seconds call historical_data() with intraDay=True and
             take the last completed rows. Only if the venue network blocks WebSockets.

MODES
    python strategy_flip.py                       live WebSocket, real UAT orders
    python strategy_flip.py --source poll         live polling fallback, real UAT orders
    python strategy_flip.py --replay              REHEARSAL: last 60 historical 1-min candles,
                                                  fed one per second -> flips within a minute,
                                                  real UAT orders (sandbox money only)
    python strategy_flip.py --replay --dry-run    same, but orders are only printed
    python strategy_flip.py --synthetic           no login at all: random candles + fake broker
    flags: --no-squareoff   keep the old leg when a new leg is bought
           --lots N         number of lots (default 10)
           --max-signals N  stop after N flips (default: run until Ctrl+C)

RATE LIMITS (docs): UAT trading 100 ops/s, historical 60 req/min. We stay far below both.

Every SDK call below is the documented one; comments say which docs page it came from.
"""
import argparse
import math
import queue
import random
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- settings
UNDERLYING = "NIFTY"          # option_chain() wants the UNDERLYING, never an option symbol
EXCHANGE = "NSE"
POLL_SECONDS = 5              # 12 historical calls/min  (limit is 60/min)
CANDLE_SECONDS = 60           # 1-minute candles
STRAT_TAG = "bmsit-flip-demo" # docs: ONE tag, hyphens only (no underscores/spaces/colons)
PAISE = 100                   # NSE prices are integer PAISE: 12550 paise = Rs 125.50
CANDLE_SYMBOL = UNDERLYING    # what we subscribe to / fetch candles for (set by resolve_candle_source)
CANDLE_TYPE = "INDEX"         # historical_data type for the candles


def resolve_candle_source(instruments):
    """On NSE the index itself has candles ("NIFTY"). On MCX the underlying is a futures contract:
    the stream key is the nearest FUT symbol (verified on UAT: "CRUDEOIL" delivers nothing,
    "FUT_CRUDEOIL_20261019" delivers 1m candles)."""
    global CANDLE_SYMBOL, CANDLE_TYPE
    if EXCHANGE == "NSE":
        CANDLE_SYMBOL, CANDLE_TYPE = UNDERLYING, "INDEX"
        return
    df = instruments.get_instruments_dataframe(exchange=EXCHANGE)
    fut = df[(df["derivative_type"] == "FUT") & (df["asset"] == UNDERLYING)].sort_values("expiry")
    if fut.empty:
        raise RuntimeError(f"no FUT contract for {UNDERLYING} on {EXCHANGE} in the instruments master")
    CANDLE_SYMBOL, CANDLE_TYPE = str(fut.iloc[0]["stock_name"]), "FUT"
    log(f"candle source on {EXCHANGE}: {CANDLE_SYMBOL} (nearest future of {UNDERLYING})")

Candle = namedtuple("Candle", "ts open high low close")   # ts = seconds since epoch (UTC), prices in paise


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def log(msg):
    """One clear line per event, with a wall-clock stamp."""
    print(f"[{now_str()}] {msg}", flush=True)


def rupees(paise):
    return f"Rs {paise / PAISE:,.2f}"


def ist(ts_seconds):
    """Pretty IST time for a UTC epoch (IST = UTC + 5:30)."""
    return (datetime.fromtimestamp(ts_seconds, tz=timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%H:%M")


# --------------------------------------------------------------------------- the strategy logic
def colour(c: Candle) -> str:
    if c.close > c.open:
        return "GREEN"
    if c.close < c.open:
        return "RED"
    return "FLAT"


class FlipDetector:
    """
    Remembers the last two completed candles.  update(candle) returns:
        "CE"  -> buy the call  (previous RED, newest GREEN)
        "PE"  -> buy the put   (previous GREEN, newest RED)
        None  -> no flip
    """
    def __init__(self):
        self.prev = None
        self.last = None

    def update(self, candle: Candle):
        self.prev, self.last = self.last, candle
        if self.prev is None:
            return None
        before, now = colour(self.prev), colour(self.last)
        if before == "RED" and now == "GREEN":
            return "CE"
        if before == "GREEN" and now == "RED":
            return "PE"
        return None


# --------------------------------------------------------------------------- candles from the SDK
def _utc(dt):
    """Docs: startDate/endDate must be UTC strings like 2025-04-19T11:01:57.000Z"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_candles(md, intraday: bool, lookback_days: int = 7):
    """
    historical_data() for NIFTY, 1-minute candles  (docs: Historical Market Data).
    intraday=True  -> only today's candles (what the live loop wants)
    intraday=False -> include past days (what --replay wants; sub-daily keeps ~3 months)
    Returns a list of Candle sorted by time, prices in paise (exchange-native integers).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    resp = md.historical_data({
        "exchange": EXCHANGE,
        "type": CANDLE_TYPE,
        "values": [CANDLE_SYMBOL],
        "fields": ["open", "high", "low", "close"],
        "startDate": _utc(start),
        "endDate": _utc(end),
        "interval": "1m",
        "intraDay": intraday,
        "realTime": False,          # docs: "To be declared" -> always pass False
    })
    candles = {}
    if resp is None or not getattr(resp, "result", None):
        return []
    # Docs access pattern: result[].values is a list of {symbol: StockChart}
    for chart in resp.result:
        for item in chart.values or []:
            for _symbol, series in item.items():
                # Each StockChart field is a list of TimeSeriesPoint(timestamp ns, value int)
                opens = {p.timestamp: p.value for p in (series.open or [])}
                highs = {p.timestamp: p.value for p in (series.high or [])}
                lows = {p.timestamp: p.value for p in (series.low or [])}
                for p in (series.close or []):
                    t = p.timestamp
                    if t in opens:
                        candles[t] = Candle(t // 1_000_000_000, opens[t], highs.get(t, 0), lows.get(t, 0), p.value)
    return [candles[t] for t in sorted(candles)]


def completed_only(candles, now_seconds=None):
    """
    Drop a candle that may still be forming.  The docs do not say whether the historical
    timestamp is the bucket START or END, so we are conservative: if the newest candle's
    timestamp is less than 60 s old we assume it is still being built and ignore it for now.
    (Worst case we act up to 60 s late, never on an unfinished candle.)
    """
    now_seconds = now_seconds or time.time()
    return [c for c in candles if now_seconds - c.ts >= CANDLE_SECONDS]


def synthetic_candles(n=60, start_price=25000 * PAISE):
    """Random-walk candles for a no-login logic test.  Clearly NOT market data."""
    rng = random.Random(42)
    out, price, t = [], start_price, int(time.time()) - n * CANDLE_SECONDS
    for i in range(n):
        o = price
        c = o + rng.randint(-1500, 1500)           # +- Rs 15 per minute
        hi, lo = max(o, c) + rng.randint(0, 500), min(o, c) - rng.randint(0, 500)
        out.append(Candle(t + i * CANDLE_SECONDS, o, hi, lo, c))
        price = c
    return out


# --------------------------------------------------------------------------- option picking
class OptionPicker:
    """
    Finds "the ATM CE/PE of the nearest expiry" using the documented option_chain() flow:
        chain = market_data.option_chain("NIFTY", exchange="NSE").chain
        chain.all_expiries      -> pick the nearest (strings 'YYYYMMDD')
        chain.at_the_money_strike
        row = next(o for o in chain.ce if o.strike_price == chain.at_the_money_strike)
        row.ref_id, row.lot_size, row.last_traded_price   (price in paise)
    tick_size comes from the instruments master (docs: carry ref_id, tick_size, lot_size).
    """
    def __init__(self, md, instruments):
        self.md = md
        self.instruments = instruments

    def nearest_expiry(self):
        chain = self.md.option_chain(UNDERLYING, exchange=EXCHANGE).chain
        expiries = sorted(str(e) for e in (chain.all_expiries or []))
        if not expiries:
            raise RuntimeError("option_chain returned no expiries for NIFTY")
        # Keep only expiries that are today or later (strings sort like dates in YYYYMMDD).
        today = datetime.now().strftime("%Y%m%d")
        future = [e for e in expiries if e >= today]
        return (future or expiries)[0]

    def pick(self, option_type: str):
        """option_type is 'CE' or 'PE'. Returns a dict describing the leg."""
        expiry = self.nearest_expiry()
        chain = self.md.option_chain(UNDERLYING, expiry=expiry, exchange=EXCHANGE).chain
        atm = chain.at_the_money_strike
        rows = chain.ce if option_type == "CE" else chain.pe
        row = next((o for o in rows if o.strike_price == atm), None)
        if row is None:
            # Fall back to the strike closest to the underlying price.
            row = min(rows, key=lambda o: abs(o.strike_price - (chain.current_price or 0)))
        inst = self.instruments.get_instrument_by_ref_id(row.ref_id, exchange=EXCHANGE)
        if isinstance(inst, dict):              # SDK returns {"msg": ...} when not found
            raise RuntimeError(f"ref_id {row.ref_id} from the chain is not in the instruments master: {inst}")
        return {
            "ref_id": int(row.ref_id),
            "symbol": inst.stock_name,
            "lot_size": int(row.lot_size or inst.lot_size),
            "tick_size": int(inst.tick_size),
            "chain_ltp": int(row.last_traded_price or 0),
            "strike": int(row.strike_price),
            "expiry": expiry,
            "type": option_type,
        }


# --------------------------------------------------------------------------- orders
def round_to_tick(price_paise: int, tick_size: int) -> int:
    """
    Docs: entryPrice is an integer (paise) and must be a multiple of tick_size, or the
    exchange rejects it with 'The entered price is not a multiple of the tick size'.
    """
    tick = max(int(tick_size), 1)
    return int(round(price_paise / tick) * tick)


class Broker:
    """Real orders through NubraTrader (docs: Place Single Order + Get Orders)."""
    def __init__(self, nubra, md):
        from nubra_python_sdk.trading.trading_data import NubraTrader
        self.trader = NubraTrader(nubra)
        self.md = md

    def ltp(self, leg):
        """Docs place-order pattern: quote(ref_id, levels=1).orderBook.last_traded_price"""
        try:
            q = self.md.quote(ref_id=leg["ref_id"], levels=1)
            if q and q.orderBook and q.orderBook.last_traded_price:
                return int(q.orderBook.last_traded_price)
        except Exception as e:
            log(f"quote() failed ({type(e).__name__}: {e}); using option-chain LTP instead")
        return leg["chain_ltp"]

    def place(self, side: str, leg: dict, qty: int):
        price = round_to_tick(self.ltp(leg), leg["tick_size"])
        if price <= 0:
            log(f"SKIP {side}: no traded price for {leg['symbol']} (market closed? UAT stale?)")
            return None
        # The V3 payload exactly as the docs' "Limit Order" example shows.
        payload = {
            "refId": leg["ref_id"],
            "qty": qty,
            "side": side,                   # BUY or SELL
            "deliveryType": "IDAY",         # intraday product
            "priceType": "LIMIT",
            "validityType": "DAY",
            "isMultiLeg": False,            # single-instrument order
            "executionMode": "ENTRY",       # plain limit entry (a square-off SELL is also a plain
                                            # order; "EXIT" mode is for exit-trigger payloads only)
            "entryPrice": price,            # integer paise, multiple of tick_size
            "stratTags": [STRAT_TAG],       # one hyphenated tag
        }
        log(f"ORDER SENT {side} {qty} x {leg['symbol']} LIMIT @ {rupees(price)} "
            f"(strike {leg['strike'] / PAISE:.0f} {leg['type']}, expiry {leg['expiry']})")
        try:
            result = self.trader.create_order(payload)
        except Exception as e:
            log(f"ORDER FAILED: {type(e).__name__}: {e}")
            return None
        order = result.orders[0] if getattr(result, "orders", None) else None
        if order is None:
            log(f"ORDER RESPONSE had no orders: {result}")
            return None
        log(f"ORDER ID {order.intentOrderId} status {order.status}"
            + (f" ({order.rejectionMsg})" if order.rejectionMsg else ""))
        self.read_back(order.intentOrderId)
        return order.intentOrderId

    def read_back(self, intent_order_id: int):
        """Docs Get Orders: get_order(intentOrderId) -> list of order objects."""
        try:
            time.sleep(0.5)                 # give the OMS a moment
            found = self.trader.get_order(int(intent_order_id))
            if found:
                o = found[0]
                log(f"ORDER STATUS (get_order) {o.intentOrderId} -> {o.status}, "
                    f"filled {o.filledQty}/{o.orderQty}"
                    + (f", reason: {o.rejectionMsg}" if o.rejectionMsg else ""))
            else:
                # Alternative documented route: filter by strategy tag.
                grouped = self.trader.orders(strat_tags=STRAT_TAG)
                buckets = {k: len(v) for k, v in (grouped.orders or {}).items()} if grouped else {}
                log(f"ORDER STATUS (orders by tag '{STRAT_TAG}') buckets: {buckets}")
        except Exception as e:
            log(f"could not read order back: {type(e).__name__}: {e}")


class FakeBroker:
    """Prints what WOULD be sent. Used by --dry-run and --synthetic."""
    def __init__(self):
        self.counter = 1000

    def place(self, side, leg, qty):
        price = round_to_tick(leg["chain_ltp"], leg["tick_size"])
        self.counter += 1
        log(f"[DRY-RUN] ORDER {side} {qty} x {leg['symbol']} LIMIT @ {rupees(price)} "
            f"(strike {leg['strike'] / PAISE:.0f} {leg['type']}, expiry {leg['expiry']}) -> fake id {self.counter}")
        return self.counter


class FakePicker:
    """Stand-in for OptionPicker when there is no login (--synthetic)."""
    def pick(self, option_type):
        return {"ref_id": 0, "symbol": f"NIFTY-SYNTH-25000{option_type}", "lot_size": 75,
                "tick_size": 5, "chain_ltp": 12345, "strike": 25000 * PAISE,
                "expiry": "SYNTHETIC", "type": option_type}


# --------------------------------------------------------------------------- the trader loop
class FlipTrader:
    """Glues detector + picker + broker together and tracks the open leg in memory."""
    def __init__(self, picker, broker, lots: int, squareoff: bool):
        self.detector = FlipDetector()
        self.picker = picker
        self.broker = broker
        self.lots = lots
        self.squareoff = squareoff
        self.open_leg = None          # dict(leg=..., qty=...) or None
        self.signals = 0

    def on_candle(self, candle: Candle):
        prev_colour = colour(self.detector.last) if self.detector.last else "-"
        signal = self.detector.update(candle)
        line = (f"CANDLE {ist(candle.ts)} IST  open {candle.open / PAISE:.2f}  close {candle.close / PAISE:.2f}  "
                f"{colour(candle)} (prev {prev_colour})")
        if signal is None:
            log(line + "  no flip")
            return
        log(line + f"  FLIP DETECTED -> buy ATM {signal}")
        self.signals += 1
        self.trade(signal)

    def trade(self, option_type):
        # 1) square off the previous leg bought by THIS script (tracked in memory only)
        if self.open_leg and self.squareoff:
            old = self.open_leg
            log(f"SQUARE OFF old leg {old['leg']['symbol']}")
            self.broker.place("SELL", old["leg"], old["qty"])
            self.open_leg = None
            time.sleep(0.2)            # stay far below 100 ops/s
        elif self.open_leg:
            log(f"--no-squareoff: keeping {self.open_leg['leg']['symbol']} open")
        # 2) buy the new ATM leg of the nearest expiry
        try:
            leg = self.picker.pick(option_type)
        except Exception as e:
            log(f"could not pick option: {type(e).__name__}: {e}")
            return
        qty = self.lots * leg["lot_size"]
        log(f"LEG {leg['symbol']} lot_size {leg['lot_size']} x {self.lots} lots = qty {qty}")
        order_id = self.broker.place("BUY", leg, qty)
        if order_id is not None:
            self.open_leg = {"leg": leg, "qty": qty, "order_id": order_id}


def run_poll(md, trader: FlipTrader, max_signals):
    """LIVE, default: poll today's 1-minute candles every POLL_SECONDS."""
    log(f"LIVE (poll): historical_data NIFTY 1m intraDay=True every {POLL_SECONDS}s. Ctrl+C to stop.")
    seen_ts = None
    first = True
    while True:
        try:
            candles = completed_only(fetch_candles(md, intraday=True))
        except Exception as e:
            log(f"historical_data failed: {type(e).__name__}: {e}")
            candles = []
        if candles:
            if first:
                # Seed the detector with the last two completed candles WITHOUT trading,
                # so the very first new candle can already be compared.
                for c in candles[-2:]:
                    trader.detector.update(c)
                seen_ts = candles[-1].ts
                log(f"seeded with {len(candles)} candles today; last completed {ist(seen_ts)} IST "
                    f"({colour(candles[-1])}). Waiting for the next 1-minute candle...")
                first = False
            else:
                for c in candles:
                    if c.ts > seen_ts:
                        trader.on_candle(c)
                        seen_ts = c.ts
        elif first:
            log("no completed candles yet (market not open, or UAT has no intraday data right now)")
        if max_signals and trader.signals >= max_signals:
            log("max signals reached, stopping")
            return
        time.sleep(POLL_SECONDS)


def run_stream(nubra, trader: FlipTrader, max_signals):
    """LIVE (default): realtime OHLCV WebSocket (docs: Realtime Data -> OHLCV Data)."""
    from nubra_python_sdk.ticker import websocketdata
    done = queue.Queue()           # completed candles travel from the socket thread to here
    state = {"bucket": None, "last": None}

    def on_ohlcv_data(msg):
        # msg fields (docs): indexname, interval, open/high/low/close, bucket_timestamp (start), timestamp (close)
        if msg.bucket_timestamp is None or msg.close is None:
            return
        if state["bucket"] is not None and msg.bucket_timestamp != state["bucket"] and state["last"] is not None:
            done.put(state["last"])            # the previous bucket is now complete
        state["bucket"] = msg.bucket_timestamp
        ts = msg.bucket_timestamp // 1_000_000_000 if msg.bucket_timestamp > 10**12 else msg.bucket_timestamp
        state["last"] = Candle(ts, msg.open, msg.high or 0, msg.low or 0, msg.close)

    socket = websocketdata.NubraDataSocket(
        client=nubra,
        on_ohlcv_data=on_ohlcv_data,
        on_connect=lambda m: log(f"socket: {m}"),
        on_close=lambda r: log(f"socket closed: {r}"),
        on_error=lambda e: log(f"socket error: {e}"),
    )
    socket.connect()
    socket.subscribe([CANDLE_SYMBOL], data_type="ohlcv", interval="1m", exchange=EXCHANGE)
    log(f"LIVE (stream): subscribed {CANDLE_SYMBOL} ohlcv 1m on {EXCHANGE}. A candle counts as complete when the next bucket starts. Ctrl+C to stop.")
    try:
        while True:
            try:
                trader.on_candle(done.get(timeout=1))
            except queue.Empty:
                pass
            if max_signals and trader.signals >= max_signals:
                log("max signals reached, stopping")
                return
    finally:
        try:
            socket.close()
        except Exception:
            pass


def run_replay(candles, trader: FlipTrader, max_signals, delay=1.0):
    """REHEARSAL: feed candles one per second so flips happen quickly."""
    log(f"REPLAY: {len(candles)} candles, one per {delay:.0f}s. Expect several flips.")
    for c in candles:
        trader.on_candle(c)
        if max_signals and trader.signals >= max_signals:
            log("max signals reached, stopping")
            return
        time.sleep(delay)
    log("replay finished")


# --------------------------------------------------------------------------- main
def main():
    global UNDERLYING, EXCHANGE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="store_true", help="feed the last 60 historical 1-min candles at 1/s")
    ap.add_argument("--synthetic", action="store_true", help="no login: random candles + fake broker")
    ap.add_argument("--dry-run", action="store_true", help="print orders instead of sending them")
    ap.add_argument("--source", choices=["stream", "poll"], default="stream", help="live candle source (default: WebSocket stream)")
    ap.add_argument("--no-squareoff", action="store_true", help="do not sell the old leg before buying the new one")
    ap.add_argument("--lots", type=int, default=10)
    ap.add_argument("--underlying", default=UNDERLYING, help="underlying for the option chain (default NIFTY; e.g. CRUDEOIL for an MCX rehearsal)")
    ap.add_argument("--exchange", default=EXCHANGE, help="exchange of the underlying (default NSE; MCX for commodities)")
    ap.add_argument("--max-signals", type=int, default=0, help="stop after N flips (0 = never)")
    a = ap.parse_args()
    UNDERLYING, EXCHANGE = a.underlying.upper(), a.exchange.upper()

    squareoff = not a.no_squareoff

    if a.synthetic:
        log("SYNTHETIC mode: random candles, fake option, fake orders. Nothing touches Nubra.")
        trader = FlipTrader(FakePicker(), FakeBroker(), a.lots, squareoff)
        run_replay(synthetic_candles(60), trader, a.max_signals, delay=0.05 if a.max_signals else 1.0)
        return

    # ---- real SDK objects (docs: initialise once, reuse) ----
    from common_login import get_client
    from nubra_python_sdk.marketdata.market_data import MarketData
    from nubra_python_sdk.refdata.instruments import InstrumentData
    nubra = get_client()
    md = MarketData(nubra)
    instruments = InstrumentData(nubra)
    resolve_candle_source(instruments)
    picker = OptionPicker(md, instruments)
    broker = FakeBroker() if a.dry_run else Broker(nubra, md)
    trader = FlipTrader(picker, broker, a.lots, squareoff)
    log(f"lots={a.lots}  squareoff={squareoff}  orders={'DRY-RUN' if a.dry_run else 'REAL (UAT sandbox)'}")

    if a.replay:
        candles = completed_only(fetch_candles(md, intraday=False, lookback_days=7))[-60:]
        if len(candles) < 2:
            sys.exit("replay needs at least 2 historical candles; got none (check UAT data / dates)")
        log(f"replay window {ist(candles[0].ts)} .. {ist(candles[-1].ts)} IST")
        run_replay(candles, trader, a.max_signals)
    elif a.source == "poll":
        run_poll(md, trader, a.max_signals)
    else:
        run_stream(nubra, trader, a.max_signals)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped by user (Ctrl+C). Any open leg stays open in UAT; square it off from the app if needed.")
