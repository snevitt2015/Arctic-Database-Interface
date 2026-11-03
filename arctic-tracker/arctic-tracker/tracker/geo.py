"""
geo.py  -  Basic map maths (distance, direction, moving a point).

All functions work on the Earth's surface (great-circle), which matters
in the Arctic where lines of longitude get very close together.
"""
import math

EARTH_RADIUS_KM = 6371.0
KM_PER_NAUTICAL_MILE = 1.852


def distance_km(lat1, lon1, lat2, lon2) -> float:
    """Distance between two points in kilometres (haversine formula)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Compass direction (0 = North, 90 = East) to travel from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def move_point(lat, lon, bearing, distance):
    """Start at (lat, lon), travel `distance` km in direction `bearing`. Returns new (lat, lon)."""
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    b = math.radians(bearing)
    d = distance / EARTH_RADIUS_KM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    lon2 = (math.degrees(l2) + 540) % 360 - 180   # keep between -180 and 180
    return math.degrees(p2), lon2


def knots_to_kmh(knots: float) -> float:
    return knots * KM_PER_NAUTICAL_MILE


def kmh_to_knots(kmh: float) -> float:
    return kmh / KM_PER_NAUTICAL_MILE
