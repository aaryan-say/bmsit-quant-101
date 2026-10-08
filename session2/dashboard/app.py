"""
app.py  -  a minimal Streamlit dashboard for the mean-reversion screener.

Run from the session2 folder (so the cache path resolves):
    cd session2
    ..\\data\\.venv\\Scripts\\python.exe -m streamlit run dashboard\\app.py

What is on the page
    1. the ranked screener table (most negative z first)
    2. a bar chart of z-scores, coloured by label (LOW / normal / HIGH)
    3. one price chart for a selected stock: close, 20-day mean, +-2 std bands
    4. a "Refresh" button: re-reads the cache, or (tick the box) re-fetches from Nubra PROD (data login)
    5. if  python screener.py  is running its live step, the live table it writes (cache/_live.csv)

Everything heavy lives in data.py (loading) and indicators.py (maths); this file only draws.
"""
import altair as alt
import pandas as pd
import streamlit as st

import data
import indicators

# --- colours: one fixed, colour-blind-checked set (blue = low, grey = normal, red = high) ---
C_LOW, C_NORMAL, C_HIGH = "#2a78d6", "#898781", "#e34948"
C_CLOSE, C_MEAN, C_BAND = "#2a78d6", "#eb6834", "#898781"

st.set_page_config(page_title="Mean-reversion screener", layout="wide")
st.title("Mean-reversion screener (Nubra PROD data, daily candles)")

# ------------------------------------------------------------------ data + refresh button
col_a, col_b = st.columns([1, 3])
with col_a:
    live = st.checkbox("Re-fetch from Nubra PROD on refresh (needs the data login)", value=False)
    if st.button("Refresh"):
        st.cache_data.clear()
        st.session_state["refresh_live"] = live
        st.rerun()


@st.cache_data(show_spinner="loading candles...")
def get_frames(use_live: bool):
    return data.refresh_live() if use_live else data.load_cached()


frames, meta = get_frames(st.session_state.pop("refresh_live", False))
if not frames:
    st.error("No cached data. In session2/ run  python build_cache.py  (login) or  python build_cache.py --synthetic")
    st.stop()

with col_b:
    st.caption(f"{len(frames)} stocks | source: {meta.get('source', '?')} | built: {meta.get('built_at', '?')}")
    if meta.get("source") == "SYNTHETIC":
        st.warning("SYNTHETIC data: random numbers for practice, not real prices.")

# ------------------------------------------------------------------ 1) table
table = indicators.screener_table(frames)
live_table, live_age = data.load_live()
if live_table is not None and live_age is not None and live_age < 30:
    # the screener's CHECKPOINT 5 is running right now: show ITS table (live closes), re-read on each rerun
    table = live_table[table.columns.intersection(live_table.columns)]
    st.subheader(f"LIVE ranked table (from screener.py, {live_age:.0f} s old; press R to rerun)")
else:
    st.subheader("Ranked table (most negative z first)")
st.dataframe(table.style.format({"last_close": "{:,.2f}", "mean20": "{:,.2f}", "std20": "{:,.2f}",
                                 "z": "{:+.2f}", "ret20_pct": "{:+.2f}%"}),
             width="stretch", hide_index=True)

# ------------------------------------------------------------------ 2) z-score bars
st.subheader("z-score by stock")
bars = (alt.Chart(table)
        .mark_bar(size=18, cornerRadiusEnd=4)
        .encode(x=alt.X("z:Q", title="z-score (negative = below its 20-day mean)"),
                y=alt.Y("symbol:N", sort=table["symbol"].tolist(), title=None),
                color=alt.Color("label:N", title="label",
                                scale=alt.Scale(domain=["stretched LOW (z < -2)", "normal", "stretched HIGH (z > 2)"],
                                                range=[C_LOW, C_NORMAL, C_HIGH])),
                tooltip=["symbol", alt.Tooltip("z:Q", format="+.2f"), alt.Tooltip("last_close:Q", format=",.2f"),
                         alt.Tooltip("ret20_pct:Q", format="+.2f", title="20d return %"), "label"]))
rules = alt.Chart(pd.DataFrame({"x": [-2, 2]})).mark_rule(color=C_BAND, strokeDash=[4, 4]).encode(x="x:Q")
st.altair_chart((bars + rules).properties(height=28 * len(table) + 40), width="stretch")

# ------------------------------------------------------------------ 3) one stock: price + mean + bands
st.subheader("Price with 20-day mean and +-2 std bands")
symbol = st.selectbox("stock", table["symbol"].tolist(), index=0)
df = indicators.add_bands(frames[symbol]).reset_index()
long = df.melt(id_vars="date", value_vars=["close", "mean20", "upper", "lower"], var_name="series", value_name="rupees")
names = {"close": "close", "mean20": "20-day mean", "upper": "+2 std", "lower": "-2 std"}
long["series"] = long["series"].map(names)
line = (alt.Chart(long.dropna())
        .mark_line(strokeWidth=2)
        .encode(x=alt.X("date:T", title=None),
                y=alt.Y("rupees:Q", title="Rs", scale=alt.Scale(zero=False)),
                color=alt.Color("series:N", title=None,
                                scale=alt.Scale(domain=list(names.values()), range=[C_CLOSE, C_MEAN, C_BAND, C_BAND])),
                strokeDash=alt.condition("datum.series == '+2 std' || datum.series == '-2 std'",
                                         alt.value([4, 4]), alt.value([1, 0])),
                tooltip=[alt.Tooltip("date:T"), "series", alt.Tooltip("rupees:Q", format=",.2f")]))
st.altair_chart(line.properties(height=360).interactive(), width="stretch")
row = table[table["symbol"] == symbol].iloc[0]
st.caption(f"{symbol}: last close Rs {row['last_close']:,.2f}, mean20 Rs {row['mean20']:,.2f}, "
           f"std20 Rs {row['std20']:,.2f}, z = {row['z']:+.2f}  ->  {row['label']}")
