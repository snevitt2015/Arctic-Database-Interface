"""
excel_loader.py  -  Read the Excel sheet of Russian vessels and ports.

The workbook has three tabs:
  Vessels    - our watch list (Name, Type, Flag, IMO, MMSI, Call Sign, ...)
  Ports      - Arctic ports (Name, Coordinates, ...)
  Operations - manual sightings (IMO, Date, Latitude, Longitude, Speed, Heading ...)

Rows typed into "Operations" are treated as one more OSINT source,
so analysts can add sightings by hand and they show up on the map.
"""
import re
import warnings
import pandas as pd

from config import EXCEL_FILE
from tracker.schema import clean_id, standardize

# Approximate coordinates for ports the sheet doesn't have coordinates for yet.
# VERIFY and then type the real values into the Excel "Coordinates" column
# (the Excel value always wins over this list).
FALLBACK_PORT_COORDS = {
    "sabetta": (71.27, 72.07),        # approx. - Yamal LNG, Gulf of Ob
    "arkhangelsk": (64.54, 40.52),    # approx.
    "tieshan (china)": (21.48, 109.57),  # approx. - Beihai LNG terminal, Tieshan port
}


def _read_tab(sheet_name: str, must_have: str) -> pd.DataFrame:
    """Read a tab and find the real header row (the one that contains `must_have`)."""
    raw = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name, header=None)
    for i, row in raw.iterrows():
        if must_have in [str(v).strip() for v in row.values]:
            table = raw.iloc[i + 1:].copy()
            table.columns = [str(c).strip() for c in raw.iloc[i]]
            return table.dropna(how="all").reset_index(drop=True)
    raise ValueError(f"Could not find a '{must_have}' header in tab '{sheet_name}'")


def load_vessels() -> pd.DataFrame:
    """Watch list of vessels with clean IMO/MMSI text columns."""
    df = _read_tab("Vessels", "IMO")
    df = df.rename(columns={
        "Name": "name", "Type": "type", "Flag": "flag", "IMO": "imo", "MMSI": "mmsi",
        "Call Sign": "call_sign", "AIS Class": "ais_class", "Draught": "draught",
        "Sanctioned": "sanctioned", "Notes": "notes", "Last Verified Date": "last_verified",
    })
    df["imo"] = df["imo"].map(clean_id)
    df["mmsi"] = df["mmsi"].map(clean_id)
    df["name"] = df["name"].astype(str).str.strip()
    df["sanctioned"] = df["sanctioned"].fillna("").astype(str)

    # Data-quality warnings (shown in the app too)
    problems = []
    for _, v in df.iterrows():
        if v["mmsi"] and len(v["mmsi"]) != 9:
            problems.append(f"{v['name']}: MMSI '{v['mmsi']}' is not 9 digits")
        if not v["imo"]:
            problems.append(f"{v['name']}: no IMO number (will match on MMSI/name only)")
    df.attrs["problems"] = problems
    return df


def _parse_coordinates(text):
    """Turn '68°58′48″N 33°03′22″E' or '64°34′N 39°52′E' into (68.98, 33.056)."""
    if not isinstance(text, str):
        return None
    parts = re.findall(r"(\d+(?:\.\d+)?)°\s*(?:(\d+(?:\.\d+)?)[′'])?\s*(?:(\d+(?:\.\d+)?)[″\"])?\s*([NSEW])", text)
    if len(parts) != 2:
        return None
    values = []
    for deg, minutes, seconds, hemi in parts:
        val = float(deg) + float(minutes or 0) / 60 + float(seconds or 0) / 3600
        values.append(-val if hemi in "SW" else val)
    return values[0], values[1]


def load_ports() -> pd.DataFrame:
    """Ports with decimal lat/lon. `coords_source` tells you if the value is from Excel or approximate."""
    df = _read_tab("Ports", "Name")
    df = df.rename(columns={"Name": "name", "Coordinates": "coordinates", "Notes": "notes",
                            "Berths": "berths", "Depth/Capacity": "depth_capacity",
                            "Managing Company": "managing_company", "Primary Use": "primary_use",
                            "Notable Infrastructure": "infrastructure"})
    lats, lons, srcs = [], [], []
    for _, p in df.iterrows():
        coords = _parse_coordinates(p.get("coordinates"))
        src = "excel"
        if coords is None:
            coords = FALLBACK_PORT_COORDS.get(str(p["name"]).strip().lower())
            src = "approximate - verify" if coords else "missing"
        lats.append(coords[0] if coords else None)
        lons.append(coords[1] if coords else None)
        srcs.append(src)
    df["lat"], df["lon"], df["coords_source"] = lats, lons, srcs
    return df


def load_operations() -> pd.DataFrame:
    """Manual sightings from the 'Operations' tab, in the standard position format."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _read_tab("Operations", "IMO")
    df = df.rename(columns={
        "IMO": "imo", "Date": "timestamp", "Latitude": "lat", "Longitude": "lon",
        "Speed": "speed_kn", "Heading": "heading",
    })
    df["course"] = df.get("heading")          # sheet has one direction column
    df["note"] = (df.get("Port", "").fillna("").astype(str) + " " +
                  df.get("AIS Status", "").fillna("").astype(str) + " " +
                  df.get("Region", "").fillna("").astype(str)).str.strip()
    df["source"] = "Excel Operations"
    return standardize(df)   # rows without date/lat/lon are dropped automatically
