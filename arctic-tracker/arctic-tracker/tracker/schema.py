"""
schema.py  -  The ONE standard format every data source must produce.

Every source (Global Fishing Watch, MarineTraffic, TankerMap, a CSV...)
is converted into rows with exactly these columns. Because everything
looks the same after this step, the rest of the code never needs to
know where a position came from.

To track a new feature (e.g. draught), add a column name here and fill
it in the sources that provide it.
"""
import pandas as pd

COLUMNS = [
    "timestamp",   # UTC time of the position (pandas datetime)
    "imo",         # IMO number (text, 7 digits) - best ID, never changes
    "mmsi",        # MMSI (text, 9 digits) - radio ID, can be changed by the ship
    "name",        # ship name as reported by the source
    "lat",         # latitude, decimal degrees (+ = North)
    "lon",         # longitude, decimal degrees (+ = East)
    "speed_kn",    # speed over ground in knots (may be empty)
    "course",      # course over ground, degrees 0-360 (direction of travel)
    "heading",     # direction the bow points, degrees 0-360 (may be empty)
    "draught_m",   # draught in metres (deeper = more cargo)
    "source",      # where it came from, e.g. "GFW", "MarineTraffic"
    "event_type",  # "position", "port_visit", "ais_off", "ais_on", "encounter", "loitering"
    "note",        # free text
]


def empty_table() -> pd.DataFrame:
    """An empty table with the standard columns."""
    return pd.DataFrame(columns=COLUMNS)


def clean_id(value) -> str:
    """Turn 9904546.0 / ' 9904546 ' / NaN into '9904546' or ''."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return "" if text.lower() in ("nan", "none") else text


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Make any table follow the standard format (adds missing columns, fixes types)."""
    df = df.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMNS]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for col in ["lat", "lon", "speed_kn", "course", "heading", "draught_m"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["imo", "mmsi"]:
        df[col] = df[col].map(clean_id)
    for col in ["name", "source", "event_type", "note"]:
        df[col] = df[col].fillna("").astype(str)
    df.loc[df["event_type"] == "", "event_type"] = "position"
    # A position without time or coordinates is useless
    df = df.dropna(subset=["timestamp", "lat", "lon"])
    return df.reset_index(drop=True)
