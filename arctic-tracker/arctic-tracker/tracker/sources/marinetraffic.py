"""
marinetraffic.py  -  MarineTraffic API (PAID - credits per call).

Set MARINETRAFFIC_API_KEY to use it. Without a key this source is skipped;
you can still download a CSV from the MarineTraffic website and drop it
into data/imports/ (handled by csv_import.py).

MarineTraffic gives the actual AIS track: lat, lon, speed, course, heading.
NOTE: endpoint names and versions depend on your plan (MarineTraffic is
now part of Kpler). Check the endpoint below against your account's API
docs before spending credits.
"""
import requests
import pandas as pd

from config import MARINETRAFFIC_API_KEY
from tracker.schema import standardize, empty_table

# "Vessel historical track" endpoint (PS01). Adjust to your plan if needed.
TRACK_URL = "https://services.marinetraffic.com/api/exportvesseltrack/{key}"


def fetch(vessels: pd.DataFrame, days: int = 180, log=print) -> pd.DataFrame:
    if not MARINETRAFFIC_API_KEY:
        log("MarineTraffic: no MARINETRAFFIC_API_KEY set - skipping.")
        return empty_table()

    rows = []
    for _, v in vessels.iterrows():
        params = {"v": 3, "days": days, "period": "hourly", "protocol": "jsono"}
        if v["imo"]:
            params["imo"] = v["imo"]
        elif v["mmsi"]:
            params["mmsi"] = v["mmsi"]
        else:
            continue
        try:
            r = requests.get(TRACK_URL.format(key=MARINETRAFFIC_API_KEY), params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log(f"MarineTraffic: failed for {v['name']}: {e}")
            continue
        for p in data if isinstance(data, list) else []:
            rows.append({
                "timestamp": p.get("TIMESTAMP"), "imo": v["imo"], "mmsi": p.get("MMSI") or v["mmsi"],
                "name": v["name"], "lat": p.get("LAT"), "lon": p.get("LON"),
                # MarineTraffic reports speed in tenths of a knot (e.g. 125 = 12.5 kn)
                "speed_kn": float(p["SPEED"]) / 10 if p.get("SPEED") not in (None, "") else None,
                "course": p.get("COURSE"), "heading": p.get("HEADING"),
                "source": "MarineTraffic", "event_type": "position",
            })
        log(f"MarineTraffic: {v['name']} -> {len(data) if isinstance(data, list) else 0} points")

    return standardize(pd.DataFrame(rows)) if rows else empty_table()
