# The prompt: paste everything below the line into ChatGPT, then add your idea at the bottom

Copy from `-----` to the end of this file, paste it into ChatGPT (or any LLM), replace the last line
with YOUR strategy idea, save the reply as `strategies\<short_name>.py`, and run
`python backtest.py strategies\<short_name>.py`.

-----

You are writing ONE Python file for a small, strict daily-bar backtesting engine. Follow this contract
exactly; the file must plug in with zero edits.

## Data you receive
`strategy(df)` is called once per stock (NIFTY 50 names, about 250 trading days) with a pandas DataFrame
`df` sorted by date ascending, default RangeIndex (0..n-1), columns:

| column | type | meaning |
|---|---|---|
| `date` | datetime64 | IST trading day |
| `open`, `high`, `low`, `close` | float | rupees |
| `volume` | int | shares traded |
| `nifty_close` | float (optional) | NIFTY index close that day; may be absent, check `'nifty_close' in df.columns` |

## What you must return
A `pd.Series` of floats with the SAME index as `df` (same length, same order). Each value is the position
you want to hold for that stock after that day's close:

- `+1.0` = fully long, `0.0` = flat, `-1.0` = fully short, fractions allowed (e.g. `0.5`); anything outside
  [-1, 1] is clipped.
- NaN is treated as flat (0), so NaNs from rolling warm-up are fine.
- The engine executes your position at the NEXT day's open. You never need to shift the result yourself.
- Every symbol gets equal capital; costs are Rs 20 per order + 0.05% slippage per position change, so
  flipping every day will lose money. Fewer, better trades win.

## Hard rules
1. Only `pandas` and `numpy` (and the Python standard library). No other imports, no file or network access.
2. Use ONLY past rows when computing the value for a row: `rolling(...)`, `ewm(...)`, `expanding()`,
   `shift(n)` with n >= 1, `cumsum()`, `diff()`, `pct_change()`, `ffill()`.
   NEVER `shift(-n)`, `rolling(center=True)`, `bfill()`, `iloc[i+1]`, whole-sample statistics
   (`df['close'].mean()`, `.max()`, `.rank()`, `.quantile()` over the full column), or anything that looks
   at a later row. The engine runs a causality check and rejects files that fail it.
3. Do not change `df`'s index, do not drop rows, do not `set_index('date')`, do not sort.
   Adding helper columns to `df` is fine (the engine gives you a copy).
4. Pure function: no global state, no randomness (or `np.random.seed(0)` if you must), no printing.
5. Must be fast: vectorised pandas, or at most ONE simple `for` loop over rows for stateful entry/exit logic.
6. Define constants (window lengths, thresholds) at the top of the file so they are easy to tweak.

## Helper patterns (copy these)
```python
sma = df["close"].rolling(20).mean()
ema = df["close"].ewm(span=20, adjust=False).mean()
std = df["close"].rolling(20).std()
zscore = (df["close"] - sma) / std
ret = df["close"].pct_change()
prev_close = df["close"].shift(1)
highest_prev_20 = df["high"].rolling(20).max().shift(1)   # excludes today
delta = df["close"].diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rsi = 100 - 100 / (1 + gain / loss)
vol_avg = df["volume"].rolling(20).mean()
```
Stateful entry/exit (hold until a condition) is allowed with one loop:
```python
pos = np.zeros(len(df)); holding = False
for i in range(len(df)):
    if not holding and entry_condition.iloc[i]: holding = True
    elif holding and exit_condition.iloc[i]: holding = False
    pos[i] = 1.0 if holding else 0.0
return pd.Series(pos, index=df.index)
```

## Exact file skeleton
```python
"""<one line: the idea in plain English>"""
import numpy as np
import pandas as pd

WINDOW = 20          # constants at the top


def strategy(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    # ... indicators built only from past rows ...
    pos = pd.Series(0.0, index=df.index)
    # ... set pos to +1 / 0 / -1 ...
    return pos
```

## Three short examples (idea -> code)
Idea: "long when the 20-day average is above the 50-day average, short otherwise"
```python
def strategy(df):
    fast, slow = df["close"].rolling(20).mean(), df["close"].rolling(50).mean()
    pos = pd.Series(0.0, index=df.index)
    pos[fast > slow] = 1.0
    pos[fast < slow] = -1.0
    return pos
```
Idea: "buy when RSI(14) drops below 30, sell when it goes back above 50; long only"
```python
def strategy(df):
    d = df["close"].diff()
    rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + rs)
    pos = np.zeros(len(df)); holding = False
    for i in range(len(df)):
        if not holding and rsi.iloc[i] < 30: holding = True
        elif holding and rsi.iloc[i] > 50: holding = False
        pos[i] = 1.0 if holding else 0.0
    return pd.Series(pos, index=df.index)
```
Idea: "only go long on a 20-day breakout if NIFTY itself is above its 50-day average; half size otherwise"
```python
def strategy(df):
    breakout = df["close"] > df["high"].rolling(20).max().shift(1)
    pos = breakout.astype(float)
    if "nifty_close" in df.columns:
        strong_market = df["nifty_close"] > df["nifty_close"].rolling(50).mean()
        pos = pos.where(strong_market, pos * 0.5)
    return pos
```

## Output format
Return ONLY the content of the Python file. No prose, no explanation, no markdown fences, no "Here is".
The first line must be the docstring; the file must define `strategy(df)`.

## My strategy idea
<REPLACE THIS LINE with your idea in one or two sentences, e.g. "go long when today's volume is twice
the 20-day average and the close is up; exit after the close falls below the 10-day average">

-----

# How to iterate (do this at least 3 times inside the 25-minute box)

1. `python backtest.py strategies\mine.py` and READ the scorecard: Sharpe, max drawdown, trades, exposure.
2. Ask one question: too many trades (costs eating you)? too few (nothing to judge)? huge drawdown?
3. Change ONE thing (a window, a threshold, long-only, add a filter). Re-run. Keep it only if Sharpe
   improved without the trade count collapsing below 10.
4. If the engine says LOOK-AHEAD DETECTED, you are using future rows. Fix it, do not bypass it.
5. Submit the SUBMIT line of your best run in the form. Keep the file: the presenter re-runs the top
   five on hidden data, and only honest strategies survive that.
