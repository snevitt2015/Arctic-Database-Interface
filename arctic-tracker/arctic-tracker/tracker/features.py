"""
features.py  -  Turn raw positions into movement features.

For every position (per vessel, in time order) we add:

  hours_since_prev   time since the previous position
  km_from_prev       distance from the previous position
  calc_speed_kn      speed WE calculate from the two positions
  calc_course        direction WE calculate from the two positions
  speed_used         reported speed, or our calculated one if missing
  course_used        reported course -> heading -> calculated, first one available
  turn_deg           how much the ship changed direction
  nearest_port, km_to_port, in_port
  dark_gap           AIS went silent (possible dark activity)
  speed_mismatch     reported speed doesn't fit the distance moved (possible spoofing)
  impossible_jump    moved faster than a tanker can (likely spoofed position)
  risk_score         simple sum of the flags above - a STARTING POINT to refine

To add a new feature: write a few lines in add_features() below and it
will appear as a new column everywhere (table, CSV export, app).
"""
import numpy as np
import pandas as pd

import config
from tracker.geo import distance_km, bearing_deg, kmh_to_knots

# Gaps between these kinds of rows mean "AIS went quiet". Gaps between, say,
# two port visits are normal (we just don't have the track in between).
TRACK_TYPES = {"position", "ais_on"}


def _angle_diff(a, b):
    """Smallest difference between two compass directions (0-180)."""
    if pd.isna(a) or pd.isna(b):
        return np.nan
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def nearest_port(lat, lon, ports: pd.DataFrame):
    best_name, best_km = "", np.nan
    for _, p in ports.dropna(subset=["lat", "lon"]).iterrows():
        d = distance_km(lat, lon, p["lat"], p["lon"])
        if np.isnan(best_km) or d < best_km:
            best_name, best_km = p["name"], d
    return best_name, best_km


def add_features(df: pd.DataFrame, ports: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    key = "vessel" if "vessel" in df.columns else "imo"
    df = df.sort_values([key, "timestamp"]).reset_index(drop=True)
    out = []

    for _, g in df.groupby(key, sort=False):
        g = g.copy()
        prev = g.shift(1)
        hours = (g["timestamp"] - prev["timestamp"]).dt.total_seconds() / 3600
        km = [distance_km(a, b, c, d) if not pd.isna(c) else np.nan
              for a, b, c, d in zip(prev["lat"], prev["lon"], g["lat"], g["lon"])]
        g["hours_since_prev"] = hours
        g["km_from_prev"] = km
        g["calc_speed_kn"] = [kmh_to_knots(k / h) if h and h > 0 else np.nan for k, h in zip(km, hours)]
        g["calc_course"] = [bearing_deg(a, b, c, d) if not pd.isna(a) else np.nan
                            for a, b, c, d in zip(prev["lat"], prev["lon"], g["lat"], g["lon"])]

        g["speed_used"] = g["speed_kn"].fillna(g["calc_speed_kn"])
        g["course_used"] = g["course"].fillna(g["heading"]).fillna(g["calc_course"])
        g["turn_deg"] = [_angle_diff(a, b) for a, b in zip(g["course_used"], g["course_used"].shift(1))]

        both_track = g["event_type"].isin(TRACK_TYPES) & prev["event_type"].isin(TRACK_TYPES)
        g["dark_gap"] = (g["event_type"] == "ais_off") | (both_track & (hours > config.DARK_GAP_HOURS))
        g["speed_mismatch"] = (g["speed_kn"].notna() & g["calc_speed_kn"].notna() &
                               ((g["speed_kn"] - g["calc_speed_kn"]).abs() > config.SPEED_MISMATCH_KNOTS) &
                               (hours < 24))  # only compare over short intervals
        g["impossible_jump"] = g["calc_speed_kn"] > config.MAX_REALISTIC_SPEED_KN
        out.append(g)

    df = pd.concat(out, ignore_index=True)

    ports_found = [nearest_port(a, b, ports) for a, b in zip(df["lat"], df["lon"])]
    df["nearest_port"] = [p[0] for p in ports_found]
    df["km_to_port"] = [round(p[1], 1) if not pd.isna(p[1]) else np.nan for p in ports_found]
    df["in_port"] = df["km_to_port"] < config.IN_PORT_KM

    # Simple risk score: 1 point per warning sign. Make this smarter later
    # (weights, sanctions status, encounters at sea, loitering, etc.).
    df["risk_score"] = (df["dark_gap"].astype(int) * 2 +
                        df["speed_mismatch"].astype(int) +
                        df["impossible_jump"].astype(int) * 2 +
                        (df["event_type"] == "encounter").astype(int) * 2 +
                        (df["event_type"] == "loitering").astype(int))
    return df


def dark_periods(df: pd.DataFrame) -> pd.DataFrame:
    """One row per suspected dark period: when, how long, where it went silent and reappeared."""
    if df.empty or "dark_gap" not in df.columns:
        return pd.DataFrame()
    rows = []
    key = "vessel" if "vessel" in df.columns else "imo"
    for vessel, g in df.groupby(key):
        g = g.reset_index(drop=True)
        for i, r in g[g["dark_gap"]].iterrows():
            if r["event_type"] == "ais_off":
                nxt = g.iloc[i + 1] if i + 1 < len(g) else None
                start, end = r, nxt
            else:
                start, end = g.iloc[i - 1], r
            if end is None:
                rows.append({"vessel": vessel, "went_dark": start["timestamp"], "reappeared": "still dark?",
                             "hours_dark": np.nan, "km_moved": np.nan,
                             "dark_lat": start["lat"], "dark_lon": start["lon"],
                             "near_port": start.get("nearest_port", ""), "note": start["note"]})
                continue
            rows.append({
                "vessel": vessel, "went_dark": start["timestamp"], "reappeared": end["timestamp"],
                "hours_dark": round((end["timestamp"] - start["timestamp"]).total_seconds() / 3600, 1),
                "km_moved": round(distance_km(start["lat"], start["lon"], end["lat"], end["lon"]), 1),
                "dark_lat": start["lat"], "dark_lon": start["lon"],
                "near_port": start.get("nearest_port", ""), "note": start["note"] or end["note"],
            })
    out = pd.DataFrame(rows)
    # an ais_off row followed by its ais_on row would be listed twice - keep one
    return out.drop_duplicates(subset=["vessel", "went_dark"]) if not out.empty else out
