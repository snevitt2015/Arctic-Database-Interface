"""
cueing.py  -  Turn suspicious tracking events into "cues" (pings): WHERE and WHEN to look
in satellite imagery.

A cue says: "ship X may be doing something without AIS; look in this circle during
this time window." The imagery step (sources/copernicus.py) then finds every
Sentinel-1 radar and Sentinel-2 optical scene that covers it.

Cue types (add more in CUE_RULES at the bottom):
  ais_off      AIS switched off (GFW gap event, or a >6 h silence between AIS positions).
               Window = from going dark to coming back. Area = around where it went dark,
               big enough to reach where it came back.
  spoofing     An "impossible move" (position jump faster than a ship can sail) - one of
               the two positions is probably fake. Window = around both positions.
  silent       No record for a watch-list ship for SILENT_DAYS. LOW priority: our main source
               (GFW) gives events - port visits, loitering - not every AIS ping, so a ship
               sailing normally can go days without a record. Window = the first days after
               its last record. Area = last position + how far it could sail.
  identity     The ship started using a new name or MMSI. Window = around the change.

Every cue gets a stable id (same event -> same id every day), so the viewer can show
which pings are NEW since the last run.
"""
import hashlib
import pandas as pd

import config
from tracker.geo import distance_km, knots_to_kmh

LOOKBACK_DAYS = 120          # only cue events this recent (imagery search is per cue)
SILENT_DAYS = 14             # no record for this long -> "silent" cue (GFW events can be days apart)
SEARCH_RADIUS_CAP_KM = 300   # bigger areas give too many scenes to be useful
SHIP_SPEED_KN = 12           # typical speed used to grow the search area over time


def _cue_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _cue(kind, vessel, imo, start, end, lat, lon, radius_km, priority, reason, **extra):
    return {
        "cue_id": _cue_id(kind, vessel, pd.Timestamp(start).floor("min")),
        "kind": kind, "vessel": vessel, "imo": imo,
        "start": pd.Timestamp(start), "end": pd.Timestamp(end),
        "lat": round(float(lat), 4), "lon": round(float(lon), 4),
        "radius_km": round(min(float(radius_km), SEARCH_RADIUS_CAP_KM), 1),
        "radius_capped": float(radius_km) > SEARCH_RADIUS_CAP_KM,
        "priority": priority, "reason": reason, **extra,
    }


# ------------------------------------------------------------------ rules
def cues_ais_off(df: pd.DataFrame, since) -> list:
    out = []
    for vessel, g in df[df["vessel"] != ""].groupby("vessel"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        for i, r in g[g["dark_gap"] & (g["timestamp"] >= since)].iterrows():
            if r["event_type"] == "ais_off":
                eid = str(r.get("event_id", "") or "")
                later = g.iloc[i + 1:]
                back = later[later["event_id"].astype(str) == eid + ":on"] if eid else later.iloc[0:0]
                back = back if not back.empty else later[later["event_type"] == "ais_on"].head(1)
                start_row, end_row = r, (back.iloc[0] if not back.empty else None)
            else:                                   # silence between two AIS positions
                start_row, end_row = g.iloc[i - 1], r
            start = start_row["timestamp"]
            end = end_row["timestamp"] if end_row is not None else pd.Timestamp.now(tz="UTC")
            hours = (end - start).total_seconds() / 3600
            if end_row is not None:
                lat = (start_row["lat"] + end_row["lat"]) / 2
                lon = (start_row["lon"] + end_row["lon"]) / 2
                radius = distance_km(start_row["lat"], start_row["lon"], end_row["lat"], end_row["lon"]) / 2 + 30
            else:
                lat, lon = start_row["lat"], start_row["lon"]
                radius = 30 + knots_to_kmh(SHIP_SPEED_KN) * hours
            out.append(_cue("ais_off", vessel, start_row["imo"], start, end, lat, lon, radius,
                            "high", f"AIS off for {hours:.0f} h" + ("" if end_row is not None else " - still dark"),
                            off_lat=start_row["lat"], off_lon=start_row["lon"],
                            on_lat=None if end_row is None else end_row["lat"],
                            on_lon=None if end_row is None else end_row["lon"]))
    return out


def cues_spoofing(df: pd.DataFrame, since) -> list:
    out = []
    for vessel, g in df[df["vessel"] != ""].groupby("vessel"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        for i, r in g[(g["impossible_jump"] | g["speed_mismatch"]) & (g["timestamp"] >= since)].iterrows():
            if i == 0:
                continue
            p = g.iloc[i - 1]
            km = distance_km(p["lat"], p["lon"], r["lat"], r["lon"])
            # look at the LATER position: is the ship really there? (radius covers both if close)
            out.append(_cue("spoofing", vessel, r["imo"], p["timestamp"] - pd.Timedelta(hours=6),
                            r["timestamp"] + pd.Timedelta(hours=6), r["lat"], r["lon"], min(km / 2, 100) + 20,
                            "high", f"Impossible move: {km:,.0f} km at {r['calc_speed_kn']:.0f} kn "
                                    f"({p['name']} → {r['name']})",
                            other_lat=p["lat"], other_lon=p["lon"]))
    return out


def cues_silent(df: pd.DataFrame, vessels: pd.DataFrame, now) -> list:
    out = []
    fine = df[df["pos_error_km"].fillna(0.5) <= config.COARSE_POSITION_KM] if "pos_error_km" in df else df
    for _, v in vessels.iterrows():
        g = fine[fine["vessel"] == v["name"]].sort_values("timestamp")
        if g.empty:
            continue
        last = g.iloc[-1]
        quiet_days = (now - last["timestamp"]).total_seconds() / 86400
        if quiet_days < SILENT_DAYS or quiet_days > LOOKBACK_DAYS:
            continue
        # where it most likely went: the first 3 days after the last fix
        end = last["timestamp"] + pd.Timedelta(days=3)
        radius = 20 + knots_to_kmh(SHIP_SPEED_KN) * 72
        out.append(_cue("silent", v["name"], v["imo"], last["timestamp"], end, last["lat"], last["lon"], radius,
                        "low",
                        f"No record for {quiet_days:.0f} days (last: {last['event_type'].replace('_', ' ')}"
                        f"{' near ' + last['nearest_port'] if last.get('nearest_port') else ''})"))
    return out


def cues_identity(df: pd.DataFrame, since) -> list:
    out = []
    for vessel, g in df[(df["vessel"] != "") & (df["source"] == "GFW")].groupby("vessel"):
        g = g.sort_values("timestamp")
        key = g["name"].str.upper() + "|" + g["mmsi"]
        changed = key.ne(key.shift()) & key.shift().notna()
        for i in g.index[changed]:
            r = g.loc[i]
            if r["timestamp"] < since:
                continue
            prev = g.loc[:i].iloc[-2]
            out.append(_cue("identity", vessel, r["imo"], prev["timestamp"], r["timestamp"], r["lat"], r["lon"],
                            50, "medium", f"Identity changed: {prev['name']} / {prev['mmsi']} → {r['name']} / {r['mmsi']}"))
    return out


def build_cues(df: pd.DataFrame, vessels: pd.DataFrame, now=None, lookback_days=LOOKBACK_DAYS) -> pd.DataFrame:
    """All cues, newest first. `now` defaults to the latest record in the data (not the clock),
    so results don't depend on when the job happens to run."""
    if df.empty:
        return pd.DataFrame()
    now = pd.Timestamp(now) if now is not None else df["timestamp"].max()
    since = now - pd.Timedelta(days=lookback_days)
    cues = (cues_ais_off(df, since) + cues_spoofing(df, since) +
            cues_silent(df, vessels, now) + cues_identity(df, since))
    out = pd.DataFrame(cues)
    if out.empty:
        return out
    out = out.drop_duplicates("cue_id")
    order = {"high": 0, "medium": 1, "low": 2}
    out["_p"] = out["priority"].map(order)
    return out.sort_values(["_p", "start"], ascending=[True, False]).drop(columns="_p").reset_index(drop=True)
