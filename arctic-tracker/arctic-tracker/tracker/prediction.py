"""
prediction.py  -  Predict where a ship will be in the future.

Two methods to start with. Both are deliberately simple so you can see
exactly what they do, and both return the SAME output format, so you can
add a smarter method later (route-based, machine learning...) and the
app will show it next to these.

  dead_reckoning   last known position + last speed + last direction.
                   Classic navigation; good for the next few hours.

  recent_average   average speed and direction over the last few
                   positions. Smoother; still works when the ship stopped
                   broadcasting speed/heading (common for dark ships).

Output: a table with columns
    method, hours_ahead, timestamp, lat, lon, uncertainty_km

`uncertainty_km` grows with time - the further ahead, the less sure we are.

IDEAS FOR NEXT STEPS (increasing complexity):
  * stop predictions from crossing land (use a coastline shapefile)
  * steer towards the most likely destination port (past port visits)
  * learn typical routes from all past tracks (e.g. Murmansk -> Sabetta)
  * machine learning on the features table (scikit-learn is in requirements)
  * ice-season adjustments for the Northern Sea Route
"""
import numpy as np
import pandas as pd

import config
from tracker.geo import move_point, knots_to_kmh, bearing_deg, distance_km, kmh_to_knots

# How fast the error grows: base km + km per hour ahead. Rough starting values - tune with real data.
UNCERTAINTY_BASE_KM = 2.0
UNCERTAINTY_PER_HOUR_KM = 3.0


def _project(lat, lon, speed_kn, course, start_time, method, hours=None, step=None):
    hours = hours or config.PREDICT_HOURS
    step = step or config.PREDICT_STEP_HOURS
    rows = []
    for h in range(step, hours + 1, step):
        km = knots_to_kmh(speed_kn) * h
        plat, plon = move_point(lat, lon, course, km)
        rows.append({"method": method, "hours_ahead": h,
                     "timestamp": start_time + pd.Timedelta(hours=h),
                     "lat": plat, "lon": plon,
                     "uncertainty_km": UNCERTAINTY_BASE_KM + UNCERTAINTY_PER_HOUR_KM * h})
    return rows


def dead_reckoning(track: pd.DataFrame, hours=None, step=None) -> pd.DataFrame:
    """Use the latest position that has a usable speed and direction."""
    t = track.sort_values("timestamp")
    last = t.iloc[-1]
    speed = last.get("speed_used", last["speed_kn"])
    course = last.get("course_used", last["course"])
    if pd.isna(speed) or pd.isna(course):
        return pd.DataFrame()
    if _is_stopped(last, speed):
        speed = 0.0
    return pd.DataFrame(_project(last["lat"], last["lon"], speed, course,
                                 last["timestamp"], "dead_reckoning", hours, step))


def _is_stopped(last, speed) -> bool:
    """Rule: a ship in port, or barely moving, is assumed to stay where it is.
    (Later: predict WHEN it leaves, from how long it usually stays in that port.)"""
    return bool(last.get("in_port", False)) or speed < 0.5 or last["event_type"] == "port_visit"


def recent_average(track: pd.DataFrame, n_points=None, hours=None, step=None) -> pd.DataFrame:
    """Average movement between the first and last of the last N positions."""
    n_points = n_points or config.RECENT_POINTS_FOR_AVERAGE
    t = track.sort_values("timestamp").tail(n_points)
    if len(t) < 2:
        return pd.DataFrame()
    first, last = t.iloc[0], t.iloc[-1]
    span_h = (last["timestamp"] - first["timestamp"]).total_seconds() / 3600
    if span_h <= 0:
        return pd.DataFrame()
    km = distance_km(first["lat"], first["lon"], last["lat"], last["lon"])
    speed = kmh_to_knots(km / span_h)
    course = bearing_deg(first["lat"], first["lon"], last["lat"], last["lon"])
    if _is_stopped(last, speed):
        speed = 0.0
    return pd.DataFrame(_project(last["lat"], last["lon"], speed, course,
                                 last["timestamp"], "recent_average", hours, step))


# Add new methods here and they'll show up in the app automatically.
METHODS = {
    "dead_reckoning": dead_reckoning,
    "recent_average": recent_average,
}


def predict(track: pd.DataFrame, methods=None, hours=None, step=None) -> pd.DataFrame:
    """Run every method on one vessel's track and stack the results."""
    if track.empty:
        return pd.DataFrame()
    results = []
    for name in methods or METHODS:
        p = METHODS[name](track, hours=hours, step=step)
        if not p.empty:
            results.append(p)
    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def backtest(track: pd.DataFrame, method: str = "dead_reckoning", hours: int = 12) -> pd.DataFrame:
    """How good is a method? Pretend we're at each past point, predict `hours` ahead,
    and compare with where the ship actually was. Returns the error in km per test."""
    t = track.sort_values("timestamp").reset_index(drop=True)
    errors = []
    for i in range(1, len(t)):
        history = t.iloc[: i + 1]
        target_time = t.loc[i, "timestamp"] + pd.Timedelta(hours=hours)
        future = t[(t["timestamp"] - target_time).abs() < pd.Timedelta(hours=1)]
        if future.empty:
            continue
        pred = METHODS[method](history, hours=hours, step=hours)
        if pred.empty:
            continue
        actual = future.iloc[0]
        errors.append({"from_time": t.loc[i, "timestamp"], "method": method, "hours_ahead": hours,
                       "error_km": distance_km(pred.iloc[-1]["lat"], pred.iloc[-1]["lon"],
                                               actual["lat"], actual["lon"])})
    return pd.DataFrame(errors)
