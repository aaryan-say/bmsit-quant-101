"""Example 2: z-score mean reversion (long only).

Idea: z = (close - 20-day mean) / 20-day std. When z < -2 the stock is "unusually cheap" versus its
recent range, so go long. Hold until z crosses back above 0 (the price returned to its average).
Positions are STATEFUL (we hold until an exit), so we loop over rows; the loop only looks backwards.
Run:  python backtest.py strategies\\zscore_meanrev.py
"""
import numpy as np
import pandas as pd

WINDOW = 20
ENTRY_Z = -2.0
EXIT_Z = 0.0


def strategy(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    mean = close.rolling(WINDOW).mean()
    std = close.rolling(WINDOW).std()
    z = (close - mean) / std                       # NaN during warm-up (first 19 rows)
    pos = np.zeros(len(df))
    holding = False
    for i in range(len(df)):
        zi = z.iloc[i]
        if np.isnan(zi):
            continue                               # not enough history yet -> stay flat
        if not holding and zi < ENTRY_Z:
            holding = True                         # enter long
        elif holding and zi > EXIT_Z:
            holding = False                        # exit when price is back at its mean
        pos[i] = 1.0 if holding else 0.0
    return pd.Series(pos, index=df.index)
