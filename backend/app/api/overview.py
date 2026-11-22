"""Overview tab: the landing page. Headline numbers + one world map with every layer."""
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.api.common import feature, iso, now_utc, utc
from app.api.live import latest_positions
from app.api.vessels import vessel_risks
from app.db import get_session
from app.models import Anomaly, Comparison, Position, SarDetection, SarScene, Vessel

router = APIRouter(prefix="/api/overview", tags=["overview"])


def _as_of(s: Session):
    """'Now' = the newest ping in the database (so demo / replayed data still looks live)."""
    newest = s.scalar(select(func.max(Position.ts)))
    return utc(newest) if newest else now_utc()


@router.get("")
def overview(s: Session = Depends(get_session)):
    now = _as_of(s)
    day, week = now - timedelta(hours=24), now - timedelta(days=7)
    live_since = now - timedelta(hours=config.LIVE_WINDOW_HOURS)
    open_q = select(Anomaly).where(Anomaly.status.in_(["new", "reviewed", "confirmed"]), Anomaly.severity != "info")
    open_anoms = s.scalars(open_q).all()
    by_kind, by_sev = {}, {}
    for a in open_anoms:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
    verdicts = dict(s.execute(select(Comparison.verdict, func.count()).group_by(Comparison.verdict)).all())
    risks = vessel_risks(s)
    vessels = {v.id: v for v in s.scalars(select(Vessel).where(Vessel.id.in_(list(risks))))}
    top = sorted(risks.items(), key=lambda kv: -kv[1])[:10]
    return {
        "as_of": now.isoformat(),
        "counts": {
            "vessels": s.scalar(select(func.count(Vessel.id))),
            "watchlist": s.scalar(select(func.count(Vessel.id)).where(Vessel.watchlist)),
            "live_vessels": s.scalar(select(func.count(func.distinct(Position.mmsi))).where(Position.ts >= live_since)),
            "reported_7d": s.scalar(select(func.count(func.distinct(Position.vessel_id)))
                                    .where(Position.ts >= now - timedelta(days=7), Position.vessel_id.in_(
                                        select(Vessel.id).where(Vessel.watchlist)))),
            "pings_24h": s.scalar(select(func.count(Position.id)).where(Position.ts >= day)),
            "open_anomalies": len(open_anoms),
            "high_severity_24h": sum(1 for a in open_anoms if a.severity == "high" and utc(a.start_ts) >= day),
            "sar_scenes_7d": s.scalar(select(func.count(SarScene.id)).where(SarScene.acquired_at >= week)),
            "sar_detections": s.scalar(select(func.count(SarDetection.id))),
            "dark_targets": verdicts.get("dark", 0),
            "identity_mismatches": verdicts.get("identity_mismatch", 0),
        },
        "anomalies_by_kind": by_kind,
        "anomalies_by_severity": by_sev,
        "comparison_verdicts": verdicts,
        "top_risk": [{"vessel_id": vid, "name": vessels[vid].name, "imo": vessels[vid].imo, "risk_score": r,
                      "flag": vessels[vid].flag, "watchlist": vessels[vid].watchlist} for vid, r in top if vid in vessels],
    }


@router.get("/fleet")
def fleet(s: Session = Depends(get_session)):
    """Every watch-list ship at its LAST KNOWN position, however old, with its age - the map's main layer
    (event sources like GFW report days apart, so a 'last 6 h' view alone would be nearly empty)."""
    now = _as_of(s)
    risks = vessel_risks(s)
    feats = []
    for v in s.scalars(select(Vessel).where(Vessel.watchlist)):
        p = s.scalar(select(Position).where(Position.vessel_id == v.id).order_by(Position.ts.desc()))
        if p is None:
            continue
        r = risks.get(v.id, 0.0)
        feats.append(feature(p.lon, p.lat, {
            "layer": "vessels", "vessel_id": v.id, "name": v.name, "broadcast_name": p.name, "imo": v.imo,
            "mmsi": p.mmsi, "flag": v.flag, "ship_type": v.ship_type, "ts": iso(p.ts),
            "age_hours": round((now - utc(p.ts)).total_seconds() / 3600, 1), "status": p.nav_status,
            "cog": p.cog, "sog": p.sog, "year_built": v.year_built,
            "source": p.source, "risk_score": r, "risk_band": "high" if r >= 12 else "watch" if r >= 5 else "low"}))
    return {"type": "FeatureCollection", "as_of": now.isoformat(), "features": feats}


@router.get("/map")
def overview_map(layers: str = "vessels,anomalies,sar", hours: float = 72, min_lat: float = None,
                 s: Session = Depends(get_session)):
    """One GeoJSON FeatureCollection for the world map; every feature has properties.layer."""
    now = _as_of(s)
    since = now - timedelta(hours=hours)
    want = set(layers.split(","))
    feats = []
    if "vessels" in want:
        risks = vessel_risks(s)
        for p in latest_positions(s, since):
            if min_lat is not None and p.lat < min_lat:
                continue
            r = risks.get(p.vessel_id, 0.0)
            feats.append(feature(p.lon, p.lat, {
                "layer": "vessels", "vessel_id": p.vessel_id, "mmsi": p.mmsi, "name": p.name, "ts": iso(p.ts),
                "sog": p.sog, "cog": p.cog, "heading": p.heading, "source": p.source, "risk_score": r,
                "risk_band": "high" if r >= 12 else "watch" if r >= 5 else "low"}))
    if "anomalies" in want:
        for a in s.scalars(select(Anomaly).where(Anomaly.start_ts >= since, Anomaly.lat.is_not(None),
                                                 Anomaly.status != "dismissed")):
            props = {"layer": "anomalies", "id": a.id, "kind": a.kind, "severity": a.severity, "summary": a.summary,
                     "vessel_id": a.vessel_id, "start_ts": iso(a.start_ts)}
            feats.append(feature(a.lon, a.lat, props))
            if a.lat2 is not None:              # line from where it went dark to where it reappeared
                feats.append(feature(None, None, {**props, "layer": "anomaly_links"}, "LineString",
                                     [[a.lon, a.lat], [a.lon2, a.lat2]]))
    if "sar" in want:
        latest = {}
        for c in s.scalars(select(Comparison).order_by(Comparison.id)):
            latest[c.sar_detection_id] = c
        for d in s.scalars(select(SarDetection).join(SarScene).where(SarScene.acquired_at >= since - timedelta(days=30))):
            c = latest.get(d.id)
            feats.append(feature(d.lon, d.lat, {
                "layer": "sar", "detection_id": d.id, "length_m": d.length_m, "acquired_at": iso(d.scene.acquired_at),
                "verdict": c.verdict if c else "pending", "confidence": c.confidence if c else None,
                "headline": c.card.get("headline") if c else None}))
    return {"type": "FeatureCollection", "as_of": now.isoformat(), "features": feats}
