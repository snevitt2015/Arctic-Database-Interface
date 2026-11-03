"""
app.py  -  The user interface.

Run it with:    streamlit run app.py
It opens in your browser at http://localhost:8501
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import IMPORTS_DIR, DEMO_FILE
from tracker.pipeline import build_dataset, update_data
from tracker.features import dark_periods
from tracker.matching import identity_changes
from tracker.prediction import predict, backtest, METHODS
from tracker.sources import ALL_SOURCES
from tracker.sources.csv_import import read_file
from tracker import storage

st.set_page_config(page_title="Arctic Vessel Tracker", layout="wide")
st.title("Arctic Vessel Tracker")
st.caption("Russian-linked tankers in the Arctic · OSINT: Global Fishing Watch, MarineTraffic, TankerMap, manual sightings")

# ---------------- Sidebar: data controls ----------------
with st.sidebar:
    st.header("Data")
    show_demo = st.checkbox("Show demo data (SYNTHETIC - for testing only)", value=False,
                            disabled=not DEMO_FILE.exists())

    with st.expander("Download from OSINT sources"):
        chosen = st.multiselect("Sources", list(ALL_SOURCES), default=list(ALL_SOURCES))
        if st.button("Fetch now"):
            log_box = st.empty()
            lines = []
            def log(msg):
                lines.append(msg)
                log_box.code("\n".join(lines[-15:]))
            with st.spinner("Downloading (all dates - can take a while)..."):
                new = update_data(chosen, log=log)
            st.success(f"{new} new rows saved")
            st.cache_data.clear()

    with st.expander("Upload a CSV / Excel export (TankerMap, MarineTraffic...)"):
        up = st.file_uploader("File", type=["csv", "xlsx"])
        label = st.text_input("Source name", value="TankerMap")
        if up and st.button("Import file"):
            rows = read_file(up, source_name=label)
            new = storage.save_positions(rows, private=True)   # uploads stay off GitHub
            (IMPORTS_DIR / up.name).write_bytes(up.getvalue())   # keep the original too
            st.success(f"{len(rows)} rows read, {new} new")
            st.cache_data.clear()


@st.cache_data
def load(include_demo):
    return build_dataset(include_demo)


df, vessels, ports = load(show_demo)

# ---------------- Sidebar: filters ----------------
with st.sidebar:
    st.header("Filters")
    names = sorted(vessels["name"])
    selected = st.multiselect("Vessels", names, default=names)
    if not df.empty:
        dmin, dmax = df["timestamp"].min().date(), df["timestamp"].max().date()
        dates = st.date_input("Dates (default: ALL dates)", (dmin, dmax), min_value=dmin, max_value=dmax)
    show_unmatched = st.checkbox("Show ships not on the watch list", value=False)
    hours_ahead = st.slider("Predict hours ahead", 6, 168, 72, step=6)
    methods = st.multiselect("Prediction methods", list(METHODS), default=list(METHODS))

if df.empty:
    st.info("No position data yet. Use the sidebar to **fetch from Global Fishing Watch** "
            "(needs GFW_API_TOKEN), **upload a CSV**, type sightings into the Excel 'Operations' tab, "
            "or tick **Show demo data** to try the app.")
    st.subheader("Watch list (from Excel)")
    st.dataframe(vessels, use_container_width=True)
    st.stop()

# Apply filters
view = df[df["vessel"].isin(selected) | (show_unmatched & (df["vessel"] == ""))]
if isinstance(dates, tuple) and len(dates) == 2:
    view = view[(view["timestamp"].dt.date >= dates[0]) & (view["timestamp"].dt.date <= dates[1])]

# Predictions for each selected vessel
pred_tables = []
for vessel, track in view[view["vessel"] != ""].groupby("vessel"):
    p = predict(track, methods=methods, hours=hours_ahead)
    if not p.empty:
        p.insert(0, "vessel", vessel)
        pred_tables.append(p)
preds = pd.concat(pred_tables, ignore_index=True) if pred_tables else pd.DataFrame()

# ---------------- Headline numbers ----------------
dark = dark_periods(view)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Positions", f"{len(view):,}")
c2.metric("Vessels with data", view.loc[view["vessel"] != "", "vessel"].nunique())
c3.metric("Suspected dark periods", len(dark))
c4.metric("Spoofing warnings", int(view["speed_mismatch"].sum() + view["impossible_jump"].sum()))

tab_map, tab_latest, tab_dark, tab_pred, tab_data, tab_quality = st.tabs(
    ["Map", "Latest positions", "Dark activity", "Predictions", "All data", "Data quality"])

# ---------------- Map ----------------
with tab_map:
    fig = go.Figure()
    palette = px.colors.qualitative.Bold + px.colors.qualitative.Safe
    colour = {v: palette[i % len(palette)] for i, v in enumerate(sorted(view["vessel"].unique()))}
    for vessel, g in view.groupby("vessel"):
        g = g.sort_values("timestamp")
        label = vessel or "Not on watch list"
        fig.add_trace(go.Scattermap(
            lat=g["lat"], lon=g["lon"], mode="lines+markers", name=label, legendgroup=label,
            marker=dict(size=6, color=colour[vessel]), line=dict(width=2, color=colour[vessel]),
            text=[f"{label}<br>{t:%Y-%m-%d %H:%M} UTC<br>{s:.1f} kn, {c:.0f}°<br>{e} · {src}<br>{n}"
                  for t, s, c, e, src, n in zip(g["timestamp"], g["speed_used"].fillna(0),
                                                 g["course_used"].fillna(0), g["event_type"], g["source"], g["note"])],
            hoverinfo="text"))
    flagged = view[view["dark_gap"]]
    if not flagged.empty:
        fig.add_trace(go.Scattermap(lat=flagged["lat"], lon=flagged["lon"], mode="markers",
                                    name="AIS gap (dark)", marker=dict(size=14, color="red", opacity=0.6),
                                    hoverinfo="skip"))
    for vessel, p in (preds.groupby(["vessel", "method"]) if not preds.empty else []):
        last = view[view["vessel"] == vessel[0]].sort_values("timestamp").iloc[-1]
        fig.add_trace(go.Scattermap(
            lat=[last["lat"], *p["lat"]], lon=[last["lon"], *p["lon"]], mode="lines+markers",
            name=f"{vessel[0]} prediction ({vessel[1]})", legendgroup=vessel[0], showlegend=False,
            line=dict(width=2, color=colour[vessel[0]]), marker=dict(size=5, color=colour[vessel[0]]),
            opacity=0.35,
            text=[f"Predicted +{h} h ±{u:.0f} km" for h, u in zip([0, *p["hours_ahead"]], [0, *p["uncertainty_km"]])],
            hoverinfo="text"))
    pp = ports.dropna(subset=["lat", "lon"])
    fig.add_trace(go.Scattermap(lat=pp["lat"], lon=pp["lon"], mode="markers+text", name="Ports",
                                text=pp["name"], textposition="top right",
                                marker=dict(size=11, color="black")))
    fig.update_layout(map=dict(style="open-street-map", center=dict(lat=70, lon=50), zoom=2.3),
                      height=650, margin=dict(l=0, r=0, t=0, b=0), legend=dict(bgcolor="rgba(255,255,255,0.8)"))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Lines = tracks · red circles = AIS gaps · faded lines = predictions · black = ports. "
               "Predictions are straight-line estimates and do not avoid land or ice yet.")

# ---------------- Latest ----------------
with tab_latest:
    latest = (view[view["vessel"] != ""].sort_values("timestamp").groupby("vessel").tail(1)
              [["vessel", "timestamp", "lat", "lon", "speed_used", "course_used", "event_type",
                "nearest_port", "km_to_port", "source", "note"]])
    hours_silent = (pd.Timestamp.now(tz="UTC") - latest["timestamp"]).dt.total_seconds() / 3600
    latest.insert(2, "hours_since_seen", hours_silent.round(0))
    st.dataframe(latest.sort_values("hours_since_seen", ascending=False), use_container_width=True)
    st.caption("A large 'hours since seen' can mean the ship is dark - or that you need to fetch newer data.")

# ---------------- Dark activity ----------------
with tab_dark:
    st.subheader("Suspected dark periods (AIS switched off)")
    st.dataframe(dark, use_container_width=True)
    st.subheader("Possible spoofing (position doesn't fit the reported speed)")
    st.dataframe(view[view["speed_mismatch"] | view["impossible_jump"]]
                 [["vessel", "timestamp", "lat", "lon", "speed_kn", "calc_speed_kn", "source"]],
                 use_container_width=True)
    st.subheader("Identity changes (several MMSIs or names for one ship)")
    st.dataframe(identity_changes(view), use_container_width=True)
    st.subheader("Encounters and loitering (possible ship-to-ship transfers)")
    st.dataframe(view[view["event_type"].isin(["encounter", "loitering"])]
                 [["vessel", "timestamp", "lat", "lon", "event_type", "note"]], use_container_width=True)

# ---------------- Predictions ----------------
with tab_pred:
    st.dataframe(preds, use_container_width=True)
    st.subheader("How accurate are the methods? (backtest on past data)")
    v = st.selectbox("Vessel", sorted(view.loc[view["vessel"] != "", "vessel"].unique()))
    bt_hours = st.slider("Test horizon (hours)", 3, 48, 12, step=3)
    if v:
        track = view[view["vessel"] == v]
        results = [backtest(track, m, bt_hours) for m in methods]
        results = [r for r in results if not r.empty]
        if results:
            bt = pd.concat(results)
            st.dataframe(bt.groupby("method")["error_km"].describe()[["count", "mean", "50%", "max"]]
                         .rename(columns={"mean": "mean error km", "50%": "median error km"}))
        else:
            st.write("Not enough regular positions for a backtest.")

# ---------------- All data ----------------
with tab_data:
    st.dataframe(view, use_container_width=True)
    st.download_button("Download as CSV", view.to_csv(index=False), "arctic_positions_features.csv")

# ---------------- Data quality ----------------
with tab_quality:
    st.subheader("Excel watch list problems")
    for p in vessels.attrs.get("problems", []):
        st.warning(p)
    st.subheader("Ports")
    st.dataframe(ports[["name", "lat", "lon", "coords_source"]], use_container_width=True)
    st.subheader("How positions were matched to the watch list")
    st.dataframe(df["match_on"].value_counts())
    st.subheader("Rows per source")
    st.dataframe(df["source"].value_counts())
