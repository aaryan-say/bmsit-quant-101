"""5-minute opening-range breakout (9:15-9:30) with VWAP and 20-bar volume confirmation, 1R stop, 2R target, flat at 15:00.
Student-submitted test strategy (ChatGPT-generated from PROMPT.md), kept as a worked intraday example."""
import numpy as np
import pandas as pd

TIMEFRAME = "5m"

OR_END = 9 * 60 + 30
FLAT_TIME = 15 * 60
VOLUME_WINDOW = 20
VOLUME_MULTIPLIER = 1.5
MAX_RISK_PCT = 0.008
REWARD_RISK = 2.0


def strategy(df: pd.DataFrame) -> pd.Series:
    minute = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    date = df["date"]

    opening_high = df["high"].where(minute < OR_END).groupby(date).cummax().groupby(date).ffill()
    opening_low = df["low"].where(minute < OR_END).groupby(date).cummin().groupby(date).ffill()

    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_pv = (typical_price * df["volume"]).groupby(date).cumsum()
    cum_volume = df["volume"].groupby(date).cumsum()
    vwap = cum_pv / cum_volume.replace(0, np.nan)
    avg_volume = df["volume"].rolling(VOLUME_WINDOW).mean().shift(1)

    active = (minute >= OR_END) & (minute < FLAT_TIME)
    vol_ok = df["volume"] >= VOLUME_MULTIPLIER * avg_volume
    long_breakout = active & (df["close"] > opening_high) & (df["close"] > vwap) & vol_ok
    short_breakout = active & (df["close"] < opening_low) & (df["close"] < vwap) & vol_ok

    pos = np.zeros(len(df), dtype=float)
    holding, direction, stop, target, current_day = False, 0, np.nan, np.nan, None
    lo, hi, cl = df["low"].to_numpy(), df["high"].to_numpy(), df["close"].to_numpy()
    lb, sb, mn, dt = long_breakout.to_numpy(), short_breakout.to_numpy(), minute.to_numpy(), date.to_numpy()
    for i in range(len(df)):
        if current_day is not None and dt[i] != current_day:
            holding, direction = False, 0
        current_day = dt[i]
        if mn[i] >= FLAT_TIME:
            holding, direction = False, 0
            pos[i] = 0.0
            continue
        if holding:
            if direction == 1 and (lo[i] <= stop or hi[i] >= target):
                holding, direction = False, 0
            elif direction == -1 and (hi[i] >= stop or lo[i] <= target):
                holding, direction = False, 0
        if not holding:
            if lb[i]:
                risk = cl[i] - lo[i]
                if risk > 0 and risk / cl[i] <= MAX_RISK_PCT:
                    stop, target, holding, direction = lo[i], cl[i] + REWARD_RISK * risk, True, 1
            elif sb[i]:
                risk = hi[i] - cl[i]
                if risk > 0 and risk / cl[i] <= MAX_RISK_PCT:
                    stop, target, holding, direction = hi[i], cl[i] - REWARD_RISK * risk, True, -1
        pos[i] = float(direction) if holding else 0.0
    return pd.Series(pos, index=df.index)
