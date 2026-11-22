"""Data tab: where every number comes from, how fresh it is, and the known problems in the sources."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import iso
from app.db import get_session
from app.models import DataCheck, Position, ReferenceRecord, Vessel

router = APIRouter(prefix="/api/data", tags=["data"])

SOURCE_INFO = {
    "gfw": ("Global Fishing Watch", "AIS-off events, port visits, loitering and encounters from satellite + terrestrial AIS",
            "https://globalfishingwatch.org"),
    "aisstream": ("aisstream.io", "Live AIS stream", "https://aisstream.io"),
    "norway_ais": ("Norwegian Coastal Administration", "Open live AIS, Norway / Barents / Svalbard",
                   "https://www.kystverket.no/en/navigation-and-monitoring/ais/"),
}


def ref_out(r: ReferenceRecord, names=None):
    return {"id": r.id, "kind": r.kind, "name": r.name, "vessel_id": r.vessel_id,
            "vessel_name": (names or {}).get(r.vessel_id), "date": r.date, "platform": r.platform,
            "resolution_m": r.resolution_m, "location": r.location, "provider": r.provider, "link": r.link,
            "verification": r.verification, "issues": r.issues, "usable": r.usable}


@router.get("/references")
def references(kind: str = None, vessel_id: int = None, s: Session = Depends(get_session)):
    q = select(ReferenceRecord)
    if kind:
        q = q.where(ReferenceRecord.kind.in_(kind.split(",")))
    if vessel_id:
        q = q.where(ReferenceRecord.vessel_id == vessel_id)
    rows = s.scalars(q.order_by(ReferenceRecord.kind, ReferenceRecord.name)).all()
    names = {v.id: v.name for v in s.scalars(select(Vessel).where(Vessel.id.in_({r.vessel_id for r in rows if r.vessel_id})))}
    return [ref_out(r, names) for r in rows]


@router.get("/sources")
def sources(s: Session = Depends(get_session)):
    rows = s.execute(select(Position.source, func.count(Position.id), func.min(Position.ts), func.max(Position.ts))
                     .group_by(Position.source)).all()
    out = []
    for src, n, first, last in rows:
        name, what, url = SOURCE_INFO.get(src, (src, "Imported positions", None))
        out.append({"id": src, "name": name, "what": what, "url": url, "records": n, "first": iso(first), "last": iso(last)})
    out.append({"id": "capstone", "name": "Team Capstone database", "what": "Ship registry, watch list, imagery and 3D-model records",
                "url": None, "records": s.scalar(select(func.count(Vessel.id))), "first": None, "last": None})
    return out


@router.get("/checks")
def checks(s: Session = Depends(get_session)):
    order = {"error": 0, "warning": 1, "info": 2}
    rows = sorted(s.scalars(select(DataCheck)), key=lambda c: order.get(c.severity, 3))
    return [{"severity": c.severity, "table": c.table, "record": c.record, "field": c.field, "problem": c.problem,
             "value": c.value} for c in rows]


@router.get("/routes")
def routes(days: int = 365, s: Session = Depends(get_session)):
    """Every watch-list ship's previous routes (legs reconstructed along sea lanes between reported positions)."""
    from app.services.routes import fleet_routes
    return fleet_routes(s, days)


@router.get("/shipping-lanes")
def lanes(min_lat: float = 50.0):
    from app.services.routes import shipping_lanes
    return shipping_lanes(min_lat)
