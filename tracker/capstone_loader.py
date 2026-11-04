"""
capstone_loader.py  -  Read the team's Arctic Capstone Database (exported from Airtable).

File: data/Arctic_Capstone_Database.xlsx. Tabs used:

  Ships                      registry of Russian-flagged ships (IMO, name, tonnage, type, year)
  AIS Updates                weekly manual AIS checks (area, speed, course, status, destination...)
  3D Models                  links to 3D models of ships
  SAR Imagery                radar satellite images (Sentinel-1, TerraSAR-X, RADARSAT-2 ...)
  Satellite Optical Imagery  optical satellite images (Maxar ...)
  Ship Type Statistics       fleet counts by type

"Tracked" ships = registry ships that have at least one linked AIS update,
3D model or image. They are ADDED to the Excel watch list automatically, so
Global Fishing Watch downloads their history too.

Airtable exports tab names like "Ships-Grid view"; we match on the start of the name,
so renaming views in Airtable won't break this.
"""
import re
import pandas as pd

from config import CAPSTONE_FILE
from tracker.schema import clean_id, standardize, empty_table


_source_note = {}


def _tab(start: str) -> pd.DataFrame:
    """Read a table: live from Airtable if a token is set, otherwise the Excel export tab
    whose name starts with `start` (empty table if missing)."""
    from tracker import airtable
    if airtable.is_configured():
        key = next((k for k in airtable.config.AIRTABLE_TABLES if k.lower().startswith(start.lower())), None)
        if key:
            try:
                df = airtable.load_table(key)
                _source_note[start] = "Airtable (live)"
                return df.dropna(how="all")
            except Exception as e:  # network/permission problem -> fall back to the export
                print(f"Airtable: {key} unavailable ({e}); using the Excel export instead")
    _source_note[start] = "Excel export"
    if not CAPSTONE_FILE.exists():
        return pd.DataFrame()
    book = pd.ExcelFile(CAPSTONE_FILE)
    for name in book.sheet_names:
        if name.strip().lower().startswith(start.lower()):
            return pd.read_excel(book, sheet_name=name).dropna(how="all")
    return pd.DataFrame()


def parse_latlon(text):
    """Find a position in free text.
    Handles '77.5N 68.2E', 'Lat 59.87N 30.19W', '59.87:30.19', '69.1, 33.4'. Returns (lat, lon) or None."""
    if not isinstance(text, str):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*°?\s*([NS])[\s,]+(\d+(?:\.\d+)?)\s*°?\s*([EW])", text, re.I)
    if m:
        lat = float(m.group(1)) * (-1 if m.group(2).upper() == "S" else 1)
        lon = float(m.group(3)) * (-1 if m.group(4).upper() == "W" else 1)
        return lat, lon
    m = re.search(r"(-?\d{1,2}\.\d+)\s*[:,]\s*(-?\d{1,3}\.\d+)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _split_links(value) -> list:
    return [s.strip() for s in str(value).split(",") if s.strip()] if pd.notna(value) else []


# ------------------------------------------------------------------ registry
def load_registry() -> pd.DataFrame:
    """All ships in the database: imo, name, gross_tonnage, type, year_built, flag, tracked."""
    df = _tab("Ships")
    if df.empty:
        return pd.DataFrame(columns=["imo", "name", "gross_tonnage", "type", "year_built", "flag", "tracked"])
    out = pd.DataFrame({
        "imo": df["IMO number"].map(clean_id),
        "name": df["Ship name"].astype(str).str.strip(),
        "gross_tonnage": pd.to_numeric(df["Gross tonnage"], errors="coerce"),
        "type": df["Vessel type"].astype(str).str.strip(),
        "year_built": pd.to_numeric(df["Year built"], errors="coerce").astype("Int64"),
        "flag": df["Flag / registry"].astype(str).str.replace(r"\s*\(.*\)", "", regex=True),
    })
    link_cols = [c for c in ["Linked 3D Models", "Linked SAR Imagery", "Linked Satellite Optical Imagery", "AIS Updates"] if c in df]
    out["tracked"] = df[link_cols].notna().any(axis=1).values
    out = out[(out["imo"] != "") & (out["name"].str.lower() != "ship name")]
    return out.drop_duplicates("imo").reset_index(drop=True)


# ------------------------------------------------------------------ AIS checks
def load_ais_checks() -> pd.DataFrame:
    """The weekly manual AIS checks. Rows that only hold a web link become `source_link` rows."""
    df = _tab("AIS Updates")
    if df.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "imo": df["Ship"].map(clean_id),
        "update_id": df["Update ID"].fillna("").astype(str),
        "mmsi": df["MMSI"].map(clean_id),
        "call_sign": df["Call sign"].fillna(""),
        "area": df["Last reported area"].fillna(""),
        "position_time": pd.to_datetime(df["Last position time (UTC)"], errors="coerce", utc=True),
        "position_age": df["Position age at check"].fillna(""),
        "ais_status": df["AIS status"].fillna(""),
        "nav_status": df["Nav status"].fillna(""),
        "speed_kn": pd.to_numeric(df["Speed (kn)"], errors="coerce"),
        "course": pd.to_numeric(df["Course (deg)"], errors="coerce"),
        "draught_m": pd.to_numeric(df["Draught (m)"], errors="coerce"),
        "destination": df["Reported destination"].fillna(""),
        "last_voyage": df["Last voyage"].fillna(""),
        "ais_source": df["AIS source"].fillna(""),
        "data_source": df["Data source"].fillna(""),
        "lat": pd.to_numeric(df["Latitude"], errors="coerce"),
        "lon": pd.to_numeric(df["Longitude"], errors="coerce"),
        "checked": pd.to_datetime(df["Checked (UTC)"], errors="coerce", utc=True),
        "notes": df["Notes"].fillna(""),
    })
    out["kind"] = ["source_link" if u.startswith("http") else "check" for u in out["update_id"]]
    return out[out["imo"] != ""].reset_index(drop=True)


def ais_checks_as_positions(checks: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    """AIS checks that have Latitude/Longitude filled in become positions on the map
    (and give the prediction a real speed and course)."""
    if checks.empty:
        return empty_table()
    c = checks[(checks["kind"] == "check") & checks["lat"].notna() & checks["lon"].notna()].copy()
    if c.empty:
        return empty_table()
    names = dict(zip(registry["imo"], registry["name"]))
    c["timestamp"] = c["position_time"].fillna(c["checked"])
    c["name"] = c["imo"].map(names).fillna("")
    c["source"] = "Capstone AIS check"
    c["event_type"] = "position"
    c["note"] = (c["area"] + " · " + c["nav_status"] + " · dest " + c["destination"]).str.strip(" ·")
    return standardize(c)


# ------------------------------------------------------------------ imagery, models
def load_imagery() -> pd.DataFrame:
    """SAR + optical satellite images in one table, with a parsed position where possible."""
    rows = []
    for _, r in _tab("SAR Imagery").iterrows():
        pos = parse_latlon(r.get("Geolocation"))
        rows.append({
            "kind": "SAR", "image_id": r.get("Image name"), "date": r.get("Acquisition date"),
            "platform": r.get("Sensor platform"), "resolution_m": r.get("Resolution (meters)"),
            "detail": f"{r.get('Polarization', '')} pol · incidence {r.get('Incidence angle (degrees)', '')}°",
            "location": r.get("Geolocation"), "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
            "imo": clean_id(r.get("Linked ship IMO number")) or clean_id(r.get("Linked ship")),
            "provider": r.get("Image source"), "link": r.get("Source reference"),
            "annotation": r.get("Annotation status"), "verification": r.get("Verification status"),
            "summary": r.get("SAR image summary"), "notes": r.get("Annotation improvement suggestions"),
        })
    for _, r in _tab("Satellite Optical").iterrows():
        if pd.isna(r.get("Image ID")):
            continue
        pos = parse_latlon(r.get("Location (lat/lon or AOI)")) or parse_latlon(r.get("Image ID"))
        rows.append({
            "kind": "Optical", "image_id": r.get("Image ID"), "date": r.get("Capture date"),
            "platform": r.get("Satellite name") if pd.notna(r.get("Satellite name")) else r.get("Sensor type"),
            "resolution_m": r.get("Ground Sample Distance(m)"),
            "detail": f"cloud {r.get('Cloud cover (%)', '')}% · off-nadir {r.get('Off Nadir Angle', '')}°",
            "location": r.get("Location (lat/lon or AOI)"), "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
            "imo": clean_id(r.get("Linked Ship IMO")) or clean_id(r.get("Linked ship")),
            "provider": r.get("Source provider"), "link": r.get("File download link"),
            "annotation": "", "verification": r.get("Verification status"),
            "summary": r.get("Analyst notes"), "notes": r.get("License / usage rights"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return df


def load_models() -> pd.DataFrame:
    df = _tab("3D Models")
    if df.empty:
        return pd.DataFrame(columns=["imo", "model_name", "source", "file_format", "link", "verification"])
    return pd.DataFrame({
        "imo": df["Linked ship"].map(clean_id), "model_name": df["Model name"],
        "source": df["Source"], "file_format": df["File format"], "link": df["File link"],
        "verification": df["Verification status"],
    })


def load_type_stats() -> pd.DataFrame:
    df = _tab("Ship Type Statistics")
    if df.empty:
        return pd.DataFrame(columns=["ship_type", "number_in_service"])
    return pd.DataFrame({"ship_type": df["Ship Type"], "number_in_service": df["Number in Service"]})


# ------------------------------------------------------------------ watch list
def tracked_vessels() -> pd.DataFrame:
    """Tracked ships in the same columns as the Excel watch list (for excel_loader)."""
    reg = load_registry()
    t = reg[reg["tracked"]].copy()
    if t.empty:
        return pd.DataFrame()
    checks = load_ais_checks()
    latest = (checks[checks["kind"] == "check"].sort_values("checked")
              .groupby("imo").tail(1).set_index("imo")) if not checks.empty else pd.DataFrame()
    t["mmsi"] = t["imo"].map(latest["mmsi"]) if not latest.empty else ""
    t["call_sign"] = t["imo"].map(latest["call_sign"]) if not latest.empty else ""
    t["notes"] = t["imo"].map(latest["notes"]) if not latest.empty else ""
    t["mmsi"] = t["mmsi"].fillna("")
    t["call_sign"] = t["call_sign"].fillna("")
    t["notes"] = t["notes"].fillna("")
    t["sanctioned"] = ""
    t["list_source"] = "Capstone database"
    # Registry names are upper case; make them read like the Excel list
    t["name"] = t["name"].str.title().str.replace(r"\bScf\b", "SCF", regex=True).str.replace(r"\bLng\b", "LNG", regex=True)
    return t[["name", "type", "flag", "imo", "mmsi", "call_sign", "sanctioned", "notes", "list_source",
              "gross_tonnage", "year_built"]]
