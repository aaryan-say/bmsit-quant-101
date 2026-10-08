"""Example 4 (intraday): if the first 15-minute candle of the day closes green, stay long for the day.

TIMEFRAME tells the engine to load data/15m (bars of 09:15, 09:30, ... 15:15 IST). The signal on the
09:15 bar is executed at the 09:30 open (next bar), and we set the position to 0 on the 15:00 bar so the
exit happens at the 15:15 open: no overnight risk, flat before the day's last bar.
The time of day is read from the clock (df['datetime']); never use shift(-1) to find "the last bar".
Run:  python backtest.py strategies\\first_green_15m.py
"""
import pandas as pd

TIMEFRAME = "15m"
EXIT_MINUTE = 15 * 60          # set position 0 at the 15:00 bar -> executed at the 15:15 open


def strategy(df: pd.DataFrame) -> pd.Series:
    minute = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    first_bar = minute == 9 * 60 + 15
    green_open = first_bar & (df["close"] > df["open"])             # first candle closed up
    pos = green_open.astype(float).groupby(df["date"]).cummax()      # 1 from that bar to the end of the day
    pos[minute >= EXIT_MINUTE] = 0.0                                 # flat into the close
    return pos
