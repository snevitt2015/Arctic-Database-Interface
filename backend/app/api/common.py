"""Shared helpers for the API routers."""
from datetime import datetime, timezone

from app.models import Anomaly, Position, Vessel


def utc(ts):
    if ts is None:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def iso(ts):
    return utc(ts).isoformat() if ts else None


def vessel_out(v: Vessel, risk: float = None) -> dict:
    return {"id": v.id, "imo": v.imo, "mmsi": v.mmsi, "name": v.name, "callsign": v.callsign, "flag": v.flag,
            "ship_type": v.ship_type, "classification": v.classification, "ice_class": v.ice_class,
            "length_m": v.length_m, "beam_m": v.beam_m, "air_draft_m": v.air_draft_m,
            "superstructure_height_m": v.superstructure_height_m, "gross_tonnage": v.gross_tonnage,
            "year_built": v.year_built,
            "watchlist": v.watchlist, "sanctioned": v.sanctioned, "notes": v.notes, "risk_score": risk}


def position_out(p: Position) -> dict:
    return {"id": p.id, "vessel_id": p.vessel_id, "mmsi": p.mmsi, "imo": p.imo, "name": p.name, "ts": iso(p.ts),
            "lat": p.lat, "lon": p.lon, "sog": p.sog, "cog": p.cog, "heading": p.heading,
            "nav_status": p.nav_status, "source": p.source}


def anomaly_out(a: Anomaly) -> dict:
    return {"id": a.id, "vessel_id": a.vessel_id, "mmsi": a.mmsi, "kind": a.kind, "severity": a.severity,
            "score": a.score, "start_ts": iso(a.start_ts), "end_ts": iso(a.end_ts), "lat": a.lat, "lon": a.lon,
            "lat2": a.lat2, "lon2": a.lon2, "summary": a.summary, "details": a.details, "source": a.source,
            "status": a.status}


def feature(lon, lat, props, geom_type="Point", coords=None):
    return {"type": "Feature", "geometry": {"type": geom_type, "coordinates": coords or [lon, lat]},
            "properties": props}


def osint_links(v: Vessel) -> list[dict]:
    """Deep links for the Live OSINT tab: open this ship on each public tracker."""
    links = []
    if v.imo:
        links += [
            {"source": "MarineTraffic", "url": f"https://www.marinetraffic.com/en/ais/details/ships/imo:{v.imo}"},
            {"source": "VesselFinder", "url": f"https://www.vesselfinder.com/vessels/details/{v.imo}"},
        ]
    elif v.mmsi:
        links.append({"source": "MarineTraffic", "url": f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{v.mmsi}"})
    links += [
        {"source": "Global Fishing Watch", "url": "https://globalfishingwatch.org/map",
         "hint": f"search {v.imo or v.mmsi or v.name}"},
        # set TANKERMAP_URL in the frontend config; TankerMap data comes in via CSV import
        {"source": "TankerMap", "url": None, "hint": f"search {v.name or v.imo}"},
    ]
    return links


def now_utc():
    return datetime.now(timezone.utc)
