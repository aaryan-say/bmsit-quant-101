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

## The competition

- **Time box: 25 minutes** from "go". Iterate as many times as you like inside it.
- **Submit** your best SUBMIT line plus your `.py` file here: **[Google Form link - TBD]**.
- **Judged on out-of-sample Sharpe.** The data you have ends 2026-06-30. The presenter holds a hidden
  set for **July to September 2026**. The top 5 by submitted Sharpe are re-run with
  `python backtest.py strategies\x.py --data data\hidden` and ranked by the Sharpe on those three
  months. A strategy needs **at least 10 trades** in the hidden window to be ranked.
- Strategies that use future data are rejected by the engine (`LOOK-AHEAD DETECTED`) and by the judges.
- Shorts are allowed (the engine assumes you can short any NIFTY 50 stock, which in real life means
  futures or intraday). If you would rather not, run with `--long-only`.

## What the engine does (so you can trust it)

- **Universe:** every `data\daily\<SYMBOL>.csv` (NIFTY 50 constituents, daily OHLCV, one year to
  2026-06-30). `NIFTY.csv` is the index: never traded, drawn as a reference line, and offered to your
  strategy as the optional `nifty_close` column.
- **Equal weight:** Rs 10,00,000 split equally across symbols; each symbol's sleeve compounds on its own.
- **Next-day execution:** your position for a row is computed from that day's data and executed at the
  *next* day's open. Equity is marked at each open. There is no way to earn today's move from today's
  signal. (`tests\test_engine.py` proves this with a planted price jump.)
- **Costs:** Rs 20 per order plus 0.05% slippage on the traded value, charged on every position change.
  Over-trading shows up immediately in the scorecard.
- **No leverage:** positions are clipped to [-1, 1]; NaN means flat.
- **Guards:** the strategy gets a *copy* of the data (mutating it is harmless), must return a Series on
  the same index, and is run twice (full history vs. the first two thirds) - if any past position changes
  when future rows are removed, the file is rejected as look-ahead.

### How the metrics are computed

**Total return** is final portfolio value divided by starting value, minus one, after all costs.

**CAGR** (compound annual growth rate) converts total return to a per-year rate using the number of
calendar days in the test, so a 3-month and a 12-month test are comparable. On short windows it
exaggerates both good and bad results.

**Max drawdown** is the worst peak-to-trough fall of the portfolio value, in percent. It answers "how much
would I have been down at the worst moment if I started at the previous high?".

**Sharpe ratio** is the mean daily portfolio return divided by the standard deviation of daily returns,
multiplied by sqrt(252) to annualise it (risk-free rate taken as 0). Roughly: return per unit of
wobble. Above 1 over a year is good; above 3 on daily stock data is suspicious.

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

`data\daily\_SOURCE.txt` says where the prices came from. `SYNTHETIC` means random walks generated by
`scripts\make_synthetic.py` for testing the pipeline; `NUBRA-PROD` means real NSE daily candles fetched
with `scripts\fetch_data.py` through the Nubra Python SDK (presenter only, needs a login). Both produce
identical file names and columns (`date,open,high,low,close,volume`), so nothing else changes.

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
backtest.py              the engine (one file, ~300 lines, read it)
PROMPT.md                paste into ChatGPT with your idea
strategies/              ma_crossover.py, zscore_meanrev.py, breakout.py, community/
data/daily/              <SYMBOL>.csv public data + _SOURCE.txt
data/hidden/             presenter only, git-ignored (out-of-sample)
scripts/                 make_synthetic.py, fetch_data.py (presenter), universe.py
tests/test_engine.py     python tests\test_engine.py   or   python -m pytest
results/                 created on each run: <name>.png, <name>_symbols.csv
run.bat                  Windows one-click: venv + install + run
```
