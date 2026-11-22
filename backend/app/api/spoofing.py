"""Spoofing tab: AIS gaps, on/off cycling, teleports, identity games - and imagery to check them."""
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import anomaly_out, utc
from app.db import get_session
from app.models import Anomaly, Position, Vessel
from app.services.ingest import run_detection

router = APIRouter(prefix="/api/spoofing", tags=["spoofing"])

KIND_LABELS = {
    "ais_gap": "AIS switched off", "on_off_cycling": "Transmitter switched on/off repeatedly",
    "teleport": "Impossible position jump", "mmsi_collision": "Two ships, one MMSI",
    "speed_mismatch": "Reported speed doesn't fit movement", "identity_change": "Identity changed",
    "invalid_id": "ID problem / flag mismatch", "record_conflict": "Conflicting records (MMSI shared)", "imo_clone": "Same IMO in two places",
    "position_invalid": "Invalid position", "encounter": "Encounter at sea (possible STS)",
    "loitering": "Loitering at sea",
}


@router.get("/kinds")
def kinds():
    return [{"kind": k, "label": v} for k, v in KIND_LABELS.items()]


@router.get("/anomalies")
def list_anomalies(kind: str = None, severity: str = None, status: str = None, vessel_id: int = None,
                   since: datetime = None, limit: int = 200, offset: int = 0, s: Session = Depends(get_session)):
    q = select(Anomaly)
    if kind:
        q = q.where(Anomaly.kind.in_(kind.split(",")))
    if severity:
        q = q.where(Anomaly.severity.in_(severity.split(",")))
    if status:
        q = q.where(Anomaly.status.in_(status.split(",")))
    if vessel_id:
        q = q.where(Anomaly.vessel_id == vessel_id)
    if since:
        q = q.where(Anomaly.start_ts >= since)
    rows = s.scalars(q.order_by(Anomaly.start_ts.desc()).offset(offset).limit(limit)).all()
    names = {v.id: v.name for v in s.scalars(select(Vessel).where(Vessel.id.in_({a.vessel_id for a in rows if a.vessel_id})))}
    return [{**anomaly_out(a), "label": KIND_LABELS.get(a.kind, a.kind), "vessel_name": names.get(a.vessel_id)}
            for a in rows]


@router.get("/summary")
def summary(days: int = 30, s: Session = Depends(get_session)):
    """Counts by kind / severity and a per-day timeline for the tab's charts."""
    rows = s.scalars(select(Anomaly).where(Anomaly.status != "dismissed")).all()
    if not rows:
        return {"by_kind": {}, "by_severity": {}, "timeline": []}
    newest = max(utc(a.start_ts) for a in rows)
    recent = [a for a in rows if utc(a.start_ts) >= newest - timedelta(days=days)]
    per_day = Counter((utc(a.start_ts).date().isoformat(), a.kind) for a in recent)
    return {
        "by_kind": dict(Counter(a.kind for a in rows)),
        "by_severity": dict(Counter(a.severity for a in rows)),
        "timeline": [{"date": d, "kind": k, "count": n} for (d, k), n in sorted(per_day.items())],
    }


@router.get("/anomalies/{anomaly_id}")
def anomaly_detail(anomaly_id: int, s: Session = Depends(get_session)):
    a = s.get(Anomaly, anomaly_id)
    if a is None:
        raise HTTPException(404, "No such anomaly")
    # the pings around it, so the panel can draw the evidence
    t0 = utc(a.start_ts) - timedelta(hours=6)
    t1 = utc(a.end_ts or a.start_ts) + timedelta(hours=6)
    q = select(Position).where(Position.ts >= t0, Position.ts <= t1)
    q = q.where(Position.vessel_id == a.vessel_id) if a.vessel_id else q.where(Position.mmsi == a.mmsi)
    pings = s.scalars(q.order_by(Position.ts)).all()
    return {**anomaly_out(a), "label": KIND_LABELS.get(a.kind, a.kind),
            "evidence": [{"ts": utc(p.ts).isoformat(), "lat": p.lat, "lon": p.lon, "sog": p.sog, "name": p.name,
                          "mmsi": p.mmsi, "source": p.source} for p in pings]}


class StatusIn(BaseModel):
    status: str      # new / reviewed / confirmed / dismissed


@router.patch("/anomalies/{anomaly_id}")
def set_status(anomaly_id: int, body: StatusIn, s: Session = Depends(get_session)):
    if body.status not in ("new", "reviewed", "confirmed", "dismissed"):
        raise HTTPException(400, "status must be new, reviewed, confirmed or dismissed")
    a = s.get(Anomaly, anomaly_id)
    if a is None:
        raise HTTPException(404, "No such anomaly")
    a.status = body.status
    s.commit()
    return anomaly_out(a)


@router.get("/anomalies/{anomaly_id}/imagery")
def imagery_for(anomaly_id: int, s: Session = Depends(get_session)):
    """Cross-cue: Sentinel-1/2 scenes that could show what really happened (Copernicus search)."""
    from app.ingest.copernicus import search_for_anomaly
    a = s.get(Anomaly, anomaly_id)
    if a is None or a.lat is None:
        raise HTTPException(404, "No such anomaly, or it has no position")
    a.start_ts, a.end_ts = utc(a.start_ts), utc(a.end_ts)
    try:
        return search_for_anomaly(a)
    except Exception as e:
        raise HTTPException(502, f"Copernicus search failed: {e}")


@router.post("/rerun")
def rerun(full: bool = False, s: Session = Depends(get_session)):
    """Re-run every detector over every ship (after changing thresholds)."""
    mmsis = set(s.scalars(select(Position.mmsi).distinct()))
    n = run_detection(s, mmsis, full=full) if mmsis else 0
    s.commit()
    return {"vessels": len(mmsis), "anomalies_upserted": n}
