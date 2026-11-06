"""
copernicus.py  -  Find free satellite images for each cue (Copernicus Data Space Ecosystem).

  Sentinel-1  radar (SAR)  - sees through cloud and polar night. GRD products.
  Sentinel-2  optical      - true colour, 10 m pixels, needs daylight and no cloud. L2A products.

SEARCHING needs no account. (Downloading image data does - see sar_chips.py.)
Licence: free and open. Credit "Contains modified Copernicus Sentinel data [year]".
API docs: https://documentation.dataspace.copernicus.eu/APIs/OData.html

For every cue we ask: which scenes were taken over the cue's circle during its time
window? Each scene row records what later steps need: exact UTC time, satellite,
mode, orbit direction, polarisation, footprint, whether it covers the cue's centre,
and (optical) the sun's elevation and azimuth at that moment.
"""
import json
import math
import re
import time
import requests
import pandas as pd

from tracker.geo import move_point
from tracker.sun import sun_position

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
MAX_CLOUD = 60            # % - skip optical scenes cloudier than this
MAX_SCENES_PER_SEARCH = 60

COLLECTIONS = {
    # name in our tables -> extra OData filter
    "SAR": "Collection/Name eq 'SENTINEL-1' and contains(Name,'_GRD')",
    "Optical": ("Collection/Name eq 'SENTINEL-2' and contains(Name,'MSIL2A') and "
                "Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' "
                f"and att/OData.CSC.DoubleAttribute/Value le {MAX_CLOUD:.2f})"),
}


# ------------------------------------------------------------------ geometry helpers
def circle_wkt(lat, lon, radius_km, points=16) -> str:
    """Search area as a WKT polygon (lon lat order). Clipped to valid coordinates."""
    ring = []
    for i in range(points):
        la, lo = move_point(lat, lon, 360 * i / points, radius_km)
        ring.append((max(-179.99, min(179.99, lo)), max(-85.0, min(85.0, la))))
    # a circle that crosses the date line would wrap the wrong way round - use a box instead
    lons = [p[0] for p in ring]
    if max(lons) - min(lons) > 180:
        west = [p[0] for p in ring if p[0] < 0] or [-179.99]
        east = [p[0] for p in ring if p[0] >= 0] or [179.99]
        lats = [p[1] for p in ring]
        lo_min, lo_max = (min(east), 179.99) if lon > 0 else (-179.99, max(west))
        ring = [(lo_min, min(lats)), (lo_max, min(lats)), (lo_max, max(lats)), (lo_min, max(lats))]
    ring.append(ring[0])
    return "POLYGON((" + ",".join(f"{x:.4f} {y:.4f}" for x, y in ring) + "))"


def _point_in_polygon(lon, lat, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def _outer_ring(geo):
    if not geo:
        return []
    c = geo.get("coordinates", [])
    if geo.get("type") == "MultiPolygon":
        c = max(c, key=lambda poly: len(poly[0]))      # largest part
    return c[0] if c else []


# ------------------------------------------------------------------ search
def _get(params, log):
    for attempt in range(3):
        try:
            r = requests.get(ODATA, params=params, timeout=90)
        except requests.RequestException as e:
            log(f"Copernicus: network problem ({e}); retrying")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            log(f"Copernicus: search refused ({r.status_code}): {r.text[:300]}")
            return None
        return r.json()
    log("Copernicus: search failed after retries")
    return None


def _attrs(product) -> dict:
    return {a.get("Name"): a.get("Value") for a in product.get("Attributes", [])}


def scene_row(product, kind, cue) -> dict:
    a = _attrs(product)
    start = pd.Timestamp(product["ContentDate"]["Start"])
    end = pd.Timestamp(product["ContentDate"]["End"])
    ring = _outer_ring(product.get("GeoFootprint"))
    covers = _point_in_polygon(cue["lon"], cue["lat"], ring) if ring else None
    mid = start + (end - start) / 2
    row = {
        "cue_id": cue["cue_id"], "vessel": cue["vessel"], "kind": kind,
        "scene_id": product.get("Name", ""), "product_uuid": product.get("Id", ""),
        "satellite": ("Sentinel-1" if kind == "SAR" else "Sentinel-2") + (a.get("platformSerialIdentifier") or ""),
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "mode": a.get("operationalMode") or a.get("productType", ""),
        "orbit_direction": a.get("orbitDirection", ""),
        "relative_orbit": a.get("relativeOrbitNumber", ""),
        "polarisation": a.get("polarisationChannels", ""),
        "cloud_cover": a.get("cloudCover"),
        "covers_cue_centre": covers,
        "footprint": json.dumps(ring, separators=(",", ":")) if ring else "",
        "online": product.get("Online"),
        "link": f"{ODATA}({product.get('Id')})",
    }
    # Optical: where was the sun? (shadow direction and length depend on it)
    if kind == "Optical":
        sun = sun_position(mid, cue["lat"], cue["lon"])
        row.update(sun_elevation=sun["sun_elevation"], sun_azimuth=sun["sun_azimuth"])
    else:
        row.update(sun_elevation=None, sun_azimuth=None)
    # How far was the scene time from the cue window? (0 = inside the window)
    row["hours_from_window"] = 0.0 if cue["start"] <= mid <= cue["end"] else round(
        min(abs((mid - cue["start"]).total_seconds()), abs((mid - cue["end"]).total_seconds())) / 3600, 1)
    return row


def search_cue(cue: dict, kinds=("SAR", "Optical"), pad_hours=12, log=print) -> list:
    """All Sentinel scenes over the cue's circle from (start - pad) to (end + pad)."""
    start = (pd.Timestamp(cue["start"]) - pd.Timedelta(hours=pad_hours)).tz_convert("UTC")
    end = (pd.Timestamp(cue["end"]) + pd.Timedelta(hours=pad_hours)).tz_convert("UTC")
    wkt = circle_wkt(cue["lat"], cue["lon"], cue["radius_km"])
    rows = []
    for kind in kinds:
        flt = (f"{COLLECTIONS[kind]} and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') "
               f"and ContentDate/Start ge {start:%Y-%m-%dT%H:%M:%S.000Z} "
               f"and ContentDate/Start le {end:%Y-%m-%dT%H:%M:%S.000Z}")
        data = _get({"$filter": flt, "$orderby": "ContentDate/Start asc",
                     "$top": MAX_SCENES_PER_SEARCH, "$expand": "Attributes"}, log)
        if data is None:
            continue
        for product in data.get("value", []):
            rows.append(scene_row(product, kind, cue))
        time.sleep(0.3)
    return rows


def search_all(cues: pd.DataFrame, previous: pd.DataFrame = None, log=print, max_cues=30) -> pd.DataFrame:
    """Search scenes for each cue. Cues whose window ended more than 10 days ago and were
    already searched are not searched again (the archive for them won't change much)."""
    previous = previous if previous is not None else pd.DataFrame()
    done = set(previous["cue_id"]) if not previous.empty else set()
    now = pd.Timestamp.now(tz="UTC")
    keep = previous[previous["cue_id"].isin(set(cues["cue_id"]))] if not previous.empty else previous
    rows, searched = [], 0
    for _, cue in cues.iterrows():
        settled = cue["end"] < now - pd.Timedelta(days=10)
        if cue["cue_id"] in done and settled:
            continue
        if searched >= max_cues:
            log(f"Copernicus: stopped after {max_cues} searches (rest next run)")
            break
        found = search_cue(cue.to_dict(), log=log)
        searched += 1
        log(f"Copernicus: {cue['vessel']} ({cue['kind']}) - {sum(r['kind'] == 'SAR' for r in found)} SAR, "
            f"{sum(r['kind'] == 'Optical' for r in found)} optical scenes")
        rows += found
        if not keep.empty:
            keep = keep[keep["cue_id"] != cue["cue_id"]]       # replaced by the fresh search
    new = pd.DataFrame(rows)
    out = pd.concat([keep, new], ignore_index=True) if not keep.empty else new
    return out
