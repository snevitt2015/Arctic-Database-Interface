"""
services/sar.py  -  The automated SAR -> comparative pipeline.

    process_sar_chip()   radar image chip -> CFAR ship detections -> stored -> compared (one call)
    add_detections()     detections from elsewhere (GFW SAR presence, an analyst) -> stored -> compared
    compare_detection()  build the comparative card for one detection (re-run any time, e.g. after an
                         optical observation is added) and store it as a Comparison
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.comparative import compare
from app.analytics.geo import distance_km
from app.analytics.interpolate import position_at
from app.analytics.sar_detect import detect_ships
from app.models import Comparison, FeatureCheck, ModelComparison, OpticalObservation, Position, SarDetection, SarScene, Vessel

TRACK_WINDOW_H = 48          # AIS used to estimate where each ship was at the image time
CANDIDATE_RADIUS_KM = 75     # ships whose estimate is this close (plus their uncertainty) are candidates
LOOKALIKE_TRACK_DAYS = 7


def _utc(ts):
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def vessel_dict(v: Vessel) -> dict:
    return {c: getattr(v, c) for c in ("id", "imo", "mmsi", "name", "flag", "ship_type", "classification",
                                        "ice_class", "length_m", "beam_m", "air_draft_m",
                                        "superstructure_height_m", "watchlist", "sanctioned")}


def _tracks(s: Session, when: datetime, hours: float, vessel_ids=None) -> dict:
    """{vessel_id or 'mmsi:xxx': DataFrame track} around `when`."""
    q = select(Position).where(Position.ts >= when - timedelta(hours=hours), Position.ts <= when + timedelta(hours=hours))
    if vessel_ids is not None:
        q = q.where(Position.vessel_id.in_(list(vessel_ids)))
    rows = s.scalars(q).all()
    if not rows:
        return {}
    df = pd.DataFrame([{"key": r.vessel_id or f"mmsi:{r.mmsi}", "timestamp": pd.Timestamp(_utc(r.ts)),
                        "lat": r.lat, "lon": r.lon, "speed_kn": r.sog, "course": r.cog, "mmsi": r.mmsi} for r in rows])
    return {k: g for k, g in df.groupby("key")}


def _estimate(track, when) -> dict:
    e = position_at(track, pd.Timestamp(when))
    if e["lat"] is None:
        return {}
    last = track[track["timestamp"] <= pd.Timestamp(when)].tail(1)
    last = last.iloc[0] if not last.empty else track.iloc[0]
    return {"lat": float(e["lat"]), "lon": float(e["lon"]), "uncertainty_km": float(e["uncertainty_km"]),
            "method": e["method"], "mmsi": str(last["mmsi"]), "sog": None if pd.isna(last["speed_kn"]) else float(last["speed_kn"]),
            "cog": None if pd.isna(last["course"]) else float(last["course"])}


def _det_dict(d: SarDetection) -> dict:
    return {"id": d.id, "lat": d.lat, "lon": d.lon, "acquired_at": _utc(d.scene.acquired_at),
            "satellite": d.scene.satellite, "length_m": d.length_m, "width_m": d.width_m,
            "length_err_m": d.length_err_m, "width_err_m": d.width_err_m, "heading_deg": d.heading_deg,
            "pos_err_m": d.pos_err_m, "contrast_db": d.contrast_db, "ship_shaped": d.ship_shaped,
            "possible_sts": d.possible_sts}


def _opt_dict(o: OpticalObservation) -> dict:
    return {c: getattr(o, c) for c in ("image_id", "satellite", "lat", "lon", "gsd_m", "shadow_length_m",
                                        "shadow_err_m", "shadow_azimuth_deg", "cloud_pct",
                                        "image_url")} | {"acquired_at": _utc(o.acquired_at)}


def compare_detection(s: Session, detection_id: int, optical_id: int = None) -> Comparison:
    d = s.get(SarDetection, detection_id)
    if d is None:
        raise ValueError(f"No SAR detection {detection_id}")
    det = _det_dict(d)
    when = det["acquired_at"]

    opt_row = s.get(OpticalObservation, optical_id) if optical_id else s.scalar(
        select(OpticalObservation).where(OpticalObservation.sar_detection_id == d.id)
        .order_by(OpticalObservation.id.desc()))
    opt = _opt_dict(opt_row) if opt_row else None

    vessels = {v.id: v for v in s.scalars(select(Vessel))}
    candidates = []
    for key, track in _tracks(s, when, TRACK_WINDOW_H).items():
        est = _estimate(track, when)
        if not est:
            continue
        if distance_km(det["lat"], det["lon"], est["lat"], est["lon"]) > est["uncertainty_km"] + CANDIDATE_RADIUS_KM:
            continue
        v = vessels.get(key) if isinstance(key, int) else None
        vd = vessel_dict(v) if v else {"id": None, "mmsi": key.split(":")[1], "name": f"MMSI {key.split(':')[1]}"}
        candidates.append({"vessel": vd, "est": est})

    # look-alikes: the watch list + anything with registry dimensions
    reg_vessels = [v for v in vessels.values() if v.watchlist or v.length_m]
    reg_tracks = _tracks(s, when, LOOKALIKE_TRACK_DAYS * 24, [v.id for v in reg_vessels])
    registry = [{"vessel": vessel_dict(v), "est": _estimate(reg_tracks[v.id], when) if v.id in reg_tracks else None}
                for v in reg_vessels]

    models = [{"vessel_id": m.vessel_id, "method": m.method, "colour": m.colour, "score": m.score,
               "reviewer": m.reviewer, "notes": m.notes}
              for m in s.scalars(select(ModelComparison).where(ModelComparison.sar_detection_id == d.id))]
    # a ship someone compared against is always a look-alike candidate, even off the watch list
    have = {r["vessel"]["id"] for r in registry}
    for vid in {m["vessel_id"] for m in models} - have:
        v = vessels.get(vid)
        if v:
            registry.append({"vessel": vessel_dict(v), "est": None})
    checks = [{"vessel_id": c.vessel_id, "feature": c.feature, "expected": c.expected, "observed": c.observed,
               "match": c.match, "source": c.source}
              for c in s.scalars(select(FeatureCheck).where(FeatureCheck.sar_detection_id == d.id))]
    card = compare(det, candidates, opt, registry, models, checks)
    card["sar"]["detection_id"] = d.id
    card["sar"]["scene_id"] = d.scene.scene_id
    row = Comparison(sar_detection_id=d.id, optical_id=opt_row.id if opt_row else None,
                     vessel_id=(card["identified_vessel"] or {}).get("id"), verdict=card["verdict"],
                     confidence=card["confidence"], card=_jsonable(card))
    s.add(row)
    s.commit()
    return row


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (datetime, pd.Timestamp)):
        return x.isoformat()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def get_or_create_scene(s: Session, scene_id: str, satellite: str, acquired_at, pixel_m=None, mode=None,
                        footprint=None, source="") -> SarScene:
    sc = s.scalar(select(SarScene).where(SarScene.scene_id == scene_id))
    if sc is None:
        sc = SarScene(scene_id=scene_id, satellite=satellite, acquired_at=_utc(acquired_at), pixel_m=pixel_m,
                      mode=mode, footprint=footprint, source=source)
        s.add(sc)
        s.flush()
    return sc


def add_detections(s: Session, scene: SarScene, dets: list[dict], source="cfar", run_compare=True) -> list[int]:
    ids = []
    for det in dets:
        row = SarDetection(scene_id=scene.id, lat=det["lat"], lon=det["lon"], length_m=det.get("length_m"),
                           width_m=det.get("width_m"), length_err_m=det.get("length_err_m"),
                           width_err_m=det.get("width_err_m"), heading_deg=det.get("heading_deg"),
                           pos_err_m=det.get("pos_err_m"), contrast_db=det.get("contrast_db"),
                           ship_shaped=bool(det.get("ship_shaped", True)),
                           possible_sts=bool(det.get("possible_sts", False)), source=source)
        s.add(row)
        s.flush()
        ids.append(row.id)
    s.commit()
    if run_compare:
        for i in ids:
            compare_detection(s, i)
    return ids


def process_sar_chip(s: Session, sigma0, pixel_m: float, bounds, scene_id: str, satellite: str, acquired_at,
                     mask=None, mode=None) -> dict:
    """The automated path: radar chip in, comparisons out."""
    scene = get_or_create_scene(s, scene_id, satellite, acquired_at, pixel_m=pixel_m, mode=mode,
                                footprint=_bbox_polygon(bounds), source="chip")
    dets = detect_ships(sigma0, pixel_m, bounds=bounds, mask=mask)
    ids = add_detections(s, scene, dets, source="cfar")
    return {"scene": scene.scene_id, "detections": len(ids), "detection_ids": ids}


def _bbox_polygon(b):
    w, so, e, n = b
    return {"type": "Polygon", "coordinates": [[[w, so], [e, so], [e, n], [w, n], [w, so]]]}
