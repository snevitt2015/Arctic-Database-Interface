"""
SAR ⇄ optical comparative tab.

  POST /api/sar/process-chip      upload a radar chip (.npy or .tif) -> detections -> comparisons (automated)
  POST /api/sar/detections        add detections found elsewhere (GFW SAR presence, an analyst)
  POST /api/sar/optical           add an optical observation (with shadow measurement) -> comparison re-runs
  GET  /api/sar/detections        list, filter by verdict
  GET  /api/sar/detections/{id}   the latest comparison card for one detection
  POST /api/sar/detections/{id}/compare   re-run the comparison (e.g. after new AIS arrived)
"""
import io
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.sun import sun_position
from app.api.common import iso, utc
from app.db import get_session
from app.models import Comparison, FeatureCheck, ModelComparison, OpticalObservation, SarDetection, SarScene, Vessel
from app.services.sar import add_detections, compare_detection, get_or_create_scene, process_sar_chip

router = APIRouter(prefix="/api/sar", tags=["sar"])


def _latest_comparison(s, det_id):
    return s.scalar(select(Comparison).where(Comparison.sar_detection_id == det_id).order_by(Comparison.id.desc()))


@router.get("/scenes")
def scenes(limit: int = 100, s: Session = Depends(get_session)):
    rows = s.scalars(select(SarScene).order_by(SarScene.acquired_at.desc()).limit(limit)).all()
    return [{"id": r.id, "scene_id": r.scene_id, "satellite": r.satellite, "mode": r.mode,
             "acquired_at": iso(r.acquired_at), "pixel_m": r.pixel_m, "footprint": r.footprint,
             "detections": len(r.detections), "source": r.source} for r in rows]


@router.post("/process-chip")
async def process_chip(file: UploadFile = File(...), pixel_m: float = Form(...), west: float = Form(...),
                       south: float = Form(...), east: float = Form(...), north: float = Form(...),
                       acquired_at: datetime = Form(...), scene_id: str = Form(...),
                       satellite: str = Form("Sentinel-1"), mode: str = Form(None), s: Session = Depends(get_session)):
    """Radar backscatter chip (linear sigma0, north-up) -> CFAR detections -> comparative cards."""
    raw = await file.read()
    name = file.filename.lower()
    if name.endswith(".npy"):
        arr = np.load(io.BytesIO(raw), allow_pickle=False)
    elif name.endswith((".tif", ".tiff")):
        import tifffile
        arr = tifffile.imread(io.BytesIO(raw))
    else:
        raise HTTPException(400, "Upload a .npy or .tif chip")
    if arr.ndim != 2:
        raise HTTPException(400, f"Chip must be a single band (got shape {arr.shape})")
    out = process_sar_chip(s, arr.astype("float64"), pixel_m, (west, south, east, north), scene_id, satellite,
                           acquired_at, mode=mode)
    out["comparisons"] = [_summary(s, s.get(SarDetection, i)) for i in out["detection_ids"]]
    return out


class DetectionIn(BaseModel):
    lat: float
    lon: float
    length_m: float | None = None
    width_m: float | None = None
    length_err_m: float | None = None
    width_err_m: float | None = None
    heading_deg: float | None = None
    pos_err_m: float | None = None


class DetectionsIn(BaseModel):
    scene_id: str
    satellite: str = "Sentinel-1"
    acquired_at: datetime
    pixel_m: float | None = 10
    source: str = "manual"
    detections: list[DetectionIn]


@router.post("/detections")
def post_detections(body: DetectionsIn, s: Session = Depends(get_session)):
    scene = get_or_create_scene(s, body.scene_id, body.satellite, body.acquired_at, pixel_m=body.pixel_m,
                                source=body.source)
    px = body.pixel_m or 10
    dets = [{**d.model_dump(), "length_err_m": d.length_err_m or 1.5 * px, "width_err_m": d.width_err_m or 1.5 * px,
             "pos_err_m": d.pos_err_m or px} for d in body.detections]
    ids = add_detections(s, scene, dets, source=body.source)
    return [_summary(s, s.get(SarDetection, i)) for i in ids]


def _summary(s, d: SarDetection):
    c = _latest_comparison(s, d.id)
    return {"detection_id": d.id, "scene_id": d.scene.scene_id, "acquired_at": iso(d.scene.acquired_at),
            "lat": d.lat, "lon": d.lon, "length_m": d.length_m, "width_m": d.width_m,
            "verdict": c.verdict if c else "pending", "confidence": c.confidence if c else None,
            "headline": c.card.get("headline") if c else None, "vessel_id": c.vessel_id if c else None,
            "flags": c.card.get("flags", []) if c else []}


@router.get("/detections")
def list_detections(verdict: str = None, limit: int = 200, s: Session = Depends(get_session)):
    rows = s.scalars(select(SarDetection).join(SarScene).order_by(SarScene.acquired_at.desc()).limit(limit * 3)).all()
    out = [_summary(s, d) for d in rows]
    if verdict:
        out = [r for r in out if r["verdict"] in verdict.split(",")]
    return out[:limit]


@router.get("/detections/{det_id}")
def detection_card(det_id: int, s: Session = Depends(get_session)):
    d = s.get(SarDetection, det_id)
    if d is None:
        raise HTTPException(404, "No such detection")
    c = _latest_comparison(s, det_id) or compare_detection(s, det_id)
    return {"comparison_id": c.id, "created_at": iso(c.created_at), **c.card}


@router.post("/detections/{det_id}/compare")
def recompare(det_id: int, optical_id: int = None, s: Session = Depends(get_session)):
    if s.get(SarDetection, det_id) is None:
        raise HTTPException(404, "No such detection")
    c = compare_detection(s, det_id, optical_id)
    return {"comparison_id": c.id, **c.card}


class OpticalIn(BaseModel):
    """An optical image of the target, for the shadow comparative (no dimension measuring)."""
    image_id: str
    acquired_at: datetime
    lat: float
    lon: float
    satellite: str | None = None
    gsd_m: float | None = None
    shadow_length_m: float | None = None
    shadow_err_m: float | None = None
    shadow_azimuth_deg: float | None = None
    cloud_pct: float | None = None
    image_url: str | None = None
    sar_detection_id: int | None = None
    source: str = "manual"


@router.post("/optical")
def add_optical(body: OpticalIn, s: Session = Depends(get_session)):
    """Add an optical measurement. If it is linked to a SAR detection, the comparison re-runs at once."""
    if body.sar_detection_id and s.get(SarDetection, body.sar_detection_id) is None:
        raise HTTPException(404, "No such SAR detection")
    o = OpticalObservation(**body.model_dump())
    s.add(o)
    s.commit()
    sun = sun_position(utc(o.acquired_at), o.lat, o.lon)
    out = {"optical_id": o.id, **sun}
    if o.sar_detection_id:
        c = compare_detection(s, o.sar_detection_id, o.id)
        out["comparison"] = {"comparison_id": c.id, **c.card}
    return out


@router.get("/sun")
def sun(lat: float, lon: float, when: datetime):
    """Sun elevation / azimuth for a place and time: is an optical shadow even possible?"""
    from app.analytics.sun import max_sun_elevation
    return {**sun_position(when, lat, lon), "max_sun_elevation_at_lat": max_sun_elevation(lat)}


class ModelComparisonIn(BaseModel):
    vessel_id: int
    method: str          # physical / cad / electronic
    colour: str          # bw / color
    score: float         # 0-1 similarity of the SAR target to this ship's reference render
    reviewer: str | None = None
    render_ref: str | None = None
    notes: str | None = None


@router.post("/detections/{det_id}/model-comparisons")
def add_model_comparison(det_id: int, body: ModelComparisonIn, s: Session = Depends(get_session)):
    """Record a 3D-model reference comparison; the confidence score re-runs at once."""
    if s.get(SarDetection, det_id) is None:
        raise HTTPException(404, "No such detection")
    if s.get(Vessel, body.vessel_id) is None:
        raise HTTPException(404, "No such vessel")
    if body.method not in ("physical", "cad", "electronic"):
        raise HTTPException(400, "method must be physical, cad or electronic")
    if body.colour not in ("bw", "color"):
        raise HTTPException(400, "colour must be bw or color")
    if not 0 <= body.score <= 1:
        raise HTTPException(400, "score must be between 0 and 1")
    s.add(ModelComparison(sar_detection_id=det_id, **body.model_dump()))
    s.commit()
    c = compare_detection(s, det_id)
    return {"comparison_id": c.id, **c.card}


@router.get("/detections/{det_id}/model-comparisons")
def list_model_comparisons(det_id: int, s: Session = Depends(get_session)):
    rows = s.scalars(select(ModelComparison).where(ModelComparison.sar_detection_id == det_id)).all()
    return [{"id": m.id, "vessel_id": m.vessel_id, "method": m.method, "colour": m.colour, "score": m.score,
             "reviewer": m.reviewer, "render_ref": m.render_ref, "notes": m.notes, "created_at": iso(m.created_at)}
            for m in rows]


class FeatureCheckIn(BaseModel):
    vessel_id: int
    feature: str              # flag, radar_system, superstructure, cargo_system, cranes, funnel, ...
    expected: str | None = None
    observed: str | None = None
    match: float              # 0-1: 1 = consistent with this ship, 0 = contradicts it
    source: str | None = None
    reviewer: str | None = None
    notes: str | None = None


@router.post("/detections/{det_id}/feature-checks")
def add_feature_check(det_id: int, body: FeatureCheckIn, s: Session = Depends(get_session)):
    """Record one ship feature checked against this target; the confidence score re-runs at once."""
    if s.get(SarDetection, det_id) is None or s.get(Vessel, body.vessel_id) is None:
        raise HTTPException(404, "No such detection or vessel")
    if not 0 <= body.match <= 1:
        raise HTTPException(400, "match must be between 0 and 1")
    s.add(FeatureCheck(sar_detection_id=det_id, **body.model_dump()))
    s.commit()
    c = compare_detection(s, det_id)
    return {"comparison_id": c.id, **c.card}
