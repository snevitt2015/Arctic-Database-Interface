"""
copernicus.py  -  Which Sentinel-1 (radar) and Sentinel-2 (optical) scenes cover a place and time?

Used to cross-cue: a spoofing anomaly ("AIS off here, 14:00-22:00") -> the free satellite images
that could show what the ship was really doing. Searching needs no account; downloading does.
Credit "Contains modified Copernicus Sentinel data [year]".
"""
import time
from datetime import datetime, timedelta, timezone

import requests

from app.analytics.geo import move_point
from app.analytics.sun import sun_position

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
COLLECTIONS = {
    "SAR": "Collection/Name eq 'SENTINEL-1' and contains(Name,'_GRD')",
    "Optical": "Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and "
               "Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and "
               "att/OData.CSC.DoubleAttribute/Value le 60.00)",
}


def circle_wkt(lat, lon, radius_km, n=24) -> str:
    pts = [move_point(lat, lon, 360 * i / n, radius_km) for i in range(n)]
    pts.append(pts[0])
    return "POLYGON((" + ",".join(f"{p[1]:.4f} {p[0]:.4f}" for p in pts) + "))"


def acquisition_key(name: str) -> str:
    """Sentinel-1 scenes are listed twice (standard + _COG copy); this is the shared part."""
    n = str(name).replace(".SAFE", "")
    parts = n.split("_")
    return "_".join(parts[:8]) if n.startswith("S1") and len(parts) >= 8 else n


def search_scenes(lat, lon, radius_km, start: datetime, end: datetime, kinds=("SAR", "Optical"), top=60) -> list:
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    wkt = circle_wkt(lat, lon, min(radius_km, 300))
    out, seen = [], set()
    for kind in kinds:
        flt = (f"{COLLECTIONS[kind]} and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') "
               f"and ContentDate/Start ge {start:%Y-%m-%dT%H:%M:%S.000Z} "
               f"and ContentDate/Start le {end:%Y-%m-%dT%H:%M:%S.000Z}")
        params = {"$filter": flt, "$orderby": "ContentDate/Start asc", "$top": top, "$expand": "Attributes"}
        data = None
        for attempt in range(3):
            r = requests.get(ODATA, params=params, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        for p in (data or {}).get("value", []):
            key = acquisition_key(p.get("Name", ""))
            if key in seen:
                continue
            seen.add(key)
            a = {x.get("Name"): x.get("Value") for x in p.get("Attributes", [])}
            t0 = datetime.fromisoformat(p["ContentDate"]["Start"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(p["ContentDate"]["End"].replace("Z", "+00:00"))
            mid = t0 + (t1 - t0) / 2
            row = {"kind": kind, "scene_id": p.get("Name"), "product_uuid": p.get("Id"),
                   "satellite": ("Sentinel-1" if kind == "SAR" else "Sentinel-2") + (a.get("platformSerialIdentifier") or ""),
                   "acquired_at": mid.isoformat(), "mode": a.get("operationalMode") or a.get("productType"),
                   "orbit_direction": a.get("orbitDirection"), "cloud_cover": a.get("cloudCover"),
                   "footprint": p.get("GeoFootprint"), "online": p.get("Online"), "link": f"{ODATA}({p.get('Id')})"}
            if kind == "Optical":
                sun = sun_position(mid, lat, lon)
                row.update(sun_elevation=sun["sun_elevation"], sun_azimuth=sun["sun_azimuth"],
                           shadow_usable=sun["sun_elevation"] > 0)
            out.append(row)
        time.sleep(0.3)
    return out


def search_for_anomaly(a, pad_hours=12) -> list:
    """Scenes for a spoofing anomaly: around both its points, from start - pad to end + pad."""
    start = a.start_ts - timedelta(hours=pad_hours)
    end = (a.end_ts or a.start_ts) + timedelta(hours=pad_hours)
    if a.lat2 is not None:
        from app.analytics.geo import distance_km
        lat, lon = (a.lat + a.lat2) / 2, (a.lon + a.lon2) / 2
        radius = distance_km(a.lat, a.lon, a.lat2, a.lon2) / 2 + 30
    else:
        lat, lon, radius = a.lat, a.lon, 50
    return search_scenes(lat, lon, radius, start, end)
