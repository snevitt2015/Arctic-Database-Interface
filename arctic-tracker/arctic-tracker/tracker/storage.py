"""
storage.py  -  Keep one growing file of every position from every date.

New downloads are ADDED to data/positions.csv (duplicates removed), so
history is never lost even if a website later deletes old data.
This file is also what GitHub Actions commits back to the repo each day.
"""
import pandas as pd

from config import POSITIONS_FILE, DEMO_FILE
from tracker.schema import standardize, empty_table

# Two rows are "the same" if these match
DEDUPE_KEYS = ["timestamp", "imo", "mmsi", "lat", "lon", "source", "event_type"]


def load_positions(include_demo: bool = False) -> pd.DataFrame:
    tables = []
    if POSITIONS_FILE.exists():
        tables.append(pd.read_csv(POSITIONS_FILE, dtype={"imo": str, "mmsi": str}))
    if include_demo and DEMO_FILE.exists():
        tables.append(pd.read_csv(DEMO_FILE, dtype={"imo": str, "mmsi": str}))
    if not tables:
        return empty_table()
    return standardize(pd.concat(tables, ignore_index=True))


def save_positions(new_rows: pd.DataFrame) -> int:
    """Append new rows to positions.csv. Returns how many were genuinely new."""
    old = load_positions(include_demo=False)
    new_rows = standardize(new_rows)
    combined = pd.concat([old, new_rows], ignore_index=True)
    combined["lat"] = combined["lat"].round(5)
    combined["lon"] = combined["lon"].round(5)
    combined = combined.drop_duplicates(subset=DEDUPE_KEYS).sort_values("timestamp")
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(POSITIONS_FILE, index=False)
    return len(combined) - len(old)
