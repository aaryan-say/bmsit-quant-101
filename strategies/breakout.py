"""Example 3: 20-day channel breakout (Donchian style).

Idea: if today's close is above the highest high of the PREVIOUS 20 days, buyers are in control -> long.
If it is below the lowest low of the previous 20 days -> short. Otherwise keep yesterday's position.
Note the .shift(1): the channel is built from rows BEFORE today, so today's bar cannot be its own breakout.
Run:  python backtest.py strategies\\breakout.py
"""
import pandas as pd

WINDOW = 20


def strategy(df: pd.DataFrame) -> pd.Series:
    upper = df["high"].rolling(WINDOW).max().shift(1)   # highest high of the previous 20 days
    lower = df["low"].rolling(WINDOW).min().shift(1)    # lowest low of the previous 20 days
    signal = pd.Series(float("nan"), index=df.index)
    signal[df["close"] > upper] = 1.0                   # breakout up -> long
    signal[df["close"] < lower] = -1.0                  # breakdown -> short
    return signal.ffill().fillna(0.0)                   # keep the last signal until a new one (ffill looks backward)
