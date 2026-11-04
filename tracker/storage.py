"""
storage.py  -  Keep one growing file of every position from every date.

New downloads are ADDED to data/positions.csv (duplicates removed), so
history is never lost even if a website later deletes old data.
This file is also what GitHub Actions commits back to the repo each day.

Data from sites whose terms don't allow republishing (MarineTraffic, TankerMap)
goes to data/imports/private_positions.csv instead, which is NOT uploaded to
GitHub (see .gitignore). Both files are combined when you analyse.
"""
import pandas as pd

from config import POSITIONS_FILE, DEMO_FILE, PRIVATE_POSITIONS_FILE
from tracker.schema import standardize, empty_table

# Two rows are "the same" if these match
DEDUPE_KEYS = ["timestamp", "imo", "mmsi", "lat", "lon", "source", "event_type"]


def _read(path) -> pd.DataFrame:
    if not path.exists():
        return empty_table()
    return standardize(pd.read_csv(path, dtype={"imo": str, "mmsi": str}))


def load_positions(include_demo: bool = False) -> pd.DataFrame:
    tables = []
    for path in [POSITIONS_FILE, PRIVATE_POSITIONS_FILE]:
        if path.exists():
            tables.append(pd.read_csv(path, dtype={"imo": str, "mmsi": str}))
    if include_demo and DEMO_FILE.exists():
        tables.append(pd.read_csv(DEMO_FILE, dtype={"imo": str, "mmsi": str}))
    if not tables:
        return empty_table()
    return standardize(pd.concat(tables, ignore_index=True))


def save_positions(new_rows: pd.DataFrame, private: bool = False) -> int:
    """Append new rows to positions.csv (or the private file). Returns how many were genuinely new."""
    path = PRIVATE_POSITIONS_FILE if private else POSITIONS_FILE
    old = _read(path)
    new_rows = standardize(new_rows)
    combined = pd.concat([old, new_rows], ignore_index=True)
    combined["lat"] = combined["lat"].round(5)
    combined["lon"] = combined["lon"].round(5)
    # A re-downloaded GFW event replaces the older copy of itself (same event id)...
    has_id = combined["event_id"] != ""
    combined = pd.concat([combined[~has_id], combined[has_id].drop_duplicates("event_id", keep="last")])
    # ...and anything else identical is dropped
    combined = combined.drop_duplicates(subset=DEDUPE_KEYS).sort_values("timestamp")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    return len(combined) - len(old)
