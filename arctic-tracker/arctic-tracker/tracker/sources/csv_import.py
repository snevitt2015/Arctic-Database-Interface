"""
csv_import.py  -  Import ANY CSV/Excel export (TankerMap, MarineTraffic website,
VesselFinder, your own notes...).

TankerMap and the MarineTraffic website have no free API, but you can export
or copy their data into a spreadsheet. Drop the file in data/imports/ (or
upload it in the app) and it gets read here.

Column names are matched loosely - "Latitude", "LAT" and "lat" all work.
Add more spellings to COLUMN_ALIASES if a site uses different names.
"""
from pathlib import Path
import pandas as pd

from config import IMPORTS_DIR
from tracker.schema import standardize, empty_table

COLUMN_ALIASES = {
    "timestamp": ["timestamp", "time", "date", "datetime", "date_utc", "last_pos", "position_received", "utc"],
    "imo": ["imo", "imo_number", "imo number"],
    "mmsi": ["mmsi", "ssvid"],
    "name": ["name", "ship_name", "shipname", "vessel", "vessel_name", "vessel name"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "lng", "long", "longitude"],
    "speed_kn": ["speed", "speed_kn", "sog", "speed (kn)", "speed_knots"],
    "course": ["course", "cog", "course_deg"],
    "heading": ["heading", "hdg", "true_heading"],
    "draught_m": ["draught", "draft", "draught_m"],
    "event_type": ["event_type", "event"],
    "note": ["note", "notes", "comment", "status", "destination"],
}


def read_file(path, source_name: str = None) -> pd.DataFrame:
    """Read one CSV/XLSX file and convert it to the standard format."""
    path = Path(path) if not hasattr(path, "read") else path
    name = getattr(path, "name", str(path))
    if str(name).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    lookup = {str(c).strip().lower(): c for c in df.columns}
    renamed = {}
    for standard, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                renamed[lookup[alias]] = standard
                break
    df = df.rename(columns=renamed)
    if "source" not in df.columns:
        # Use the file name as the source, e.g. "tankermap_march.csv" -> "CSV: tankermap_march"
        df["source"] = source_name or f"CSV: {Path(str(name)).stem}"
    return standardize(df)


def fetch(vessels=None, log=print) -> pd.DataFrame:
    """Read every file in data/imports/."""
    files = sorted(p for p in Path(IMPORTS_DIR).glob("*") if p.suffix.lower() in (".csv", ".xlsx", ".xls"))
    if not files:
        log("CSV imports: no files in data/imports/")
        return empty_table()
    tables = []
    for f in files:
        try:
            t = read_file(f)
            log(f"CSV imports: {f.name} -> {len(t)} rows")
            tables.append(t)
        except Exception as e:  # a bad file should not stop the others
            log(f"CSV imports: could not read {f.name}: {e}")
    return pd.concat(tables, ignore_index=True) if tables else empty_table()
