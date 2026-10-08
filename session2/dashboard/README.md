# Screener dashboard (Streamlit)

Repo structure, five lines:

1. `app.py` - the page: table, z-score bars, one price chart with mean and +-2 std bands, Refresh button. Only draws.
2. `data.py` - where candles come from: `load_cached()` reads `../cache/*.csv`; `refresh_live()` re-fetches from Nubra PROD (data login) via `../screener.py`; `load_live()` reads `../cache/_live.csv`, the table the screener's live step rewrites every 2 s.
3. `indicators.py` - the maths only: rolling mean, std, +-2 bands, z-score, the ranked table.
4. `requirements.txt` - what to `pip install` (nubra-sdk, pandas, streamlit, altair, python-dotenv).
5. `../cache/` - the CSV data the dashboard reads (built by `../build_cache.py`); keep data out of the code folder.

Run (from `session2/`):

```powershell
..\data\.venv\Scripts\python.exe -m streamlit run dashboard\app.py
```

If the cache is empty: `python build_cache.py` (needs login) or `python build_cache.py --synthetic` (fake data, no login).
