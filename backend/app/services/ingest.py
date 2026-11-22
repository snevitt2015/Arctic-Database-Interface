"""
services/ingest.py  -  Store pings from any feed, keep identities up to date, run spoofing detection.

Every feed adapter turns its messages into the same ping dict and calls store_pings():

    {"mmsi": "273...", "ts": datetime (UTC), "lat": .., "lon": .., "sog": .., "cog": .., "heading": ..,
     "nav_status": .., "name": .., "imo": .., "callsign": .., "flag": .., "ship_type": ..,
     "length_m": .., "beam_m": .., "draught_m": .., "source": "aisstream"}

Only mmsi, ts and source are required. A static-data message with no position updates the
vessel/identity tables but adds no Position row.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.analytics.spoofing import detect_for_vessel, detect_imo_clones
from app.models import Anomaly, Position, Vessel, VesselIdentity


def _utc(ts) -> datetime:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _clean(v):
    if v is None:
        return None
    s = str(v).strip().removesuffix(".0")
    return None if s in ("", "0", "nan", "None") else s


def find_or_create_vessel(s: Session, p: dict) -> Vessel | None:
    imo, mmsi = _clean(p.get("imo")), _clean(p.get("mmsi"))
    v = s.scalar(select(Vessel).where(Vessel.imo == imo)) if imo else None
    if v is None and mmsi:
        v = s.scalar(select(Vessel).where(Vessel.mmsi == mmsi).order_by(Vessel.id.desc()))
        if v is None:                     # an MMSI this ship used before (or shares with a clone)
            ident = s.scalar(select(VesselIdentity).where(VesselIdentity.mmsi == mmsi)
                             .order_by(VesselIdentity.last_seen.desc()))
            v = ident.vessel if ident else None
        if v is not None and imo and v.imo and v.imo != imo:
            v = None                      # this MMSI now claims a different IMO: a different ship record
    if v is None:
        if not (imo or mmsi):
            return None
        v = Vessel(imo=imo, mmsi=mmsi, name=p.get("name"))
        s.add(v)
        s.flush()
    # fill gaps from the message, never overwrite registry data that's already there
    if imo and not v.imo:
        v.imo = imo
    if mmsi and (not v.mmsi or not imo or v.imo == imo):
        v.mmsi = mmsi                     # current MMSI (an IMO clone's MMSI is kept in identities only)
    for attr in ("name", "callsign", "flag", "ship_type", "length_m", "beam_m"):
        if p.get(attr) and not getattr(v, attr):
            setattr(v, attr, p[attr])
    return v


def _touch_identity(s: Session, v: Vessel, p: dict, ts: datetime):
    if not (p.get("name") or p.get("imo")):
        return
    key = dict(vessel_id=v.id, mmsi=_clean(p.get("mmsi")) or "", name=(p.get("name") or "").strip().upper(),
               imo=_clean(p.get("imo")) or "")
    ident = s.scalar(select(VesselIdentity).filter_by(**key))
    if ident is None:
        s.add(VesselIdentity(**key, callsign=p.get("callsign"), flag=p.get("flag"), first_seen=ts, last_seen=ts,
                             source=p.get("source", "")))
    else:
        ident.first_seen = min(_utc(ident.first_seen), ts)
        ident.last_seen = max(_utc(ident.last_seen), ts)


def store_pings(s: Session, pings: list[dict], detect: bool = True) -> dict:
    """Save pings, then re-run detection for the ships they belong to. Returns counts."""
    added, touched = 0, set()
    seen = set()
    for p in pings:
        mmsi = _clean(p.get("mmsi"))
        if not mmsi:
            continue
        ts = _utc(p["ts"])
        v = find_or_create_vessel(s, p)
        if v is not None:
            _touch_identity(s, v, p, ts)
        if p.get("lat") is None or p.get("lon") is None:
            continue
        key = (mmsi, ts, p["source"])
        if key in seen or s.scalar(select(Position.id).where(Position.mmsi == mmsi, Position.ts == ts,
                                                             Position.source == p["source"])):
            continue
        seen.add(key)
        # position messages carry no name/IMO: stamp the ship's last known identity on the ping,
        # so identity changes and IMO clones can be seen in the position history
        s.add(Position(vessel_id=v.id if v else None, mmsi=mmsi,
                       imo=_clean(p.get("imo")) or (v.imo if v else None),
                       name=p.get("name") or (v.name if v else None),
                       ts=ts, lat=float(p["lat"]), lon=float(p["lon"]), sog=p.get("sog"), cog=p.get("cog"),
                       heading=p.get("heading"), nav_status=p.get("nav_status"), draught_m=p.get("draught_m"),
                       source=p["source"]))
        added += 1
        touched.add(mmsi)
    s.flush()
    found = run_detection(s, touched) if detect and touched else 0
    s.commit()
    return {"positions_added": added, "vessels_touched": len(touched), "anomalies_upserted": found}


def _pings_for(s: Session, mmsis, since: datetime | None):
    q = select(Position).where(Position.mmsi.in_(list(mmsis)))
    if since is not None:
        q = q.where(Position.ts >= since)
    rows = s.scalars(q.order_by(Position.ts)).all()
    out = {}
    for r in rows:
        out.setdefault(r.mmsi, []).append({
            "mmsi": r.mmsi, "ts": _utc(r.ts), "lat": r.lat, "lon": r.lon, "sog": r.sog, "cog": r.cog,
            "name": r.name, "imo": r.imo, "source": r.source, "vessel_id": r.vessel_id})
    return out


def _no_nan(x):
    """Real feeds have empty values (NaN); the database and JSON need None."""
    if isinstance(x, float) and x != x:
        return None
    if isinstance(x, dict):
        return {k: _no_nan(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_no_nan(v) for v in x]
    return x


def upsert_anomalies(s: Session, found: list[dict], vessel_by_mmsi: dict = None, source="detector") -> int:
    n = 0
    for a in found:
        a = _no_nan(a)
        a = {**a, "start_ts": _utc(a["start_ts"]), "end_ts": _utc(a["end_ts"]) if a.get("end_ts") else None}
        row =s.scalar(select(Anomaly).where(Anomaly.dedupe_key == a["dedupe_key"]))
        vid = (vessel_by_mmsi or {}).get(a.get("mmsi"))
        if row is None:
            s.add(Anomaly(**{k: a.get(k) for k in ("dedupe_key", "mmsi", "kind", "severity", "score", "start_ts",
                                                   "end_ts", "lat", "lon", "lat2", "lon2", "summary", "details")},
                          vessel_id=vid, source=a.get("source", source)))
        else:                               # keep analyst status; refresh the evidence
            for k in ("severity", "score", "end_ts", "lat2", "lon2", "summary", "details"):
                setattr(row, k, a.get(k))
            row.vessel_id = row.vessel_id or vid
        n += 1
    s.flush()
    return n


def run_detection(s: Session, mmsis, lookback_hours: float = None, full: bool = False) -> int:
    """Re-run every detector for these MMSIs over the recent window (or all history with full=True)."""
    lookback_hours = lookback_hours or config.DETECT_LOOKBACK_HOURS
    if full:
        since = None
    else:
        newest = s.scalar(select(func.max(Position.ts)).where(Position.mmsi.in_(list(mmsis))))
        since = _utc(newest) - timedelta(hours=lookback_hours) if newest else None
    pings = _pings_for(s, mmsis, since)
    vessels = {v.mmsi: v for v in s.scalars(select(Vessel).where(Vessel.mmsi.in_(list(mmsis))))}
    found = []
    for mmsi, ps in pings.items():
        v = vessels.get(mmsi)
        found += detect_for_vessel(ps, mmsi, reported_flag=v.flag if v else None)
    found += detect_imo_clones([p for ps in pings.values() for p in ps])
    vid = {m: v.id for m, v in vessels.items()}
    for ps in pings.values():                      # pings know their vessel even if the MMSI moved on
        for p in ps:
            if p.get("vessel_id"):
                vid.setdefault(p["mmsi"], p["vessel_id"])
    return upsert_anomalies(s, found, vid)
