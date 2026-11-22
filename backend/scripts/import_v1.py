"""
import_v1.py  -  Load the team's REAL data from the v1 repo into the v2 database.

    python scripts/import_v1.py --v1 "C:\\path\\to\\Arctic-Database-Interface"

What comes in (nothing is generated or estimated):
  * Watch list   data/Russian_Tankers_and_Port_Data.xlsx (Vessels) + ships the Capstone database tracks
  * Registry     data/Arctic_Capstone_Database.xlsx (Ships tab, ~4,170 ships: IMO, name, type, flag, GT, year)
  * GFW history  data/positions.csv (Global Fishing Watch events since 2012):
                   port visits / loitering / encounters -> positions (source "gfw")
                   AIS-off events                       -> ais_gap anomalies exactly as GFW reported them
                   encounters                           -> encounter anomalies
Then the detectors that are valid for event data run over it: impossible jumps, identity changes,
IMO clones and invalid IDs. (Gap and on/off detectors only run on continuous AIS feeds.)

Safe to re-run: records are matched, not duplicated.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.analytics.spoofing import SEVERITY_SCORE  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.analytics.records import check_imagery, check_model  # noqa: E402
from app.models import DataCheck, Position, ReferenceRecord, Vessel  # noqa: E402
from app.services.ingest import run_detection, store_pings, upsert_anomalies  # noqa: E402


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    t = str(v).strip().removesuffix(".0")
    return t or None


def _f(v):
    try:
        x = float(str(v).lower().replace("m", "").strip())
        return None if pd.isna(x) else x
    except (TypeError, ValueError):
        return None


def upsert(s, imo, mmsi, **fields):
    v = s.scalar(select(Vessel).where(Vessel.imo == imo)) if imo else None
    if v is None and mmsi:
        v = s.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    if v is None:
        v = Vessel(imo=imo, mmsi=mmsi)
        s.add(v)
    if mmsi and not v.mmsi:
        v.mmsi = mmsi
    for k, val in fields.items():
        if val is not None and (getattr(v, k) in (None, "", False) or k in ("watchlist", "sanctioned", "notes")):
            setattr(v, k, val)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True, help="folder of the v1 repo (Arctic-Database-Interface)")
    args = ap.parse_args()
    v1 = Path(args.v1).resolve()
    if not (v1 / "tracker").is_dir():
        raise SystemExit(f"{v1} doesn't look like the v1 repo (no tracker/ folder)")
    sys.path.insert(0, str(v1))
    import os
    os.chdir(v1)                                   # v1 loaders use paths relative to the repo
    from tracker import capstone_loader, excel_loader

    init_db()
    s = SessionLocal()

    # 1. registry (all ships), then the watch list on top
    reg = capstone_loader.load_registry()
    for r in reg.to_dict("records"):
        upsert(s, _s(r["imo"]), None, name=_s(r["name"]), ship_type=_s(r["type"]), flag=_s(r["flag"]),
               gross_tonnage=_f(r["gross_tonnage"]), length_m=_f(r.get("length_m")), beam_m=_f(r.get("beam_m")),
               year_built=int(_f(r.get("year_built"))) if _f(r.get("year_built")) else None,
               air_draft_m=_f(r.get("air_draft_m")))
    s.flush()
    watch = excel_loader.load_vessels(include_capstone=True)
    for r in watch.to_dict("records"):
        sanctioned = str(r.get("sanctioned") or "").strip().lower() in ("yes", "y", "true", "1", "x", "sanctioned")
        upsert(s, _s(r["imo"]), _s(r["mmsi"]), name=_s(r["name"]), ship_type=_s(r["type"]), flag=_s(r["flag"]),
               callsign=_s(r.get("call_sign")), gross_tonnage=_f(r.get("gross_tonnage")), watchlist=True,
               sanctioned=sanctioned or None, notes=_s(r.get("notes")))
    s.commit()
    print(f"registry: {len(reg)} ships, watch list: {len(watch)} ships")

    # 2. GFW history
    gfw = pd.read_csv(v1 / "data" / "positions.csv", dtype={"imo": str, "mmsi": str, "event_id": str})
    gfw["timestamp"] = pd.to_datetime(gfw["timestamp"], utc=True)
    pings, anomalies = [], []
    # GFW gap rows carry no event id: pair each "AIS off" with the next "AIS on" of the same MMSI
    # that has the same GFW note (the note holds the gap's duration/distance, so it identifies the pair)
    ons = gfw[gfw.event_type == "ais_on"].sort_values("timestamp")

    def matching_on(off):
        later = ons[(ons["mmsi"] == off["mmsi"]) & (ons["timestamp"] >= off["timestamp"])]
        same = later[later["note"] == off["note"]]
        rows = same if not same.empty else later
        return rows.iloc[0].to_dict() if not rows.empty else None

    for r in gfw.to_dict("records"):
        mmsi, imo = _s(r["mmsi"]), _s(r["imo"])
        if not mmsi or pd.isna(r["lat"]):
            continue
        base = {"mmsi": mmsi, "imo": imo, "name": _s(r["name"]), "ts": r["timestamp"].to_pydatetime(),
                "lat": r["lat"], "lon": r["lon"], "source": "gfw", "nav_status": r["event_type"].replace("_", " ")}
        pings.append(base)
        if r["event_type"] == "ais_off":
            on = matching_on(r)
            note = str(r.get("note") or "")
            m = re.search(r"AIS off ([\d.]+) h, ([\d.?]+) km", note)
            hours = float(m[1]) if m else None
            km = _f(m[2]) if m else None
            intentional = "intentional=True" in note
            sev = "high" if intentional or (hours or 0) >= 24 else "medium" if (hours or 0) >= 12 else "low"
            anomalies.append({
                "dedupe_key": f"gfw-gap-{mmsi}-{base['ts']:%Y%m%d%H%M%S}", "kind": "ais_gap", "severity": sev,
                "score": SEVERITY_SCORE[sev], "mmsi": mmsi, "start_ts": base["ts"],
                "end_ts": on["timestamp"].to_pydatetime() if on else None, "lat": r["lat"], "lon": r["lon"],
                "lat2": on["lat"] if on else None, "lon2": on["lon"] if on else None,
                "summary": f"AIS off {hours:.1f} h" if hours is not None else "AIS off (duration not reported)",
                "details": {"hours": hours, "km_moved": km, "intentional": intentional, "gfw_note": note,
                            "gfw_event_id": r["event_id"], "sources": ["gfw"]}, "source": "gfw"})
            if anomalies[-1]["summary"] and km is not None:
                anomalies[-1]["summary"] += f", {km:,.0f} km while dark"
            if intentional:
                anomalies[-1]["summary"] += " - GFW judges it INTENTIONAL"
        elif r["event_type"] == "encounter":
            anomalies.append({
                "dedupe_key": f"gfw-{_s(r['event_id']) or r['timestamp']}-{mmsi}"[:64], "kind": "encounter",
                "severity": "high", "score": SEVERITY_SCORE["high"], "mmsi": mmsi, "start_ts": base["ts"],
                "end_ts": None, "lat": r["lat"], "lon": r["lon"], "summary": str(r.get("note") or "Encounter at sea"),
                "details": {"sources": ["gfw"], "gfw_event_id": r["event_id"]}, "source": "gfw"})
    res = store_pings(s, pings, detect=False)
    vid = {}
    for v in s.scalars(select(Vessel).where(Vessel.mmsi.is_not(None))):
        vid[v.mmsi] = v.id
    for p in s.execute(select(Position.mmsi, Position.vessel_id).where(Position.vessel_id.is_not(None)).distinct()):
        vid.setdefault(p.mmsi, p.vessel_id)
    n_gfw = upsert_anomalies(s, anomalies, vid)
    s.commit()
    print(f"GFW: {res['positions_added']} positions, {n_gfw} GFW anomalies (AIS-off + encounters)")

    # 3. imagery and 3D-model records from the team database, each checked before it can count as evidence
    s.query(ReferenceRecord).delete()
    by_imo = {v.imo: v for v in s.scalars(select(Vessel).where(Vessel.imo.is_not(None)))}
    img = capstone_loader.load_imagery()
    for r in img.to_dict("records"):
        v = by_imo.get(_s(r.get("imo")))
        rec = {"name": _s(r["image_id"]) or "(unnamed)", "date": _s(r.get("date")), "link": _s(r.get("link")),
               "verification": _s(r.get("verification"))}
        issues = check_imagery(rec, v.name if v else None)
        s.add(ReferenceRecord(kind=str(r["kind"]).lower(), name=rec["name"], vessel_id=v.id if v else None,
                              date=rec["date"], platform=_s(r.get("platform")), resolution_m=_f(r.get("resolution_m")),
                              location=_s(r.get("location")), lat=_f(r.get("lat")), lon=_f(r.get("lon")),
                              provider=_s(r.get("provider")), link=rec["link"], verification=rec["verification"],
                              issues=issues, usable=not any(i["level"] == "blocking" for i in issues)))
    models = capstone_loader.load_models()
    for r in models.to_dict("records"):
        v = by_imo.get(_s(r.get("imo")))
        rec = {"name": _s(r["model_name"]), "link": _s(r.get("link")), "verification": _s(r.get("verification"))}
        issues = check_model(rec, v.name if v else "")
        s.add(ReferenceRecord(kind="model", name=rec["name"] or "(unnamed)", vessel_id=v.id if v else None,
                              provider=_s(r.get("source")), platform=_s(r.get("file_format")), link=rec["link"],
                              verification=rec["verification"], issues=issues,
                              usable=not any(i["level"] == "blocking" for i in issues)))
    # data-quality problems found by the v1 validation run
    s.query(DataCheck).delete()
    checks_csv = v1 / "data" / "output_data_checks.csv"
    if checks_csv.exists():
        for r in pd.read_csv(checks_csv, dtype=str).fillna("").to_dict("records"):
            s.add(DataCheck(severity=r["severity"], table=r["table"], record=r["record"], field=r["field"] or None,
                            problem=r["problem"], value=r["value"] or None))
    s.commit()
    print(f"references: {len(img)} imagery + {len(models)} 3D-model records checked")

    # 4. detectors valid for event data
    mmsis = set(s.scalars(select(Position.mmsi).distinct()))
    n = run_detection(s, mmsis, full=True)
    s.commit()
    print(f"detectors: {n} findings over {len(mmsis)} MMSIs")
    s.close()


if __name__ == "__main__":
    main()
