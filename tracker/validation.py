"""
validation.py  -  Catch data mistakes before they reach the algorithm.

Each check is one small function that returns a list of problems. run_all_checks()
runs them all on the Excel watch list and the Capstone Database and returns one
table:  severity | table | record | field | problem | value

  error    will give wrong results (bad IMO, impossible coordinates, wrong hemisphere)
  warning  will limit what the algorithm can do (no coordinates, date without time)
  info     worth knowing

The table is saved as data/output_data_checks.csv and shown in the viewer
("Data checks" in the Alerts drawer). To add a check: write a function and add
it to CHECKS at the bottom.
"""
import re
import pandas as pd

from tracker.geo import distance_km
from tracker.schema import clean_id


# ------------------------------------------------------------------ building blocks
def imo_is_valid(imo) -> bool:
    """IMO numbers have a built-in check digit: digits 1-6 times 7,6,5,4,3,2, summed; last digit of the sum = digit 7."""
    s = clean_id(imo)
    if not re.fullmatch(r"\d{7}", s):
        return False
    return sum(int(d) * w for d, w in zip(s[:6], range(7, 1, -1))) % 10 == int(s[6])


def mmsi_problem(mmsi) -> str:
    """'' if the MMSI looks fine, otherwise what's wrong with it."""
    s = clean_id(mmsi)
    if not s:
        return ""
    if not re.fullmatch(r"\d{9}", s):
        return f"MMSI should be 9 digits (has {len(s)})"
    if s[0] in "01":
        return "MMSI starting with 0/1 is a coast station or group call, not a ship"
    return ""


def _issue(severity, table, record, field, problem, value=""):
    return {"severity": severity, "table": table, "record": str(record), "field": field,
            "problem": problem, "value": "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)}


def _has_time(ts) -> bool:
    """False when a timestamp is really just a date (exactly midnight)."""
    t = pd.Timestamp(ts)
    return not (t.hour == 0 and t.minute == 0 and t.second == 0)


# ------------------------------------------------------------------ checks
def check_vessels(vessels, **_):
    out = []
    for _, v in vessels.iterrows():
        name = v["name"]
        if not v["imo"]:
            out.append(_issue("warning", "Watch list", name, "IMO", "No IMO - can only be matched by MMSI or name, which ships change"))
        elif not imo_is_valid(v["imo"]):
            out.append(_issue("error", "Watch list", name, "IMO", "IMO check digit is wrong - probably a typo", v["imo"]))
        p = mmsi_problem(v.get("mmsi"))
        if p:
            out.append(_issue("error", "Watch list", name, "MMSI", p, v.get("mmsi")))
        if not v.get("mmsi"):
            out.append(_issue("info", "Watch list", name, "MMSI", "No MMSI recorded"))
    dup = vessels[(vessels["imo"] != "") & vessels["imo"].duplicated(keep=False)]
    for imo, g in dup.groupby("imo"):
        out.append(_issue("error", "Watch list", ", ".join(g["name"]), "IMO", "Same IMO listed more than once", imo))
    return out


def check_registry(registry, **_):
    out = []
    bad = registry[~registry["imo"].map(imo_is_valid)]
    for _, r in bad.iterrows():
        out.append(_issue("error", "Ships (registry)", r["name"], "IMO number", "IMO check digit is wrong", r["imo"]))
    for imo, g in registry[registry["imo"].duplicated(keep=False)].groupby("imo"):
        out.append(_issue("error", "Ships (registry)", ", ".join(g["name"]), "IMO number", "Duplicate IMO", imo))
    for col, label in [("length_m", "Length overall"), ("beam_m", "Beam")]:
        if col not in registry.columns or registry[col].isna().all():
            out.append(_issue("warning", "Ships (registry)", "all ships", label,
                              "Field missing - needed to compare measured ship size in images against the real ship"))
            break
    return out


def check_ais_checks(checks, registry, **_):
    out = []
    if checks.empty:
        return out
    known = set(registry["imo"])
    c = checks[checks["kind"] == "check"]
    no_pos = c[c["lat"].isna() | c["lon"].isna()]
    if len(no_pos):
        out.append(_issue("warning", "AIS Updates", f"{len(no_pos)} of {len(c)} checks", "Latitude / Longitude",
                          "No coordinates - these checks can't be plotted or used to estimate positions"))
    for _, r in c.iterrows():
        rec = r["update_id"] or r["imo"]
        if r["imo"] not in known:
            out.append(_issue("error", "AIS Updates", rec, "Ship", "Linked ship is not in the Ships table", r["imo"]))
        if pd.notna(r["lat"]) and not -90 <= r["lat"] <= 90:
            out.append(_issue("error", "AIS Updates", rec, "Latitude", "Outside -90..90", r["lat"]))
        if pd.notna(r["lon"]) and not -180 <= r["lon"] <= 180:
            out.append(_issue("error", "AIS Updates", rec, "Longitude", "Outside -180..180", r["lon"]))
        if pd.notna(r["position_time"]) and pd.notna(r["checked"]) and r["position_time"] > r["checked"] + pd.Timedelta(minutes=5):
            out.append(_issue("error", "AIS Updates", rec, "Last position time", "Position time is after the time it was checked",
                              r["position_time"]))
        if pd.notna(r["speed_kn"]) and r["speed_kn"] > 30:
            out.append(_issue("warning", "AIS Updates", rec, "Speed (kn)", "Faster than any cargo ship - check units", r["speed_kn"]))
        p = mmsi_problem(r["mmsi"])
        if p:
            out.append(_issue("error", "AIS Updates", rec, "MMSI", p, r["mmsi"]))
    links = checks[checks["kind"] == "source_link"]
    for _, r in links.iterrows():
        out.append(_issue("info", "AIS Updates", r["imo"], "Update ID", "Row holds only a web link, not an AIS check", r["update_id"][:60]))
    return out


def check_imagery(imagery, ports, **_):
    """Imagery needs an exact time and a believable position - matching depends on both."""
    out = []
    if imagery.empty:
        return out
    named = [(p["name"].split(" (")[0].lower(), p["lat"], p["lon"]) for _, p in ports.dropna(subset=["lat", "lon"]).iterrows()]
    named += [("st petersburg", 59.93, 30.31), ("saint petersburg", 59.93, 30.31)]
    today = pd.Timestamp.now(tz="UTC").normalize()
    for _, i in imagery.iterrows():
        rec = i["image_id"]
        if pd.isna(i.get("lat")) or pd.isna(i.get("lon")):
            out.append(_issue("warning", f"{i['kind']} imagery", rec, "Location", "No position could be read - write it like 77.5N 68.2E", i.get("location")))
        else:
            text = f"{i.get('location', '')} {rec}".lower()
            for name, plat, plon in named:
                if name in text:
                    d = distance_km(i["lat"], i["lon"], plat, plon)
                    if d > 100:
                        hint = " - check the E/W or N/S letter" if distance_km(i["lat"], -i["lon"], plat, plon) < 100 else ""
                        out.append(_issue("error", f"{i['kind']} imagery", rec, "Location",
                                          f"Position is {d:,.0f} km from {name.title()}, which the text names{hint}",
                                          f"{i['lat']}, {i['lon']}"))
                    break
        date = pd.to_datetime(i.get("date"), errors="coerce")
        if pd.isna(date):
            out.append(_issue("error", f"{i['kind']} imagery", rec, "Date", "No acquisition date"))
        else:
            if not _has_time(date):
                out.append(_issue("warning", f"{i['kind']} imagery", rec, "Date",
                                  "Date only, no time - a ship moves hundreds of km in a day; record UTC time to the second", i.get("date")))
            if date.tz_localize(None) > today.tz_localize(None) + pd.Timedelta(days=1):
                out.append(_issue("error", f"{i['kind']} imagery", rec, "Date", "Acquisition date is in the future", i.get("date")))
        dated_name = re.search(r"(20\d{2})(\d{2})(\d{2})", str(rec))
        if dated_name and pd.notna(date):
            name_date = pd.Timestamp(f"{dated_name.group(1)}-{dated_name.group(2)}-{dated_name.group(3)}")
            if abs((name_date - date.tz_localize(None).normalize()).days) > 1:
                out.append(_issue("warning", f"{i['kind']} imagery", rec, "Date",
                                  f"Image name says {name_date:%Y-%m-%d} but the date field says {date:%Y-%m-%d}"))
        if not clean_id(i.get("imo")):
            out.append(_issue("info", f"{i['kind']} imagery", rec, "Linked ship", "Not linked to a ship yet"))
    return out


CHECKS = [check_vessels, check_registry, check_ais_checks, check_imagery]


def run_all_checks() -> pd.DataFrame:
    from tracker import excel_loader, capstone_loader as cap
    data = {
        "vessels": excel_loader.load_vessels(), "registry": cap.load_registry(), "checks": cap.load_ais_checks(),
        "imagery": cap.load_imagery(), "ports": excel_loader.load_ports(),
    }
    rows = [issue for check in CHECKS for issue in check(**data)]
    order = {"error": 0, "warning": 1, "info": 2}
    df = pd.DataFrame(rows, columns=["severity", "table", "record", "field", "problem", "value"])
    return df.sort_values("severity", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)
