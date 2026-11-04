"""
interpolate.py  -  Where was a ship at an exact moment?

This is the bridge between tracking and imagery: to say "this radar blip is
Arktika", we need Arktika's estimated position at the second the satellite
passed over, and how sure we are about it.

    position_at(track, when)  ->  dict(lat, lon, method, uncertainty_km, ...)

Methods, best first:
  exact          a position within EXACT_MINUTES of `when`
  area           same, but the position is only an area name (e.g. "Barents Sea", ±700 km)
  interpolated   between the positions just before and just after (along the
                 great circle, at constant speed). Only if they are close enough
                 in time that the ship couldn't have done much else.
  projected      after the last known position: move it along its last speed and
                 course (dead reckoning). Only for a limited time.
  last_known     too long ago to project - just the last position, with a big
                 uncertainty that grows with time.
  none           no position before `when` at all.

`uncertainty_km` is a rough radius. It is deliberately simple; replace it with
something smarter once you have ground truth (SAR detections with confirmed AIS).
"""
import math
import pandas as pd

from tracker.geo import distance_km, bearing_deg, move_point, knots_to_kmh

EXACT_MINUTES = 10          # a fix this close in time counts as "exact"
MAX_INTERP_HOURS = 12       # interpolate only across gaps up to this long
MAX_PROJECT_HOURS = 6       # dead-reckon at most this far past the last fix
AIS_ERROR_KM = 0.5          # typical error of a single AIS / event position
DRIFT_KM_PER_HOUR = 5.0     # how fast uncertainty grows when we're guessing
MAX_SHIP_KNOTS = 20.0       # a ship this slow or faster is unrealistic for the uncertainty cap


def _err(p) -> float:
    """This fix's own position error (area names are hundreds of km), at least AIS_ERROR_KM."""
    v = p.get("pos_error_km") if hasattr(p, "get") else None
    return max(AIS_ERROR_KM, float(v)) if v is not None and pd.notna(v) else AIS_ERROR_KM


def _as_utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def position_at(track: pd.DataFrame, when) -> dict:
    """Estimate one ship's position at `when` (UTC) from its track (standard columns)."""
    when = _as_utc(when)
    t = track.dropna(subset=["lat", "lon", "timestamp"]).sort_values("timestamp")
    out = {"time": when, "lat": None, "lon": None, "method": "none", "uncertainty_km": None,
           "before_time": None, "after_time": None, "gap_hours": None}
    if t.empty:
        return out

    before = t[t["timestamp"] <= when].tail(1)
    after = t[t["timestamp"] > when].head(1)
    b = before.iloc[0] if not before.empty else None
    a = after.iloc[0] if not after.empty else None
    if b is not None:
        out["before_time"] = b["timestamp"]
    if a is not None:
        out["after_time"] = a["timestamp"]

    # 1. exact
    for p in (b, a):
        if p is not None and abs((p["timestamp"] - when).total_seconds()) <= EXACT_MINUTES * 60:
            return {**out, "lat": p["lat"], "lon": p["lon"], "method": "exact" if _err(p) <= 20 else "area",
                    "uncertainty_km": _err(p)}

    # 2. interpolate between the fixes around `when`
    if b is not None and a is not None:
        gap_h = (a["timestamp"] - b["timestamp"]).total_seconds() / 3600
        out["gap_hours"] = round(gap_h, 2)
        if gap_h <= MAX_INTERP_HOURS:
            frac = (when - b["timestamp"]).total_seconds() / (gap_h * 3600)
            d = distance_km(b["lat"], b["lon"], a["lat"], a["lon"])
            lat, lon = move_point(b["lat"], b["lon"], bearing_deg(b["lat"], b["lon"], a["lat"], a["lon"]), d * frac)
            # biggest error is in the middle of the gap
            unc = max(_err(b), _err(a)) + DRIFT_KM_PER_HOUR * gap_h * min(frac, 1 - frac)
            return {**out, "lat": lat, "lon": lon, "method": "interpolated", "uncertainty_km": round(unc, 2)}

    if b is None:
        return out          # nothing known before this moment

    # 3. project forward from the last fix with a usable speed and course
    age_h = (when - b["timestamp"]).total_seconds() / 3600
    speed = b.get("speed_used", b.get("speed_kn"))
    course = b.get("course_used", b.get("course"))
    if age_h <= MAX_PROJECT_HOURS and pd.notna(speed) and pd.notna(course) and _err(b) <= 20:
        lat, lon = move_point(b["lat"], b["lon"], course, knots_to_kmh(speed) * age_h)
        unc = _err(b) + DRIFT_KM_PER_HOUR * age_h
        return {**out, "lat": lat, "lon": lon, "method": "projected", "uncertainty_km": round(unc, 2)}

    # 4. last known position; the ship could be anywhere it can reach since then
    unc = min(_err(b) + knots_to_kmh(MAX_SHIP_KNOTS) * age_h, 20000)
    return {**out, "lat": b["lat"], "lon": b["lon"], "method": "last_known", "uncertainty_km": round(unc, 1)}


def positions_at(df: pd.DataFrame, when, vessels=None) -> pd.DataFrame:
    """position_at for every ship (or the listed ones) at the same moment - e.g. a SAR scene time.
    Sorted with the most certain estimates first."""
    key = "vessel" if "vessel" in df.columns else "imo"
    rows = []
    for v, track in df.groupby(key):
        if not v or (vessels is not None and v not in vessels):
            continue
        rows.append({key: v, **position_at(track, when)})
    out = pd.DataFrame(rows)
    return out.sort_values("uncertainty_km", na_position="last").reset_index(drop=True) if not out.empty else out


def candidates_near(df: pd.DataFrame, lat: float, lon: float, when, max_km: float = 50) -> pd.DataFrame:
    """Which watch-list ships could be at (lat, lon) at `when`? Used later to match an image
    detection to a ship. A ship qualifies if the spot is within its uncertainty circle + max_km."""
    est = positions_at(df, when)
    if est.empty:
        return est
    est = est.dropna(subset=["lat"]).copy()
    est["km_away"] = [distance_km(lat, lon, a, b) for a, b in zip(est["lat"], est["lon"])]
    est = est[est["km_away"] <= est["uncertainty_km"] + max_km]
    return est.sort_values(["km_away"]).reset_index(drop=True)
