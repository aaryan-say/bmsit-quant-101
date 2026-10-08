"""
indicators.py  -  the maths, and nothing else.  (Same formulas as ../screener.py CHECKPOINT 3.)

Keeping the maths in its own small file means the dashboard (app.py) and the data
loader (data.py) never have to know HOW a z-score is computed, only that it exists.
"""
import pandas as pd

WINDOW = 20
Z_LIMIT = 2.0


def add_bands(df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """
    Add rolling columns to a daily-candle frame (needs a 'close' column in rupees):
        mean20 : rolling 20-day average of close           (the "centre")
        std20  : rolling 20-day standard deviation          (the "normal wobble")
        upper  : mean20 + 2 * std20                          (+2 band)
        lower  : mean20 - 2 * std20                          (-2 band)
        z      : (close - mean20) / std20                    (how many wobbles away)
    The first 19 rows have no window yet, so they are NaN; that is expected.
    """
    out = df.copy()
    roll = out["close"].rolling(window)
    out["mean20"] = roll.mean()
    out["std20"] = roll.std()                 # pandas default: sample std (divide by n-1)
    out["upper"] = out["mean20"] + Z_LIMIT * out["std20"]
    out["lower"] = out["mean20"] - Z_LIMIT * out["std20"]
    out["z"] = (out["close"] - out["mean20"]) / out["std20"]
    return out


def label_for(z: float) -> str:
    if z < -Z_LIMIT:
        return "stretched LOW (z < -2)"
    if z > Z_LIMIT:
        return "stretched HIGH (z > 2)"
    return "normal"


def summarise(symbol: str, df: pd.DataFrame, window: int = WINDOW) -> dict:
    """One row of the screener table for one stock."""
    close = df["close"].dropna()
    if len(close) < window + 1:
        return None
    last20 = close.tail(window)
    mean20, std20 = last20.mean(), last20.std()
    last_close = close.iloc[-1]
    z = (last_close - mean20) / std20 if std20 > 0 else 0.0
    ret20 = (last_close / close.iloc[-1 - window] - 1.0) * 100.0
    return {"symbol": symbol, "last_close": round(last_close, 2), "mean20": round(mean20, 2),
            "std20": round(std20, 2), "z": round(z, 2), "ret20_pct": round(ret20, 2), "label": label_for(z)}


def screener_table(frames: dict) -> pd.DataFrame:
    """{symbol: candles} -> ranked table, most negative z first."""
    rows = [r for r in (summarise(s, df) for s, df in frames.items()) if r]
    table = pd.DataFrame(rows)
    return table.sort_values("z").reset_index(drop=True) if not table.empty else table
