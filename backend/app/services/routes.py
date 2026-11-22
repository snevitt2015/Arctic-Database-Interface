"""
services/routes.py  -  Each ship's previous route, kept at sea.

The free sources give REPORTED POSITIONS (GFW port visits, loitering, gap start/end; live AIS when a feed is on),
not a continuous track. Joining them with straight lines crosses land. So consecutive reported positions are
joined along the open global shipping-lane network (Eurostat MARNET, via the `searoute` package):

  reported positions   real, from the sources - drawn as points
  legs between them    RECONSTRUCTED along sea lanes - always labelled as an estimate of the path, never as a track

A leg is NOT drawn when it can't be a real voyage:
  - the two records conflict (the ship couldn't have sailed between them: usually an MMSI shared with another ship)
  - the records are more than MAX_GAP_DAYS apart (anything could have happened in between)
Legs under SHORT_KM are drawn straight (inside a port or anchorage area).
"""
from datetime import timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.geo import distance_km, kmh_to_knots
from app.models import Position, Vessel

SHORT_KM = 25
ICE_LAT = 81.0       # the sea-lane network ends around here; legs beyond it (e.g. icebreaker voyages to the Pole) are
                     # drawn straight and labelled "through the ice" - there are no lanes to follow
MAX_GAP_DAYS = 30
MAX_KN = 30          # faster than any of these ships: the two records can't both be this hull
_cache = {}


def _utc(t):
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _sea_path(a, b):
    key = (round(a[0], 2), round(a[1], 2), round(b[0], 2), round(b[1], 2))
    if key not in _cache:
        import searoute
        r = searoute.searoute(list(a), list(b), units="km", append_orig_dest=True, restrictions=["northwest"])
        coords = [[round(x, 3), round(y, 3)] for x, y in r.geometry["coordinates"]]
        _cache[key] = (coords, float(r.properties["length"]))
    return _cache[key]


def vessel_route(s: Session, vessel_id: int, days: int = 365, since=None) -> list[dict]:
    """GeoJSON features: one LineString per reconstructed leg (properties.kind = 'leg')."""
    if since is None:
        newest = s.scalar(select(func.max(Position.ts)).where(Position.vessel_id == vessel_id))
        if newest is None:
            return []
        since = _utc(newest) - timedelta(days=days)
    ps = s.scalars(select(Position).where(Position.vessel_id == vessel_id, Position.ts >= since)
                   .order_by(Position.ts)).all()
    out = []
    for a, b in zip(ps, ps[1:]):
        km = distance_km(a.lat, a.lon, b.lat, b.lon)
        h = (_utc(b.ts) - _utc(a.ts)).total_seconds() / 3600
        if km < 0.5:
            continue
        base = {"kind": "leg", "vessel_id": vessel_id, "from_ts": _utc(a.ts).isoformat(), "to_ts": _utc(b.ts).isoformat(),
                "from_event": a.nav_status, "to_event": b.nav_status}
        if h > MAX_GAP_DAYS * 24:
            continue
        if h <= 0 or kmh_to_knots(km / max(h, 1 / 60)) > MAX_KN:
            continue                                   # conflicting records: no voyage between them
        if km < SHORT_KM:
            coords, length, how = [[a.lon, a.lat], [b.lon, b.lat]], km, "direct (short hop)"
        elif max(a.lat, b.lat) > ICE_LAT:
            coords, length, how = [[a.lon, a.lat], [b.lon, b.lat]], km, "straight through the ice (north of the sea-lane network)"
        else:
            coords, length = _sea_path((a.lon, a.lat), (b.lon, b.lat))
            how = "along sea lanes (reconstructed)"
            if kmh_to_knots(length / max(h, 1 / 60)) > MAX_KN:
                continue                               # the sea-lane path is longer than the ship could have sailed
        out.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {**base, "km": round(length), "hours": round(h, 1), "how": how}})
    return out


def fleet_routes(s: Session, days: int = 365) -> dict:
    newest = s.scalar(select(func.max(Position.ts)))
    since = _utc(newest) - timedelta(days=days) if newest else None
    feats = []
    for v in s.scalars(select(Vessel).where(Vessel.watchlist)):
        feats += vessel_route(s, v.id, since=since)
    return {"type": "FeatureCollection", "days": days, "features": feats,
            "note": "Legs between reported positions are reconstructed along the open shipping-lane network "
                    "(Eurostat MARNET via searoute) - an estimate of the path, not a recorded track."}


def shipping_lanes(min_lat: float = 50.0) -> dict:
    """The open shipping-lane network itself (north of min_lat), for the 'Shipping lanes' map layer."""
    import json
    import os

    import searoute
    path = os.path.join(os.path.dirname(searoute.__file__), "data", "marnet_searoute.geojson")
    with open(path) as f:
        net = json.load(f)
    feats = []
    for ft in net["features"]:
        g = ft["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for line in lines:
            if any(p[1] >= min_lat for p in line):
                feats.append({"type": "Feature", "properties": {},
                              "geometry": {"type": "LineString", "coordinates": [[round(x, 2), round(y, 2)] for x, y in line]}})
    return {"type": "FeatureCollection", "features": feats,
            "source": "Eurostat MARNET global shipping-lane network (via searoute)"}
