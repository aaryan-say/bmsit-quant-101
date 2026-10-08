"""The trading universe shared by make_synthetic.py and fetch_data.py.

Each entry is a tuple of candidate NSE trading symbols in preference order. fetch_data.py tries them
against the Nubra instruments master and uses the FIRST one that resolves (e.g. ZOMATO was renamed
ETERNAL in 2025). The CSV file is named after the symbol that resolved.
"""

NIFTY50 = [
    ("RELIANCE",), ("HDFCBANK",), ("ICICIBANK",), ("INFY",), ("TCS",), ("SBIN",), ("BHARTIARTL",),
    ("ITC",), ("LT",), ("KOTAKBANK",), ("AXISBANK",), ("HINDUNILVR",), ("BAJFINANCE",), ("MARUTI",),
    ("SUNPHARMA",), ("M&M",), ("NTPC",), ("TITAN",), ("ULTRACEMCO",), ("ONGC",), ("TATAMOTORS",),
    ("POWERGRID",), ("ADANIENT",), ("ADANIPORTS",), ("BAJAJFINSV",), ("ASIANPAINT",), ("JSWSTEEL",),
    ("TATASTEEL",), ("COALINDIA",), ("NESTLEIND",), ("HCLTECH",), ("WIPRO",), ("TECHM",), ("GRASIM",),
    ("HINDALCO",), ("DRREDDY",), ("CIPLA",), ("APOLLOHOSP",), ("EICHERMOT",), ("BAJAJ-AUTO",),
    ("HEROMOTOCO",), ("BRITANNIA",), ("TATACONSUM",), ("INDUSINDBK",), ("SBILIFE",), ("HDFCLIFE",),
    ("BPCL",), ("SHRIRAMFIN",), ("TRENT",), ("BEL",), ("ETERNAL", "ZOMATO"), ("JIOFIN",),
]

# The index is stored as data/daily/NIFTY.csv. The engine does NOT trade it; it is plotted as a
# reference line and offered to strategies as the helper column `nifty_close`.
INDEX_SYMBOL = "NIFTY"

# Date ranges used by both generators (inclusive, IST trading days).
PUBLIC_START = "2025-07-01"
PUBLIC_END = "2026-06-30"      # students see data up to here
HIDDEN_START = "2026-07-01"    # out-of-sample window used for judging
HIDDEN_END = "2026-09-30"
INTRADAY_START = "2025-09-04"  # Nubra PROD keeps sub-daily candles only from this date (verified 2026-10-08)

# Timeframes. Folder layout:  1d -> data/daily (public) + data/hidden (presenter), plain .csv
#                             others -> data/<tf>/ (public) + data/hidden/<tf>/ (presenter), .csv.gz
INTERVALS = ["1d", "1h", "30m", "15m", "5m", "1m"]
BAR_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}   # NSE session 09:15-15:30 = 375 minutes
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_MINUTES = 375


def data_folders(interval, root):
    """(public folder, hidden folder, file extension) for a timeframe."""
    import os
    if interval == "1d":
        return os.path.join(root, "data", "daily"), os.path.join(root, "data", "hidden"), ".csv"
    return os.path.join(root, "data", interval), os.path.join(root, "data", "hidden", interval), ".csv.gz"

# Rough price levels (rupees) so synthetic charts look plausible. Only used by make_synthetic.py.
APPROX_PRICE = {
    "RELIANCE": 1450, "HDFCBANK": 1950, "ICICIBANK": 1400, "INFY": 1550, "TCS": 3400, "SBIN": 800,
    "BHARTIARTL": 1900, "ITC": 420, "LT": 3500, "KOTAKBANK": 2100, "AXISBANK": 1150, "HINDUNILVR": 2400,
    "BAJFINANCE": 900, "MARUTI": 12500, "SUNPHARMA": 1700, "M&M": 3100, "NTPC": 340, "TITAN": 3400,
    "ULTRACEMCO": 11500, "ONGC": 245, "TATAMOTORS": 700, "POWERGRID": 300, "ADANIENT": 2600,
    "ADANIPORTS": 1400, "BAJAJFINSV": 2000, "ASIANPAINT": 2400, "JSWSTEEL": 1000, "TATASTEEL": 160,
    "COALINDIA": 390, "NESTLEIND": 2400, "HCLTECH": 1700, "WIPRO": 260, "TECHM": 1600, "GRASIM": 2800,
    "HINDALCO": 680, "DRREDDY": 1300, "CIPLA": 1500, "APOLLOHOSP": 7200, "EICHERMOT": 5500,
    "BAJAJ-AUTO": 8500, "HEROMOTOCO": 4300, "BRITANNIA": 5700, "TATACONSUM": 1100, "INDUSINDBK": 850,
    "SBILIFE": 1800, "HDFCLIFE": 780, "BPCL": 330, "SHRIRAMFIN": 650, "TRENT": 5600, "BEL": 400,
    "ETERNAL": 260, "JIOFIN": 320, "NIFTY": 25500,
}
