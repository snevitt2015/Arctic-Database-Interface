"""
sun.py  -  Where was the sun? (elevation and azimuth at a time and place)

Needed for optical imagery: shadow length depends on the sun's elevation, and
the 3D-print photo sessions are shot at fixed sun angles (20-50°). Given an
image's exact UTC time and location, sun_position() says which session angle to
compare against.

Uses the NOAA solar-position approximation (accurate to well under 1° for
1950-2050) - plenty for picking the nearest 5° step.
"""
import math
import pandas as pd


def sun_position(when, lat: float, lon: float) -> dict:
    """Sun elevation (degrees above horizon) and azimuth (degrees clockwise from north)."""
    t = pd.Timestamp(when)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    day_of_year = t.dayofyear
    hour = t.hour + t.minute / 60 + t.second / 3600

    g = 2 * math.pi / 365 * (day_of_year - 1 + (hour - 12) / 24)                 # fractional year (radians)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))  # minutes
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))

    true_solar_min = hour * 60 + eqtime + 4 * lon
    hour_angle = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(lat)

    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    elevation = 90 - math.degrees(zen)

    az = math.degrees(math.atan2(math.sin(hour_angle),
                                 math.cos(hour_angle) * math.sin(phi) - math.tan(decl) * math.cos(phi))) + 180
    return {"sun_elevation": round(elevation, 2), "sun_azimuth": round(az % 360, 2)}


def max_sun_elevation(lat: float) -> float:
    """Highest the sun ever gets at this latitude (summer solstice noon)."""
    return round(min(90.0, 90 - abs(lat) + 23.44), 1)


def nearest_step(angle: float, low: float = 20, high: float = 50, step: float = 5):
    """Snap an angle to the nearest session step (e.g. 37.2 -> 35). None if outside the range."""
    if angle is None or angle < low - step / 2 or angle > high + step / 2:
        return None
    return min(max(round(angle / step) * step, low), high)
