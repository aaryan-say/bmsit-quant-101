"""Example 1: moving-average crossover (trend following).

Idea: when the fast 20-day average is above the slow 50-day average the stock is trending up, so be long.
When it is below, be short (the engine flips this to flat if you run with --long-only).
Run:  python backtest.py strategies\\ma_crossover.py
"""
import pandas as pd

FAST = 20
SLOW = 50


def strategy(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    fast = close.rolling(FAST).mean()     # average of the LAST 20 closes, including today
    slow = close.rolling(SLOW).mean()     # NaN for the first 49 rows -> the engine treats NaN as flat
    pos = pd.Series(0.0, index=df.index)
    pos[fast > slow] = 1.0                # long when fast above slow
    pos[fast < slow] = -1.0               # short when fast below slow
    return pos                            # the engine executes this at TOMORROW's open
