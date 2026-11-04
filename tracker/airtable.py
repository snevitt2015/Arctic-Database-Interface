"""
airtable.py  -  Read from and write to the team's Airtable base directly.

Without this, someone has to export the base to Excel and replace
data/Arctic_Capstone_Database.xlsx by hand. With it, every run reads the live base,
and the SAR pipeline (later) can write its detections straight back in.

Setup (once):
  1. https://airtable.com/create/tokens -> Create token
       scopes: data.records:read  (and data.records:write for writing back)
       access: the Arctic Capstone base
  2. Locally: put AIRTABLE_TOKEN=... in .env
     GitHub:  Settings -> Secrets and variables -> Actions -> New secret AIRTABLE_TOKEN
  3. Check AIRTABLE_BASE_ID and the table names in config.py match your base.

If no token is set, everything falls back to the Excel export - nothing breaks.

Airtable limits: 5 requests per second per base, 100 records per page, 10 records per write.
API docs: https://airtable.com/developers/web/api/introduction
"""
import time
import requests
import pandas as pd

import config

API = "https://api.airtable.com/v0"
_cache = {}


def is_configured() -> bool:
    return bool(config.AIRTABLE_TOKEN and config.AIRTABLE_BASE_ID)


def _headers():
    return {"Authorization": f"Bearer {config.AIRTABLE_TOKEN}", "Content-Type": "application/json"}


def _request(method, table, **kwargs):
    url = f"{API}/{config.AIRTABLE_BASE_ID}/{requests.utils.quote(table, safe='')}"
    for attempt in range(4):
        r = requests.request(method, url, headers=_headers(), timeout=60, **kwargs)
        if r.status_code == 429:                 # rate limited: Airtable asks for a 30 s pause
            time.sleep(30)
            continue
        r.raise_for_status()
        time.sleep(0.22)                         # stay under 5 requests/second
        return r.json()
    r.raise_for_status()


def fetch_records(table: str) -> list:
    """Every record in a table: [{"id": "rec...", "fields": {...}}, ...]"""
    if table in _cache:
        return _cache[table]
    records, offset = [], None
    while True:
        params = {"pageSize": 100, **({"offset": offset} if offset else {})}
        data = _request("GET", table, params=params)
        records += data.get("records", [])
        offset = data.get("offset")
        if not offset:
            break
    _cache[table] = records
    return records


def _flatten(value, id_index):
    """Make Airtable values look like the Excel export:
    linked records -> their primary field, lookups/multi-selects -> 'a, b', attachments -> URLs."""
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, str) and v.startswith("rec") and v in id_index:
                parts.append(str(id_index[v]))
            elif isinstance(v, dict):
                parts.append(v.get("url") or v.get("name") or str(v))
            else:
                parts.append(str(v))
        return ", ".join(parts)
    if isinstance(value, dict):
        return value.get("url") or value.get("name") or value.get("label") or str(value)
    return value


def load_table(key: str) -> pd.DataFrame:
    """Read one configured table (key = 'Ships', 'AIS Updates', ...) as a DataFrame with the same
    column names as the Excel export. Linked-record IDs are replaced with readable values."""
    tables = config.AIRTABLE_TABLES
    if key not in tables:
        return pd.DataFrame()
    # index of record id -> primary value across all tables, so links can be resolved
    id_index = {}
    for k, t in tables.items():
        try:
            for rec in fetch_records(t["table"]):
                id_index[rec["id"]] = rec["fields"].get(t["primary"], rec["id"])
        except requests.HTTPError as e:
            print(f"Airtable: could not read table '{t['table']}': {e}")
    rows = [{f: _flatten(v, id_index) for f, v in rec["fields"].items()} | {"_airtable_id": rec["id"]}
            for rec in fetch_records(tables[key]["table"])]
    return pd.DataFrame(rows)


def upsert(key: str, rows: list, merge_on: list) -> int:
    """Write rows into a table, updating existing records whose `merge_on` fields match
    (so re-running never creates duplicates). Returns how many rows were sent.
    Used later by the SAR pipeline to put detections into the base."""
    table = config.AIRTABLE_TABLES[key]["table"]
    sent = 0
    for i in range(0, len(rows), 10):
        batch = [{"fields": {k: v for k, v in r.items() if v is not None and v == v}} for r in rows[i:i + 10]]
        _request("PATCH", table, json={"performUpsert": {"fieldsToMergeOn": merge_on},
                                       "records": batch, "typecast": True})
        sent += len(batch)
    return sent
