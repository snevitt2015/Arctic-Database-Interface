"""
global_fishing_watch.py  -  Global Fishing Watch (GFW) API v3.

Free for NON-COMMERCIAL use. Get a token at
https://globalfishingwatch.org/our-apis/tokens/signup and set it as the
environment variable GFW_API_TOKEN. Attribute GFW in anything you publish.

What we pull, for every vessel, for ALL dates since 2012:
  * AIS gaps ("AIS off" events) -> the key signal for DARK SHIPS
  * port visits                  -> where the ship loaded / unloaded
  * encounters                   -> meetings with other ships (possible ship-to-ship transfer)
  * loitering                    -> slow drifting at sea (often waiting for a transfer)

Each event has a position and a time, so it becomes one row on the map.
Docs: https://globalfishingwatch.org/our-apis/documentation
"""
import time
import requests
import pandas as pd

from config import GFW_API_TOKEN, HISTORY_START_DATE
from tracker.schema import standardize, empty_table

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

EVENT_DATASETS = {
    "ais_off":    "public-global-gaps-events:latest",
    "port_visit": "public-global-port-visits-events:latest",
    "encounter":  "public-global-encounters-events:latest",
    "loitering":  "public-global-loitering-events:latest",
}


def _get(path: str, params: dict) -> dict:
    """One GET request to GFW, with a simple retry if we hit the rate limit."""
    headers = {"Authorization": f"Bearer {GFW_API_TOKEN}"}
    for attempt in range(3):
        r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=60)
        if r.status_code == 429:          # too many requests -> wait and retry
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _clean(value) -> str:
    return str(value or "").strip().upper()


def _accepted_ids(entry: dict, vessel, log=print) -> list:
    """Keep only the GFW identities that really belong to OUR ship.

    GFW joins identities together, and now and then an unrelated vessel slips in
    (e.g. a fishing boat that once used a similar radio ID). Rules:
      1. identity reports our IMO                      -> keep
      2. identity reports a DIFFERENT IMO              -> drop
      3. no IMO, but its MMSI is our MMSI, or an MMSI
         also used by an identity with our IMO         -> keep
      4. no IMO, but same name as a confirmed identity -> keep
      5. anything else                                 -> drop (and say so)
    If GFW gives no IMO for any identity, we fall back to keeping them all.
    """
    infos = [i for i in entry.get("selfReportedInfo", []) if i.get("id")]
    our_imo, our_mmsi = _clean(vessel["imo"]), _clean(vessel["mmsi"])
    if not our_imo or not any(_clean(i.get("imo")) for i in infos):
        confirmed = [i for i in infos if not our_mmsi or _clean(i.get("ssvid")) == our_mmsi] if not our_imo else infos
        return [i["id"] for i in (confirmed or infos)]

    with_our_imo = [i for i in infos if _clean(i.get("imo")) == our_imo]
    good_mmsis = {_clean(i.get("ssvid")) for i in with_our_imo} | {our_mmsi}
    good_names = {_clean(i.get("shipname")) for i in with_our_imo} | {_clean(vessel["name"])}
    keep = []
    for i in infos:
        imo = _clean(i.get("imo"))
        if imo == our_imo or (not imo and (_clean(i.get("ssvid")) in good_mmsis
                                           or _clean(i.get("shipname")) in good_names)):
            keep.append(i["id"])
        else:
            log(f"GFW: ignoring identity {i.get('shipname')} (MMSI {i.get('ssvid')}, IMO {imo or 'none'}) "
                f"- does not match {vessel['name']}")
    return keep


def find_gfw_ids(vessel, log=print) -> list:
    """GFW has its own vessel IDs. Look them up by IMO, then MMSI, then name.
    A ship can have several IDs over the years (after changing name, MMSI or flag);
    we keep all of them that check out, to cover all dates."""
    for query in [vessel["imo"], vessel["mmsi"], vessel["name"]]:
        if not query:
            continue
        data = _get("/vessels/search", {
            "query": query,
            "datasets[0]": "public-global-vessel-identity:latest",
        })
        ids = []
        for entry in data.get("entries", []):
            ids += _accepted_ids(entry, vessel, log)
        if ids:
            return list(dict.fromkeys(ids))   # remove duplicates, keep order
    return []


def _events_to_rows(events: list, event_type: str, vessel) -> list:
    """Convert GFW event JSON into rows in our standard format."""
    rows = []
    for ev in events:
        base = {
            "imo": vessel["imo"], "mmsi": (ev.get("vessel") or {}).get("ssvid") or vessel["mmsi"],
            "name": (ev.get("vessel") or {}).get("name") or vessel["name"],
            "source": "GFW", "event_type": event_type,
        }
        pos = ev.get("position") or {}

        if event_type == "ais_off":
            gap = ev.get("gap") or {}
            off = gap.get("offPosition") or pos
            on = gap.get("onPosition") or {}
            note = (f"AIS off {gap.get('durationHours', '?')} h, "
                    f"{gap.get('distanceKm', '?')} km travelled while dark, "
                    f"intentional={gap.get('intentionalDisabling', '?')}")
            rows.append({**base, "timestamp": ev.get("start"), "lat": off.get("lat"),
                         "lon": off.get("lon"), "note": note})
            if on.get("lat") is not None:
                rows.append({**base, "event_type": "ais_on", "timestamp": ev.get("end"),
                             "lat": on.get("lat"), "lon": on.get("lon"), "note": note})
        else:
            note = ""
            if event_type == "port_visit":
                visit = ev.get("port_visit") or {}
                anchorage = visit.get("intermediateAnchorage") or visit.get("startAnchorage") or {}
                note = f"Port: {anchorage.get('name', '?')} ({anchorage.get('flag', '')})"
            elif event_type == "encounter":
                other = (ev.get("encounter") or {}).get("vessel") or {}
                note = f"Met {other.get('name', '?')} (MMSI {other.get('ssvid', '?')})"
            rows.append({**base, "timestamp": ev.get("start"), "lat": pos.get("lat"),
                         "lon": pos.get("lon"), "note": note})
    return rows


def fetch(vessels: pd.DataFrame, start_date: str = HISTORY_START_DATE,
          end_date: str = None, log=print) -> pd.DataFrame:
    """Download all events for every vessel in the watch list."""
    if not GFW_API_TOKEN:
        log("GFW: no GFW_API_TOKEN set - skipping. (See README, step 3.)")
        return empty_table()

    end_date = end_date or pd.Timestamp.utcnow().strftime("%Y-%m-%d")
    rows = []
    for _, vessel in vessels.iterrows():
        try:
            ids = find_gfw_ids(vessel, log)
        except requests.RequestException as e:
            log(f"GFW: search failed for {vessel['name']}: {e}")
            continue
        if not ids:
            log(f"GFW: {vessel['name']} not found")
            continue

        for event_type, dataset in EVENT_DATASETS.items():
            offset = 0
            while True:                      # page through all results
                params = {"datasets[0]": dataset, "start-date": start_date,
                          "end-date": end_date, "limit": 100, "offset": offset}
                for i, gfw_id in enumerate(ids):
                    params[f"vessels[{i}]"] = gfw_id
                try:
                    data = _get("/events", params)
                except requests.RequestException as e:
                    log(f"GFW: {event_type} failed for {vessel['name']}: {e}")
                    break
                rows += _events_to_rows(data.get("entries", []), event_type, vessel)
                offset = data.get("nextOffset")
                if not offset:
                    break
        log(f"GFW: {vessel['name']} done")

    return standardize(pd.DataFrame(rows)) if rows else empty_table()
