"""
csv_import.py  -  Import position exports from MarineTraffic, TankerMap, VesselFinder, or any CSV/Excel.

Column names differ between sites; ALIASES maps the common ones to ours. Unknown columns are
ignored. Also used to load the vessel registry (Capstone database export) with load_registry().
"""
import pandas as pd

ALIASES = {
    "mmsi": ["mmsi", "ssvid", "MMSI"],
    "imo": ["imo", "IMO", "imo_number", "IMO number"],
    "name": ["name", "shipname", "ship_name", "vessel_name", "SHIPNAME", "Vessel Name", "Name"],
    "ts": ["ts", "timestamp", "time", "datetime", "TIMESTAMP", "last_position_utc", "Timestamp", "date"],
    "lat": ["lat", "latitude", "LAT", "Latitude"],
    "lon": ["lon", "lng", "longitude", "LON", "Longitude"],
    "sog": ["sog", "speed", "SPEED", "speed_kn", "Speed"],
    "cog": ["cog", "course", "COURSE", "Course"],
    "heading": ["heading", "HEADING", "Heading"],
    "flag": ["flag", "FLAG", "Flag"],
    "callsign": ["callsign", "call_sign", "CALLSIGN"],
    "draught_m": ["draught", "draught_m", "DRAUGHT"],
}
REGISTRY_ALIASES = {
    "imo": ["imo", "IMO", "IMO number"], "mmsi": ["mmsi", "MMSI"], "name": ["name", "Name", "Ship name"],
    "flag": ["flag", "Flag"], "ship_type": ["ship_type", "Type", "Ship type"],
    "classification": ["classification", "Class", "Category"], "ice_class": ["ice_class", "Ice class"],
    "length_m": ["length_m", "Length (m)", "LOA", "Length"], "beam_m": ["beam_m", "Beam (m)", "Beam", "Breadth"],
    "air_draft_m": ["air_draft_m", "Air draft (m)", "Air draft"],
    "superstructure_height_m": ["superstructure_height_m", "Superstructure height (m)"],
    "gross_tonnage": ["gross_tonnage", "GT", "Gross tonnage"],
    "watchlist": ["watchlist", "Watch list", "Tracked"], "sanctioned": ["sanctioned", "Sanctioned"],
}


def _rename(df, aliases):
    cols = {}
    for ours, names in aliases.items():
        for n in names:
            if n in df.columns and ours not in cols.values():
                cols[n] = ours
                break
    return df.rename(columns=cols)[[c for c in cols.values()]]


def _read(path):
    return pd.read_excel(path) if str(path).lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)


def load_positions(path_or_df, source: str) -> list[dict]:
    df = path_or_df if isinstance(path_or_df, pd.DataFrame) else _read(path_or_df)
    df = _rename(df, ALIASES)
    missing = {"mmsi", "ts", "lat", "lon"} - set(df.columns)
    if missing:
        raise ValueError(f"File is missing column(s): {', '.join(sorted(missing))}")
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "lat", "lon", "mmsi"])
    out = []
    for r in df.to_dict("records"):
        r = {k: (None if pd.isna(v) else v) for k, v in r.items()}
        r["mmsi"] = str(r["mmsi"]).removesuffix(".0")
        if r.get("imo") is not None:
            r["imo"] = str(r["imo"]).removesuffix(".0")
        r["ts"] = r["ts"].to_pydatetime()
        r["source"] = f"csv:{source}"
        out.append(r)
    return out


def load_registry(path_or_df) -> list[dict]:
    df = path_or_df if isinstance(path_or_df, pd.DataFrame) else _read(path_or_df)
    df = _rename(df, REGISTRY_ALIASES)
    out = []
    for r in df.to_dict("records"):
        r = {k: (None if pd.isna(v) else v) for k, v in r.items()}
        for k in ("imo", "mmsi"):
            if r.get(k) is not None:
                r[k] = str(r[k]).removesuffix(".0")
        for k in ("watchlist", "sanctioned"):
            if k in r:
                r[k] = str(r[k]).strip().lower() in ("1", "true", "yes", "y", "x", "checked")
        out.append(r)
    return out
