"""Brokerage pipeline & revenue dashboard. Run: streamlit run app.py"""

import os
import tempfile
import time
from collections import deque
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

import queries as q
from warehouse import Warehouse

st.set_page_config(page_title="Brokerage Pipeline", page_icon="🏙️", layout="wide")
TEAL, INK, MUTED = "#0F766E", "#1C1917", "#A8A29E"
QUERY_LOG: deque = deque(maxlen=25)

st.markdown(
    """<style>
    .block-container {padding-top: 3.5rem; max-width: 1400px;}
    [data-testid="stMetric"] {background: #fff; border: 1px solid #E7E5E4; border-radius: 10px; padding: 14px 18px;}
    [data-testid="stMetricValue"] {font-size: 1.9rem; font-weight: 650; letter-spacing: -0.02em;}
    .eyebrow {font-size: .78rem; letter-spacing: .12em; text-transform: uppercase; color: #0F766E; font-weight: 650;}
    .conn {font-family: ui-monospace, Menlo, monospace; font-size: .8rem; color: #57534E;}
    </style>""",
    unsafe_allow_html=True,
)


def cloud_params() -> dict | None:
    """Snowflake settings from Streamlit secrets ([snowflake] table) when deployed; None locally."""
    try:
        cfg = dict(st.secrets["snowflake"])
    except Exception:  # noqa: BLE001 - no secrets file / no [snowflake] table -> use local connections.toml
        return None
    if "private_key" in cfg:  # connector wants a key file; keep it private to this process
        fd, path = tempfile.mkstemp(suffix=".p8")
        with os.fdopen(fd, "w") as fh:
            fh.write(cfg.pop("private_key"))
        os.chmod(path, 0o600)
        cfg["private_key_file"] = path
    return cfg


@st.cache_resource(show_spinner="Connecting to the warehouse…")
def get_wh() -> Warehouse:
    wh = Warehouse(params=cloud_params())
    raw_query = wh.query

    def logged_query(sql: str, params=()):  # record every statement that actually hits the warehouse
        started = time.perf_counter()
        df = raw_query(sql, params)
        QUERY_LOG.appendleft((" ".join(sql.split()), (time.perf_counter() - started) * 1000, len(df)))
        return df

    wh.query = logged_query
    return wh


@st.cache_data(ttl=300, show_spinner=False)
def run(name: str, f: q.Filters | None = None, *extra) -> pd.DataFrame | pd.Series | dict:
    fn = getattr(q, name)
    return fn(get_wh()) if f is None else fn(get_wh(), f, *extra)


def money(x: float) -> str:
    return f"${x / 1e6:.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:.0f}K" if abs(x) >= 1e3 else f"${x:,.0f}"


def delta(now: float, before: float) -> str | None:
    return f"{(now - before) / before:+.1%}" if before else None


try:
    wh = get_wh()
    opts = run("options")
except Exception as e:  # noqa: BLE001 - surface config problems clearly instead of a stack trace
    st.error(f"Could not reach the warehouse: {e}")
    st.info("Check ~/.snowflake/connections.toml, run `python sync.py`, or use `WAREHOUSE=duckdb` for local mode.")
    st.stop()

# ---------- Sidebar filters ----------
with st.sidebar:
    st.markdown('<div class="eyebrow">Filters</div>', unsafe_allow_html=True)
    default_start = max(opts["min_date"], (opts["max_date"] - timedelta(days=150)).replace(day=1))  # whole months
    picked = st.date_input("Lead created", (default_start, opts["max_date"]),
                           min_value=opts["min_date"], max_value=opts["max_date"])
    start, end = picked if isinstance(picked, tuple) and len(picked) == 2 else (default_start, opts["max_date"])
    hoods = st.multiselect("Neighborhood", opts["neighborhoods"], placeholder="All neighborhoods")
    sources = st.multiselect("Lead source", opts["sources"], placeholder="All sources")
    teams = st.multiselect("Team", opts["teams"], placeholder="All teams")
    st.divider()
    if st.button("↻ Refresh from warehouse", width="stretch"):
        st.cache_data.clear()
        st.rerun()

f = q.Filters(start, end, tuple(hoods), tuple(sources), tuple(teams))

# ---------- Header ----------
left, right = st.columns([3, 2], vertical_alignment="bottom")
left.markdown('<div class="eyebrow">Chicago rentals · sales analytics</div>', unsafe_allow_html=True)
left.title("Pipeline & Revenue")
right.markdown(
    f'<div class="conn" style="text-align:right">{wh.describe()}<br>'
    f'last synced {run("last_synced"):%b %d, %Y %H:%M}</div>',
    unsafe_allow_html=True,
)

# ---------- KPIs (current window vs. previous window of equal length) ----------
cur, prev = run("kpis", f), run("kpis", f.shifted_back())
conv, prev_conv = (cur.leases / cur.leads if cur.leads else 0), (prev.leases / prev.leads if prev.leads else 0)
k = st.columns(5)
k[0].metric("Leads", f"{int(cur.leads):,}", delta(cur.leads, prev.leads))
k[1].metric("Tours", f"{int(cur.tours):,}", delta(cur.tours, prev.tours))
k[2].metric("Leases signed", f"{int(cur.leases):,}", delta(cur.leases, prev.leases))
k[3].metric("Lead → lease", f"{conv:.1%}", f"{(conv - prev_conv) * 100:+.1f} pts" if prev.leads else None)
k[4].metric("Revenue", money(cur.revenue), delta(cur.revenue, prev.revenue))
st.caption(f"Deltas compare against {f.shifted_back().start:%b %d} – {f.shifted_back().end:%b %d, %Y}.")

overview, team, markets, calls, explorer = st.tabs(["Overview", "Agents", "Markets & sources", "AI calls", "Lead explorer"])

with overview:
    c1, c2 = st.columns([3, 2], gap="large")
    m = run("monthly", f)
    c1.subheader("Commission revenue by lead month")
    c1.altair_chart(
        alt.Chart(m).mark_bar(color=TEAL, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("yearmonth(month):T", title=None),
            y=alt.Y("revenue:Q", title=None, axis=alt.Axis(format="$,.2s")),
            tooltip=[alt.Tooltip("yearmonth(month):T", title="Month"), alt.Tooltip("revenue:Q", format="$,.0f"),
                     "leads:Q", "leases:Q"],
        ).properties(height=320),
        width="stretch",
    )
    fn = run("funnel", f)
    fn["pct"] = fn.leads / fn.leads.iloc[0] if fn.leads.iloc[0] else 0
    c2.subheader("Lead funnel")
    bars = alt.Chart(fn).encode(
        y=alt.Y("step:N", sort=list(fn.step), title=None),
        x=alt.X("leads:Q", title=None, axis=None, scale=alt.Scale(domain=[0, int(fn.leads.max() * 1.25) or 1])),
        tooltip=["step", alt.Tooltip("leads:Q", format=","), alt.Tooltip("pct:Q", format=".1%", title="of leads")],
    )
    c2.altair_chart(
        (bars.mark_bar(color=TEAL, cornerRadiusEnd=3)
         + bars.mark_text(align="left", dx=6, color=INK).encode(text=alt.Text("pct:Q", format=".1%"))
         ).properties(height=320),
        width="stretch",
    )

with team:
    lb = run("agent_leaderboard", f)
    st.dataframe(
        lb, hide_index=True, width="stretch", height=560,
        column_config={
            "revenue": st.column_config.ProgressColumn("Revenue", format="dollar", min_value=0,
                                                       max_value=float(lb.revenue.max() or 1)),
            "conversion": st.column_config.NumberColumn("Lead → lease", format="percent"),
        },
    )

with markets:
    c1, c2 = st.columns(2, gap="large")
    nb = run("neighborhoods", f)
    c1.subheader("Neighborhoods")
    c1.altair_chart(
        alt.Chart(nb).mark_bar(color=TEAL, cornerRadiusEnd=3).encode(
            y=alt.Y("neighborhood:N", sort="-x", title=None),
            x=alt.X("revenue:Q", title=None, axis=alt.Axis(format="$,.2s")),
            tooltip=["neighborhood", "leads:Q", "leases:Q", alt.Tooltip("avg_rent:Q", format="$,.0f", title="Avg rent"),
                     alt.Tooltip("revenue:Q", format="$,.0f")],
        ).properties(height=380),
        width="stretch",
    )
    src = run("by_source", f)
    c2.subheader("Lead sources")
    c2.altair_chart(
        alt.Chart(src).mark_circle(color=TEAL, opacity=0.85).encode(
            x=alt.X("leads:Q", title="Leads", axis=alt.Axis(format=",")),
            y=alt.Y("conversion:Q", title="Lead → lease", axis=alt.Axis(format=".0%")),
            size=alt.Size("revenue:Q", legend=None, scale=alt.Scale(range=[200, 2400])),
            tooltip=["source", "leads:Q", "leases:Q", alt.Tooltip("conversion:Q", format=".1%"),
                     alt.Tooltip("revenue:Q", format="$,.0f")],
        ).properties(height=380)
        + alt.Chart(src).mark_text(dy=-22, color=INK).encode(
            x=alt.X("leads:Q", axis=alt.Axis(format=",")), y="conversion:Q", text="source"),
        width="stretch",
    )

with calls:
    cs = run("calls_summary", f)
    cols = st.columns(max(len(cs), 1))
    for col, row in zip(cols, cs.itertuples()):
        col.metric(f"{row.channel} calls", f"{row.calls:,}",
                   f"{row.connect_rate:.0%} connect · {row.avg_talk_sec / 60:.1f} min avg talk", delta_color="off")
    st.caption("Calls placed to leads created in the selected window.")

with explorer:
    page = st.number_input("Page", min_value=1, value=1, step=1) - 1
    st.dataframe(run("lead_page", f, page), hide_index=True, width="stretch",
                 column_config={"budget": st.column_config.NumberColumn(format="dollar"),
                                "monthly_rent": st.column_config.NumberColumn("Rent", format="dollar")})

with st.expander("SQL executed in the warehouse (this session)"):
    if not QUERY_LOG:
        st.caption("All results served from cache — press Refresh to re-query.")
    for sql, ms, rows in list(QUERY_LOG)[:10]:
        st.code(sql, language="sql")
        st.caption(f"{ms:,.0f} ms · {rows:,} rows returned")
