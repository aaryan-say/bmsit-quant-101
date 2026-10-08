# bmsit-quant-101: build and backtest a trading strategy in 25 minutes

A tiny, honest backtesting kit for the BMSIT quant workshop (Session 3). You describe a strategy in
English, ChatGPT turns it into a 20-line Python file, this engine runs it across the NIFTY 50 stocks
with realistic costs and next-day execution, and prints a scorecard you can submit.

Needs only Python 3.10+ and `pip install pandas matplotlib`. Works on Windows, mac and Linux.
No account, no login, no API key: the price data is already in the repo.

## Quick start (4 steps)

1. **Get the repo.** `git clone <repo-url>` or click *Code -> Download ZIP* on GitHub and unzip it.
2. **Install.** Open a terminal in the folder and run `pip install pandas matplotlib`
   (Windows: or just double-click `run.bat`, which creates a virtual env, installs, and runs the example).
3. **Prompt.** Open `PROMPT.md`, copy everything below the first `-----` into ChatGPT, replace the last
   line with your idea, and save the reply as `strategies\mine.py` (any name you like).
4. **Run.** `python backtest.py strategies\mine.py`. Read the scorecard, look at
   `results\mine.png`, change one thing, run again. When you are happy, paste the `SUBMIT | ...` line
   into the form.

Try the examples first: `python backtest.py strategies\ma_crossover.py`, then `python backtest.py --all`
for a leaderboard of every file in `strategies\`.

Useful flags: `--long-only` (no shorts), `--start 2026-01-01 --end 2026-06-30` (score a window),
`--no-plot`, `--json`, `--data <folder>`. `python backtest.py -h` lists them.

## Timeframes: daily or intraday

Your strategy file may declare `TIMEFRAME = "1d"` (the default when absent), `"1h"`, `"30m"`, `"15m"`,
`"5m"` or `"1m"`. The engine then loads that folder (`data\daily` for 1d, `data\<tf>` otherwise), executes
at the next *bar's* open, and annualises Sharpe/CAGR using bars per year (246 trading days x bars per day).
The scorecard and the SUBMIT line show the timeframe; the hidden re-run uses the same one automatically
(`--data data\hidden` picks `data\hidden\<tf>`).

When intraday makes sense: ideas about the opening range, time of day, "flat before the close", or
reacting within the day. When it does not: anything built on multi-day trends or averages, which is
most ideas. Two facts to keep in mind:

- Intraday data starts **2025-09-04** (that is as far back as the data source keeps sub-daily candles),
  so you get about 200 days instead of 250.
- More bars = more trades = costs matter much more. A 15-minute strategy that enters and exits daily on
  all 52 stocks makes ~5,000 round trips in ten months; at Rs 20 + 0.05% a side that is a large chunk of
  a Rs 19,000 sleeve. If the scorecard warns that sleeves "went to zero", costs ate the capital.
  See `strategies\first_green_15m.py` for an intraday example that shows exactly this.

## The competition

- **Time box: 25 minutes** from "go". Iterate as many times as you like inside it.
- **Submit** your best SUBMIT line plus your `.py` file here: **[Google Form link - TBD]**.
- **Judged on out-of-sample Sharpe.** The data you have ends 2026-06-30. The presenter holds a hidden
  set for **July to September 2026**. The top 5 by submitted Sharpe are re-run with
  `python backtest.py strategies\x.py --data data\hidden` (same timeframe as the file declares) and
  ranked by the Sharpe on those three months. A strategy needs **at least 10 trades** in the hidden
  window to be ranked.
- Strategies that use future data are rejected by the engine (`LOOK-AHEAD DETECTED`) and by the judges.
- Shorts are allowed (the engine assumes you can short any NIFTY 50 stock, which in real life means
  futures or intraday). If you would rather not, run with `--long-only`.

## What the engine does (so you can trust it)

- **Universe:** every `<SYMBOL>.csv` in the timeframe's folder (NIFTY 50 constituents; daily OHLCV for
  one year to 2026-06-30, intraday from 2025-09-04). `NIFTY` is the index: never traded, drawn as a
  reference line, and offered to your strategy as the optional `nifty_close` column.
- **Equal weight:** Rs 10,00,000 split equally across symbols; each symbol's sleeve compounds on its own.
  A sleeve stops at zero (it cannot go negative); the scorecard warns when that happens.
- **Next-bar execution:** your position for a row is computed from that bar's data and executed at the
  *next* bar's open. Equity is marked at each open. There is no way to earn this bar's move from this
  bar's signal. (`tests\test_engine.py` proves this with a planted price jump.)
- **Costs:** Rs 20 per order plus 0.05% slippage on the traded value, charged on every position change.
  Over-trading shows up immediately in the scorecard.
- **No leverage:** positions are clipped to [-1, 1]; NaN means flat.
- **Guards:** the strategy gets a *copy* of the data (mutating it is harmless), must return a Series on
  the same index, and is run twice (full history vs. the first two thirds) - if any past position changes
  when future rows are removed, the file is rejected as look-ahead.

### How the metrics are computed

**Total return** is final portfolio value divided by starting value, minus one, after all costs.

**CAGR** (compound annual growth rate) converts total return to a per-year rate using the number of
bars in the test and the bars per year for that timeframe (246 for daily; for intraday, the median
number of bars per trading day times 246), so a 3-month and a 12-month test are comparable. On short
windows it exaggerates both good and bad results.

**Max drawdown** is the worst peak-to-trough fall of the portfolio value, in percent. It answers "how much
would I have been down at the worst moment if I started at the previous high?".

**Sharpe ratio** is the mean per-bar portfolio return divided by the standard deviation of per-bar
returns, multiplied by sqrt(bars per year) to annualise it (risk-free rate taken as 0; the scorecard
prints the factor used). Roughly: return per unit of wobble. Above 1 over a year is good; above 3 on
daily stock data is suspicious. Intraday Sharpe values swing more in both directions because a small
consistent per-bar edge (or cost) gets multiplied by sqrt(6,000+).

**Win rate** is the share of closed round-trip trades (entry to exit on one symbol) that made money after
costs. A low win rate is fine if winners are much bigger than losers.

**Trades** counts round trips across all symbols. Fewer than 10 means the result is mostly luck.

**Exposure** is the percentage of symbol-days on which you held a position. 100% means always in the
market; 10% means you are mostly in cash, so compare returns with that in mind.

**Buy & hold** is the same equal-weight universe bought at the first open and held, with the same costs:
the benchmark your strategy has to beat.

## The five ways backtests lie (and what this engine does about them)

1. **Look-ahead bias:** using information you would not have had at the time. *Engine:* next-day
   execution, a causality check that re-runs your code on a truncated history, and the hidden
   out-of-sample set. The test suite shows that a strategy peeking one day ahead (`shift(-1)`) scores a
   Sharpe of about 70 and a CAGR of thousands of percent on the public data; that is why the final
   ranking uses data you never saw.
2. **Overfitting:** tuning windows and thresholds until the past looks great. *Engine:* does not stop
   you. The 25-minute box, the "change one thing" discipline, and the hidden set are the defences.
   If Sharpe jumps from 0.5 to 2.5 after a parameter tweak, assume you fitted noise.
3. **Survivorship bias:** testing on today's index members, which by definition did well enough to be in
   the index. *Engine:* does **not** address it; the universe is the current NIFTY 50. Treat absolute
   returns as optimistic.
4. **Costs and slippage:** ignoring them makes high-frequency flipping look profitable. *Engine:* charges
   Rs 20 per order plus 0.05% slippage on every change; the tests confirm costs reduce returns.
   Real costs can be higher (impact, taxes, STT); real fills can be worse than the open.
5. **Data snooping:** trying 50 ideas and reporting the best one, which would have beaten the benchmark
   by chance. *Engine:* cannot see what you tried. The hidden set and the 10-trade minimum reduce (not
   remove) the effect. Prefer an idea with a reason behind it over one that merely scored well.

Other caveats: prices are unadjusted for splits/bonuses, execution at the exact open is optimistic, and
shorting cash equities overnight is not possible for retail investors (you would use futures).

## Data

```
data\daily\<SYMBOL>.csv        1d, 2025-07-01 .. 2026-06-30         (public)
data\1h\<SYMBOL>.csv.gz        1h,  2025-09-04 .. 2026-06-30        (public; pandas reads .gz directly)
data\30m\ data\15m\ data\5m\   same layout                           (public)
data\1m\<SYMBOL>.csv.gz        1m, ~75,000 rows per symbol           (NOT in git: shipped as a separate zip,
                                                                       unzip into data\1m\)
data\hidden\...                presenter only, git-ignored: same symbols to 2026-09-30, incl. hidden\<tf>\
```
Columns are `date,open,high,low,close,volume`; intraday files have a leading `datetime` column (IST bar
start). Every folder's `_SOURCE.txt` says where the prices came from: `SYNTHETIC` means random walks
generated by `scripts\make_synthetic.py` for testing the pipeline; `NUBRA-PROD` means real NSE candles
fetched with `scripts\fetch_data.py --interval <tf>` through the Nubra Python SDK (presenter only,
needs a login). Both produce identical file names and columns, so nothing else changes.

## Bring your strategy to the repo

Found something that holds up? Open a pull request that adds ONE file, `strategies\community\<yourname>_<idea>.py`,
following the `PROMPT.md` contract, with a docstring that states the idea and the SUBMIT line you got.
It must run with `python backtest.py strategies\community\<file>.py`. `--all` runs that folder too, so
the leaderboard grows with every merge.

## Options backtesting (advanced)

This kit trades stocks on daily bars. For options strategies (delta-neutral straddles/strangles on NIFTY,
Greeks, expiry handling) see the presenter's engine: `pip install nubra-backtest-engine` and the repo
https://github.com/aaryan-say/nifty-options-delta-neutral-backtest.

## Repo layout

```
backtest.py              the engine (one file, ~350 lines, read it)
PROMPT.md                paste into ChatGPT with your idea
strategies/              ma_crossover.py, zscore_meanrev.py, breakout.py, first_green_15m.py, community/
data/daily/, data/<tf>/  public data per timeframe + _SOURCE.txt (data/1m/ ships as a zip)
data/hidden/             presenter only, git-ignored (out-of-sample, per timeframe)
scripts/                 make_synthetic.py, fetch_data.py (presenter), universe.py
tests/test_engine.py     python tests\test_engine.py   or   python -m pytest
results/                 created on each run: <name>.png, <name>_symbols.csv
run.bat                  Windows one-click: venv + install + run
```

## Data cleaning (what was done to the intraday files)

The raw intraday feed had two problems the daily series does not: a handful of spike bars with
garbage prices (for example MARUTI at Rs 5.50 on 2025-11-06 10:00, and a batch of bad opens at
09:15 on 2026-01-05), and no adjustment for corporate actions (KOTAKBANK's split on 2026-01-14,
TRENT's on 2026-06-04) while the daily series is adjusted. `scripts\clean_data.py` fixes both:

- drops bars whose close is more than 40% away from both neighbours; repairs opens, highs and lows
  that are more than 40% away from the bar's own close;
- rescales every intraday day so its last close matches the adjusted daily close whenever the two
  differ by more than 3% (volume scaled the other way);
- keeps only the regular session, 09:15 to 15:29 IST (the feed also returns pre-open and
  post-close bars). `backtest.py` applies the same session filter when it loads data.

Run it after any fresh `fetch_data.py` for an intraday interval. Sanity check used: an always-long
strategy on 15m data must return about the same as buy-and-hold of the daily series.
