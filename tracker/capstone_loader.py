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
_force_excel = False   # set after a problem with live Airtable data, so the rest of the run uses the export

# Every field the code reads, per table. Airtable's API leaves out fields that are empty in
# EVERY record (e.g. Latitude today), so missing ones are added back as empty columns.
EXPECTED_FIELDS = {
    "Ships": ["IMO number", "Ship name", "Gross tonnage", "Vessel type", "Year built", "Flag / registry",
              "Length overall (m)", "Beam (m)", "Air draft (m)",
              "Linked 3D Models", "Linked SAR Imagery", "Linked Satellite Optical Imagery", "AIS Updates"],
    "AIS Updates": ["Update ID", "Ship", "MMSI", "Call sign", "Last reported area", "Last position time (UTC)",
                    "Position age at check", "AIS status", "Nav status", "Speed (kn)", "Course (deg)", "Draught (m)",
                    "Reported destination", "Last voyage", "AIS source", "Data source", "Latitude", "Longitude",
                    "Checked (UTC)", "Notes"],
    "3D Models": ["Model name", "Linked ship", "Source", "File format", "File link", "Verification status"],
    "SAR Imagery": ["Image name", "Linked ship", "Acquisition date", "Sensor platform", "Resolution (meters)",
                    "Polarization", "Incidence angle (degrees)", "Geolocation", "Image source", "Source reference",
                    "Annotation status", "Verification status", "Linked ship IMO number", "SAR image summary",
                    "Annotation improvement suggestions"],
    "Satellite Optical": ["Image ID", "Capture date", "Satellite name", "Sensor type", "Ground Sample Distance(m)",
                          "Cloud cover (%)", "Off Nadir Angle", "Location (lat/lon or AOI)", "File download link",
                          "Source provider", "License / usage rights", "Linked ship", "Verification status",
                          "Analyst notes", "Linked Ship IMO"],
    "Ship Type Statistics": ["Ship Type", "Number in Service"],
}


def _with_expected(df: pd.DataFrame, start: str) -> pd.DataFrame:
    if df.empty and not len(df.columns):
        return df
    for col in EXPECTED_FIELDS.get(start, []):
        if col not in df.columns:
            df[col] = None
    return df


def _safe(fn):
    """If live Airtable data breaks a loader, say why and redo it from the Excel export,
    so one odd record never stops the daily run."""
    def wrapper(*args, **kwargs):
        global _force_excel
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            from tracker import airtable
            if _force_excel or not airtable.is_configured():
                raise
            print(f"Airtable data problem in {fn.__name__}: {type(e).__name__}: {e} - using the Excel export instead")
            _force_excel = True
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _tab(start: str) -> pd.DataFrame:
    return _with_expected(_read_tab(start), start)


def _read_tab(start: str) -> pd.DataFrame:
    """Read a table: live from Airtable if a token is set, otherwise the Excel export tab
    whose name starts with `start` (empty table if missing)."""
    from tracker import airtable
    if airtable.is_configured() and not _force_excel:
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
@_safe
def load_registry(include_invalid: bool = False) -> pd.DataFrame:
    """All ships in the database: imo, name, gross_tonnage, type, year_built, flag, tracked.

    Records whose IMO field isn't a 7-digit number (e.g. a header row imported as a ship:
    'IMO number' / 'Name of ship') are left out, unless include_invalid=True (the data checks
    use that to list them)."""
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
        # dimensions (add these fields in Airtable - see docs/airtable_fields.md); empty until then
        "length_m": pd.to_numeric(df["Length overall (m)"], errors="coerce"),
        "beam_m": pd.to_numeric(df["Beam (m)"], errors="coerce"),
        "air_draft_m": pd.to_numeric(df["Air draft (m)"], errors="coerce"),
    })
    link_cols = [c for c in ["Linked 3D Models", "Linked SAR Imagery", "Linked Satellite Optical Imagery", "AIS Updates"] if c in df]
    out["tracked"] = df[link_cols].notna().any(axis=1).values
    out = out[(out["imo"] != "") & (out["name"].str.lower() != "ship name")]
    if not include_invalid:
        out = out[out["imo"].str.fullmatch(r"\d{7}")]
    return out.drop_duplicates("imo").reset_index(drop=True)


# ------------------------------------------------------------------ AIS checks
@_safe
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


# ------------------------------------------------------------------ area names -> coarse positions
_gazetteer = None


def gazetteer() -> pd.DataFrame:
    global _gazetteer
    if _gazetteer is None:
        from config import GAZETTEER_FILE
        g = pd.read_csv(GAZETTEER_FILE) if GAZETTEER_FILE.exists() else pd.DataFrame(
            columns=["name", "aliases", "lat", "lon", "radius_km", "kind"])
        g["alias_list"] = g["aliases"].fillna("").str.split("|")
        _gazetteer = g
    return _gazetteer


def area_to_position(area: str):
    """Turn an AIS area name into (lat, lon, radius_km, matched_name), or None.

    'Barents Sea (Murmansk)' -> Murmansk, ±20 km   (the place in brackets is more precise)
    'Gulf of Finland (Bolshoy Bor)' -> Gulf of Finland, ±200 km   (Bolshoy Bor not in the list)
    Add places to data/region_gazetteer.csv - longer names win, so 'lower volga' beats 'volga'."""
    if not isinstance(area, str) or not area.strip():
        return None
    text = area.lower()
    inside = " ".join(re.findall(r"\(([^)]*)\)", text))
    outside = re.sub(r"\([^)]*\)", " ", text)
    for part in (inside, outside):
        best = None
        for _, g in gazetteer().iterrows():
            for alias in g["alias_list"]:
                if alias and re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", part):
                    if best is None or len(alias) > best[0]:
                        best = (len(alias), g)
        if best:
            g = best[1]
            return float(g["lat"]), float(g["lon"]), float(g["radius_km"]), g["name"]
    return None


def ais_checks_as_positions(checks: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    """Every AIS check becomes a position:
      * with Latitude/Longitude filled in -> a precise position (event 'position', ±0.5 km)
      * with only an area name            -> the area's centre with its radius as the error
                                             (event 'area_report', e.g. Barents Sea ±700 km)
    Area reports are drawn on the map and used for 'where was it roughly', but are kept out of
    speed calculations and predictions, where they would give nonsense."""
    if checks.empty:
        return empty_table()
    c = checks[checks["kind"] == "check"].copy()
    if c.empty:
        return empty_table()
    precise = c["lat"].notna() & c["lon"].notna()
    found = [None if p else area_to_position(a) for p, a in zip(precise, c["area"])]
    c["lat"] = [lat if p else (f[0] if f else None) for lat, f, p in zip(c["lat"], found, precise)]
    c["lon"] = [lon if p else (f[1] if f else None) for lon, f, p in zip(c["lon"], found, precise)]
    c["pos_error_km"] = [0.5 if p else (f[2] if f else None) for f, p in zip(found, precise)]
    c["event_type"] = ["position" if p else "area_report" for p in precise]
    area_note = [("" if p else f" (placed at {f[3]} ±{f[2]:.0f} km)" if f else " (area not recognised)")
                 for f, p in zip(found, precise)]
    names = dict(zip(registry["imo"], registry["name"]))
    c["timestamp"] = c["position_time"].fillna(c["checked"])
    c["name"] = c["imo"].map(names).fillna("")
    c["source"] = "Capstone AIS check"
    c["note"] = [(f"{a}{n} · {s} · dest {d}").strip(" ·") for a, n, s, d in
                 zip(c["area"], area_note, c["nav_status"], c["destination"])]
    return standardize(c)


# ------------------------------------------------------------------ imagery, models
@_safe
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


@_safe
def load_models() -> pd.DataFrame:
    df = _tab("3D Models")
    if df.empty:
        return pd.DataFrame(columns=["imo", "model_name", "source", "file_format", "link", "verification"])
    return pd.DataFrame({
        "imo": df["Linked ship"].map(clean_id), "model_name": df["Model name"],
        "source": df["Source"], "file_format": df["File format"], "link": df["File link"],
        "verification": df["Verification status"],
    })


@_safe
def load_type_stats() -> pd.DataFrame:
    df = _tab("Ship Type Statistics")
    if df.empty:
        return pd.DataFrame(columns=["ship_type", "number_in_service"])
    return pd.DataFrame({"ship_type": df["Ship Type"], "number_in_service": df["Number in Service"]})


# ------------------------------------------------------------------ watch list
@_safe
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
