# Session 2 code kit - Nubra Python SDK: PROD for data, UAT for orders

Two environments, on purpose:

- **`NubraEnv.PROD`** is used **read-only, for market data**: the daily candles and the live WebSocket
  stream. UAT holds almost no price history (some stocks have a single candle), so a 20-day z-score
  needs PROD data.
- **`NubraEnv.UAT`** (the sandbox) is where **every order** goes. Practice money. Nothing in this folder
  ever hands a PROD client to `NubraTrader`.

Every SDK call is copied from the official docs (`nubra-api-python-sdk-v3-llm-builder (20).md`,
SDK 0.5.x); the comments in each file say which page.

## Files

| File | Who | What |
|---|---|---|
| `common_login.py` | everyone | `get_prod_client()` -> PROD client for data; `get_client()` -> UAT client for orders. Run it alone as a login test. |
| `strategy_flip.py` | presenter demo | NIFTY 1-minute "candle colour flip" over the OHLCV WebSocket: red->green buys ATM CE, green->red buys ATM PE, 10 lots, squares off the old leg first. UAT. |
| `screener.py` | student project | 15-stock mean-reversion screener in 7 CHECKPOINTS: history, maths, ranked table, **live index stream**, risk rules, one optional UAT order on a z cross-down. |
| `build_cache.py` | presenter (night before) | Saves PROD daily candles to `cache/*.csv` so `screener.py --offline` and the dashboard work without a login. |
| `dashboard/app.py` | project bonus | Streamlit page: table, z-score bars, price + mean + bands; shows the screener's live table while it runs. See `dashboard/README.md`. |

Python: reuse the existing venv `..\data\.venv` (Python 3.12, nubra-sdk 0.5.4, pandas, streamlit, altair).

## Logins and .env files

Copy `.env.example` and fill `PHONE_NO` / `MPIN` (the key names the SDK reads with `env_creds=True`):

| Environment | `.env` lives in | token file (written by the SDK) | needed for |
|---|---|---|---|
| PROD (data) | `session2\prod\.env` *(or, on the presenter's machine, `..\data\.env`)* | `session2\prod\auth_data.db*` *(or `..\data\auth_data.db*`)* | `screener.py`, `build_cache.py`, dashboard refresh |
| UAT (orders) | `session2\.env` | `session2\auth_data.db*` | `screener.py --trade`, `strategy_flip.py` |

`get_prod_client()` picks automatically: if `..\data\` already holds a PROD session (presenter), it reuses
`..\data\common.py`; otherwise it uses `session2\prod\`. The SDK keeps one token file per working
directory, which is why the two environments live in two folders and never mix.

Never commit `.env`, never show it. The OTP is always typed by you, the first time only, in a real
terminal. On **every** start the SDK re-verifies the saved token with your MPIN (from `.env` or typed),
and asks for an OTP again if the token expired. The UAT and PROD MPINs can differ.

No PROD login at all? `screener.py --offline` runs checkpoints 2, 3, 4 and 6 from `cache\` (built from PROD).

## How to run each file

Open PowerShell in this folder:

```powershell
cd C:\Users\Aryan\Desktop\BMSIT_WorkShop_Deck\session2
$py = "..\data\.venv\Scripts\python.exe"

& $py common_login.py                       # login test: PROD (data) then UAT (orders); OTP on first use
& $py common_login.py --prod-only           # just the data login

& $py build_cache.py                        # presenter: ~70 PROD daily candles x 14 stocks -> cache\
& $py build_cache.py --synthetic            # anyone: fake practice data (clearly labelled), no login

& $py screener.py --offline                 # no login: CHECKPOINTS 2-4 + 6 from cache
& $py screener.py                           # PROD data: candles, maths, table, then 60 s of LIVE updates
& $py screener.py --watch 0                 # live step runs until Ctrl+C
& $py screener.py --trade                   # + UAT login: ONE sandbox LIMIT BUY when a live z crosses DOWN through -2
& $py screener.py --trade --trade-now       # presenter rehearsal: skip the crossing, try the most negative z now

& $py strategy_flip.py --synthetic          # no login: random candles, fake orders (see the logic)
& $py strategy_flip.py --replay --dry-run   # UAT login, last 60 real 1-min candles at 1/s, orders printed
& $py strategy_flip.py --replay             # same, REAL UAT orders (rehearsal)
& $py strategy_flip.py                      # LIVE demo: 1-min candles over the OHLCV WebSocket, UAT orders
& $py strategy_flip.py --source poll        # fallback only: poll historical_data every 5 s
#   extra flags: --no-squareoff  --lots 10  --max-signals 3

& $py -m streamlit run dashboard\app.py     # dashboard at http://localhost:8501
```

## The screener's seven checkpoints

1. **Login**: PROD for data (`get_prod_client()`); UAT only when `--trade` is passed.
2. **Daily candles** for one stock: `historical_data`, interval `1d`, `intraDay=False`, paise / 100.
3. **The maths**: mean20, std20, z = (close - mean20) / std20, 20-day return, label.
4. **The watchlist**: batches of 5, 1.1 s apart (60 calls/min), ranked table (most negative z first).
5. **Live**: `socket.subscribe(symbols, data_type="index", exchange="NSE")` on the PROD client
   (weight 1 per symbol, 15 of the 50,000 budget). On every message the symbol's *today* close becomes
   `index_value / 100` (replacing today's forming candle, or appended as today's row when the history
   ends yesterday), z is recomputed and the table is reprinted at most every 2 s. The table is also
   written to `cache\_live.csv` for dashboards. Outside 09:15-15:30 IST the stream is silent: the
   script prints "no updates yet" and keeps the last table.
6. **Risk rules**: one order per run, qty 1, only z < -2, never a stretched high; every refusal prints why.
7. **One UAT order** (`--trade`): fires only when a stock's live z crosses **down** through -2 (was >= -2
   on the previous refresh, now < -2). LIMIT BUY at the LTP (UAT `quote()`, falling back to the stream
   price) rounded to `tick_size`, `get_margin()` first, then `get_order()` to read the status back.
   `--trade-now` skips the crossing for rehearsals; the risk rules still decide.

## Candle source for the demo (WebSocket)

The demo subscribes to the realtime OHLCV stream: `socket.subscribe(["NIFTY"], data_type="ohlcv",
interval="1m", exchange="NSE")`. The stream sends the *current* candle as it builds; the moment a message
arrives with a new `bucket_timestamp`, the previous candle is complete and the strategy acts on it. No
polling, no artificial delay. `--source poll` (historical_data every 5 s, 60 s completion guard) exists only
as a fallback if the venue network blocks WebSockets.

## The five classic mistakes (and the fix)

1. **Paise vs rupees.** Every NSE price from the SDK (`price`, `last_traded_price`, candle `open/close`,
   stream `index_value`, option `strike_price`, `entryPrice`) is an **integer in paise**. 250000 = Rs 2500.00.
   Divide by 100 to show, multiply by 100 to send. The order price must also be a **multiple of `tick_size`**.
2. **`ref_id` is per environment.** UAT and PROD have different `ref_id`s. Never hardcode one; resolve it
   with `InstrumentData(...).get_instrument_by_symbol(...)` or from `option_chain()` in the same environment
   (the screener resolves the order's `ref_id` in UAT, even though the data came from PROD).
3. **UTC dates.** `startDate`/`endDate` are UTC strings like `2025-04-19T11:01:57.000Z`; timestamps come
   back as **nanoseconds** since epoch (`pd.to_datetime(ts, unit="ns", utc=True)`), convert to
   `Asia/Kolkata` to get the Indian trading day.
4. **Cumulative volume.** `cumulative_volume` is a running total within the day. For daily candles use it
   as-is; for intraday intervals `.diff()` it to get per-candle volume.
5. **`intraDay` flag.** `intraDay=True` = **only today** (startDate/endDate ignored). To include past
   days, even for 1-minute candles, use `intraDay=False`. Sub-daily history reaches back ~3 months.

Bonus: `stratTags` takes **one** tag, hyphens only (`"bmsit-screener"`); MARKET orders pair with
`validityType: "IOC"` and no `entryPrice`; `keep_running()` blocks, so the screener polls a dict the
socket thread fills instead.

## Volunteer cheat-sheet (one page)

**Before students arrive**
- Presenter: `..\data\` has the PROD session (`..\data\auth.py`) and `..\data\.env` with MPIN;
  `session2\.env` has the UAT login. Run `common_login.py` once in a terminal (both logins, OTPs).
- Run `build_cache.py` so `cache\` has real CSVs and `_meta.json` says `NUBRA-PROD`.
- Run `screener.py --offline`, then `screener.py --watch 10`, then `strategy_flip.py --replay --dry-run`.
  All three must print without errors. Delete `cache\_live.csv` if you want a clean start.
- Rehearse the order step: `screener.py --trade --trade-now` (one UAT order if the most negative z < -2).

**Student logins: which one do they need?**
- Checkpoints 2-4 and 6: none (`--offline`), or a PROD login for fresh candles.
- Checkpoint 5 (live): a PROD login (`session2\prod\.env` + OTP once). Read-only; no static IP needed for data.
- Checkpoint 7 (`--trade`): a UAT login as well (`session2\.env`). Presenter demos this from the big screen;
  students opt in only if they have UAT.
- No login at all: `screener.py --offline` and the dashboard, or the finished project from the GitHub link.

**Student cannot log in**
- "No session2/prod/.env ...": copy `.env.example` to `session2\prod\.env`, fill PROD phone + MPIN, run in a real terminal.
- "Missing PHONE_NO / MPIN": `.env` is missing or has empty values; copy `.env.example`.
- "MPIN verification failed": wrong MPIN for THAT environment (UAT and PROD can differ), or the saved token
  belongs to the other environment. Delete that folder's `auth_data.db*` and log in again.
- Prompt hangs in VS Code's output pane: run in a real terminal (the SDK uses `input()`).
- Many students on one Wi-Fi share one IP: historical limit is 60/min per IP. Stagger runs or use `--offline`.
- "IP address mismatch ... trading access is restricted" on the PROD login: expected, it only blocks PROD
  trading, which nobody does today. Data and the stream still work.

**Reading errors**
- `Exception in get_instruments` / every symbol `NOT IN INSTRUMENTS MASTER` -> the master is briefly
  unavailable; the screener carries on with the names as written and `historical_data` still works.
- `400 ticker not found` for a whole batch -> one bad symbol sinks the batch; the screener retries one by
  one and reports the bad name (TATAMOTORS was delisted after the demerger).
- `The entered price is not a multiple of the tick size` -> round `entryPrice` to `tick_size` (paise).
- `Client error: 4xx` from `create_order` -> read the message; usually a bad `refId`, wrong env, or
  the account lacks the V3/OMS flag (docs: "Sentinel/OMS flag must be enabled").
- `Instrument not found` dict from `get_instrument_by_symbol` -> wrong symbol (ETERNAL vs ZOMATO).
- Empty candles -> market closed with `intraDay=True`, or dates not UTC, or `type` mismatch (STOCK vs INDEX).
- Live table never changes -> market closed (index stream ticks 09:15-15:30 IST); that is fine, the
  table from checkpoint 4 stays on screen.

**Live-demo risks to know**
- After 15:30 IST no new 1-minute candles arrive: use `--replay` to force flips. The screener's live step
  will be silent too; rehearse the order with `--trade-now`.
- UAT option chain / quotes can be stale or zero outside market hours; both scripts refuse to send a
  blind price (the screener falls back to the live stream price when UAT's quote is empty).
- Expiries are `YYYYMMDD` strings in `chain.all_expiries`; the script picks the nearest date >= today.
- The flip script tracks its own open leg in memory only. If you Ctrl+C mid-demo, square off in the Nubra app.
- 10 lots x NIFTY lot size is a large notional; it is sandbox money, say so out loud.
- Both scripts stream over WebSocket by default; if the venue blocks it, `strategy_flip.py --source poll`.
