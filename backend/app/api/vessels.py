"""Vessels: registry, watch list, one ship's full profile and track."""
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.analytics.spoofing import risk_score
from app.api.common import anomaly_out, feature, now_utc, osint_links, position_out, utc, vessel_out
from app.db import get_session
from app.ingest.csv_import import load_registry
from app.models import Anomaly, Comparison, Position, ReferenceRecord, Vessel

router = APIRouter(prefix="/api/vessels", tags=["vessels"])


class VesselIn(BaseModel):
    imo: str | None = None
    mmsi: str | None = None
    name: str | None = None
    callsign: str | None = None
    flag: str | None = None
    ship_type: str | None = None
    classification: str | None = None
    ice_class: str | None = None
    length_m: float | None = None
    beam_m: float | None = None
    air_draft_m: float | None = None
    superstructure_height_m: float | None = None
    gross_tonnage: float | None = None
    watchlist: bool | None = None
    sanctioned: bool | None = None
    notes: str | None = None


def upsert_vessel(s: Session, data: dict) -> Vessel:
    data = {k: v for k, v in data.items() if v is not None}
    v = None
    if data.get("imo"):
        v = s.scalar(select(Vessel).where(Vessel.imo == data["imo"]))
    if v is None and data.get("mmsi"):
        v = s.scalar(select(Vessel).where(Vessel.mmsi == data["mmsi"], Vessel.imo.is_(None)))
    if v is None:
        v = Vessel()
        s.add(v)
    for k, val in data.items():
        if hasattr(Vessel, k):
            setattr(v, k, val)
    s.flush()
    return v


def vessel_risks(s: Session, ids=None) -> dict:
    q = select(Anomaly).where(Anomaly.vessel_id.is_not(None))
    if ids is not None:
        q = q.where(Anomaly.vessel_id.in_(list(ids)))
    by = {}
    for a in s.scalars(q):
        by.setdefault(a.vessel_id, []).append({"score": a.score, "start_ts": utc(a.start_ts), "status": a.status})
    now = now_utc()
    return {vid: risk_score(rows, now) for vid, rows in by.items()}


@router.get("")
def list_vessels(q: str = None, watchlist: bool = None, limit: int = 200, s: Session = Depends(get_session)):
    query = select(Vessel)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Vessel.name.ilike(like), Vessel.imo.ilike(like), Vessel.mmsi.ilike(like)))
    if watchlist is not None:
        query = query.where(Vessel.watchlist == watchlist)
    vs = s.scalars(query.limit(limit)).all()
    risks = vessel_risks(s, [v.id for v in vs])
    out = [vessel_out(v, risks.get(v.id, 0.0)) for v in vs]
    return sorted(out, key=lambda r: -(r["risk_score"] or 0))


@router.post("")
def create_or_update(v: VesselIn, s: Session = Depends(get_session)):
    row = upsert_vessel(s, v.model_dump())
    s.commit()
    return vessel_out(row)


@router.post("/import")
async def import_registry(file: UploadFile = File(...), s: Session = Depends(get_session)):
    """Upload the Capstone registry export (CSV/XLSX) to load dimensions, flags, watch list."""
    import io
    import pandas as pd
    raw = await file.read()
    df = pd.read_excel(io.BytesIO(raw)) if file.filename.lower().endswith((".xlsx", ".xls")) else pd.read_csv(io.BytesIO(raw))
    rows = load_registry(df)
    for r in rows:
        upsert_vessel(s, r)
    s.commit()
    return {"vessels_upserted": len(rows)}


@router.get("/{vessel_id}")
def vessel_profile(vessel_id: int, s: Session = Depends(get_session)):
    v = s.get(Vessel, vessel_id)
    if v is None:
        raise HTTPException(404, "No such vessel")
    last = s.scalar(select(Position).where(Position.vessel_id == v.id).order_by(Position.ts.desc()))
    anomalies = s.scalars(select(Anomaly).where(Anomaly.vessel_id == v.id).order_by(Anomaly.start_ts.desc())).all()
    by_kind = {}
    for a in anomalies:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    comps = s.scalars(select(Comparison).where(Comparison.vessel_id == v.id).order_by(Comparison.id.desc()).limit(10)).all()
    return {
        **vessel_out(v, vessel_risks(s, [v.id]).get(v.id, 0.0)),
        "identities": [{"mmsi": i.mmsi, "imo": i.imo, "name": i.name, "callsign": i.callsign, "flag": i.flag,
                        "first_seen": utc(i.first_seen).isoformat(), "last_seen": utc(i.last_seen).isoformat(),
                        "source": i.source} for i in sorted(v.identities, key=lambda i: i.first_seen)],
        "last_position": position_out(last) if last else None,
        "anomaly_counts": by_kind,
        "recent_anomalies": [anomaly_out(a) for a in anomalies[:20]],
        "sar_sightings": [{"comparison_id": c.id, "verdict": c.verdict, "confidence": c.confidence,
                           "acquired_at": c.card.get("sar", {}).get("acquired_at"),
                           "position": c.card.get("position")} for c in comps],
        "osint_links": osint_links(v),
        "references": [{"kind": r.kind, "name": r.name, "provider": r.provider, "link": r.link, "date": r.date,
                        "usable": r.usable, "issues": r.issues}
                       for r in s.scalars(select(ReferenceRecord).where(ReferenceRecord.vessel_id == v.id))],
    }


@router.get("/{vessel_id}/track")
def vessel_track(vessel_id: int, days: float = 30, s: Session = Depends(get_session)):
    """GeoJSON: the track line + each ping (for the map)."""
    newest = s.scalar(select(func.max(Position.ts)).where(Position.vessel_id == vessel_id))
    if newest is None:
        return {"type": "FeatureCollection", "features": []}
    since = utc(newest) - timedelta(days=days)
    ps = s.scalars(select(Position).where(Position.vessel_id == vessel_id, Position.ts >= since)
                   .order_by(Position.ts)).all()
    feats = [feature(None, None, {"kind": "track", "vessel_id": vessel_id}, "LineString",
                     [[p.lon, p.lat] for p in ps])] if len(ps) > 1 else []
    feats += [feature(p.lon, p.lat, {"kind": "ping", **position_out(p)}) for p in ps]
    return {"type": "FeatureCollection", "features": feats}


@router.get("/{vessel_id}/route")
def vessel_route_api(vessel_id: int, days: int = 365, s: Session = Depends(get_session)):
    """Previous route: legs between reported positions, reconstructed along sea lanes (labelled as an estimate)."""
    from app.services.routes import vessel_route
    return {"type": "FeatureCollection", "features": vessel_route(s, vessel_id, days)}
