"""
gfw.py  -  Global Fishing Watch API v3 (free, non-commercial; token in GFW_API_TOKEN).

GFW's AIS gaps come from SATELLITE + terrestrial AIS and include its own "intentional
disabling" judgement, so they are the strongest gap evidence we have. We store:

  AIS gap events      -> Anomaly(kind="ais_gap", source="gfw")  (+ the off/on positions as pings)
  encounters          -> Anomaly(kind="encounter")              (possible ship-to-ship transfer)
  loitering           -> Anomaly(kind="loitering")
  port visits         -> pings (source="gfw"), so the track shows where the ship has been

Run for the watch list:   python -m app.workers.gfw_sync
Attribute GFW in anything published.
"""
import hashlib
import logging
import time
from datetime import date

import requests

from app import config
from app.analytics.spoofing import SEVERITY_SCORE

BASE = "https://gateway.api.globalfishingwatch.org/v3"
EVENT_DATASETS = {
    "ais_gap": "public-global-gaps-events:latest",
    "port_visit": "public-global-port-visits-events:latest",
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
}
log = logging.getLogger("gfw")


def _get(path, params, token=None):
    headers = {"Authorization": f"Bearer {token or config.GFW_API_TOKEN}"}
    for attempt in range(4):
        r = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=60)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def find_ids(imo=None, mmsi=None, name=None) -> list[str]:
    """GFW vessel ids whose identity really matches (an IMO mismatch is rejected)."""
    for q in (imo, mmsi, name):
        if not q:
            continue
        data = _get("/vessels/search", {"query": q, "datasets[0]": "public-global-vessel-identity:latest"})
        ids = []
        for entry in data.get("entries", []):
            for info in entry.get("selfReportedInfo", []):
                rep_imo = str(info.get("imo") or "")
                if imo and rep_imo and rep_imo != str(imo):
                    continue
                if info.get("id"):
                    ids.append(info["id"])
        if ids:
            return list(dict.fromkeys(ids))
    return []


def fetch_events(ids: list[str], start: str = "2012-01-01", end: str = None) -> dict[str, list]:
    end = end or date.today().isoformat()
    out = {}
    for kind, ds in EVENT_DATASETS.items():
        out[kind], offset = [], 0
        while True:
            params = {"datasets[0]": ds, "start-date": start, "end-date": end, "limit": 100, "offset": offset}
            params.update({f"vessels[{i}]": v for i, v in enumerate(ids)})
            data = _get("/events", params)
            out[kind] += data.get("entries", [])
            offset = data.get("nextOffset")
            if not offset:
                break
    return out


def to_records(events: dict[str, list], imo=None, fallback_mmsi=None):
    """GFW events -> (pings, anomalies) in our standard shapes."""
    pings, anomalies = [], []
    for kind, evs in events.items():
        for ev in evs:
            v = ev.get("vessel") or {}
            mmsi = str(v.get("ssvid") or fallback_mmsi or "")
            name = v.get("name")
            pos = ev.get("position") or {}
            key = hashlib.sha1(f"gfw|{ev.get('id')}".encode()).hexdigest()[:24]
            if kind == "ais_gap":
                g = ev.get("gap") or {}
                off, on = g.get("offPosition") or pos, g.get("onPosition") or {}
                h = float(g.get("durationHours") or 0)
                intentional = g.get("intentionalDisabling")
                sev = "high" if intentional or h >= 24 else "medium" if h >= 12 else "low"
                anomalies.append({
                    "dedupe_key": key, "kind": "ais_gap", "severity": sev, "score": SEVERITY_SCORE[sev],
                    "mmsi": mmsi, "start_ts": ev.get("start"), "end_ts": ev.get("end"),
                    "lat": off.get("lat"), "lon": off.get("lon"), "lat2": on.get("lat"), "lon2": on.get("lon"),
                    "summary": f"AIS off {h:.1f} h, {g.get('distanceKm', '?')} km while dark"
                               + (" - GFW judges it INTENTIONAL" if intentional else ""),
                    "details": {"hours": h, "km_moved": g.get("distanceKm"), "intentional": intentional,
                                "gfw_event_id": ev.get("id"), "sources": ["gfw"]}, "source": "gfw"})
                for p, t in ((off, ev.get("start")), (on, ev.get("end"))):
                    if p.get("lat") is not None:
                        pings.append({"mmsi": mmsi, "imo": imo, "name": name, "ts": t, "lat": p["lat"],
                                      "lon": p["lon"], "source": "gfw"})
            elif kind in ("encounter", "loitering"):
                other = (ev.get("encounter") or {}).get("vessel") or {}
                sev = "high" if kind == "encounter" else "medium"
                anomalies.append({
                    "dedupe_key": key, "kind": kind, "severity": sev, "score": SEVERITY_SCORE[sev], "mmsi": mmsi,
                    "start_ts": ev.get("start"), "end_ts": ev.get("end"), "lat": pos.get("lat"), "lon": pos.get("lon"),
                    "summary": (f"Met {other.get('name', '?')} (MMSI {other.get('ssvid', '?')}) at sea"
                                if kind == "encounter" else "Loitering at sea (slow drifting, often before a transfer)"),
                    "details": {"gfw_event_id": ev.get("id"), "other_vessel": other or None, "sources": ["gfw"]},
                    "source": "gfw"})
                if pos.get("lat") is not None:
                    pings.append({"mmsi": mmsi, "imo": imo, "name": name, "ts": ev.get("start"),
                                  "lat": pos["lat"], "lon": pos["lon"], "source": "gfw"})
            elif pos.get("lat") is not None:          # port visit
                pings.append({"mmsi": mmsi, "imo": imo, "name": name, "ts": ev.get("start"),
                              "lat": pos["lat"], "lon": pos["lon"], "source": "gfw"})
    return pings, anomalies
