"""
train.py  -  Module 3: train a detector on the lab labels, run it, export the same metrics.

Module 1 (annotate) labels every photo with two oriented boxes: 0 = vessel, 1 = shadow.
Here we train an Ultralytics YOLO-OBB model on those labels so new photos (and, later,
satellite optical chips) can be measured without the hand-tuned OpenCV steps.

    pip install ultralytics            (big download - only needed for this module)

Train:    python -m lab.train train   --data lab_out/yolo/dataset.yaml --epochs 100
Predict:  python -m lab.train predict --weights runs/obb/train/weights/best.pt \
                                      --images "D:/Lab/11-3-26Sun25Camera25" \
                                      [--metrics lab_out/vessel_shadow_metrics.csv] --out lab_out
          -> lab_out/predicted_metrics.csv  (same column names as annotate where they overlap)

With --metrics, the predicted boxes get the session's mm-per-pixel scale and sun/camera
angles from Module 1, so the export has lengths in mm and a shadow-reach estimate.

The model is only as good as the labels: look at the Module 1 debug overlays first and
only train on sessions that Module 2 (validate) is happy with.
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from lab import settings as S

CLASS_NAMES = {0: "vessel", 1: "shadow"}


def corners_to_box(corners) -> dict:
    """4 corner points (x, y) -> centre, length (long side), width, axis angle
    (degrees clockwise from image up, 0-180) - same convention as annotate.obb."""
    c = np.asarray(corners, float).reshape(4, 2)
    e1, e2 = c[1] - c[0], c[2] - c[1]
    l1, l2 = np.linalg.norm(e1), np.linalg.norm(e2)
    long_edge = e1 if l1 >= l2 else e2
    axis = (math.degrees(math.atan2(long_edge[0], -long_edge[1])) + 360) % 180
    cx, cy = c.mean(0)
    return {"cx": float(cx), "cy": float(cy), "length": float(max(l1, l2)), "width": float(min(l1, l2)),
            "axis_deg": float(axis), "corners": c}


def overhang_px(vessel_corners, shadow_corners, direction_deg):
    """How far the shadow box sticks out past the vessel box along an image direction."""
    a = math.radians(direction_deg)
    u = np.array([math.sin(a), -math.cos(a)])
    return float((np.asarray(shadow_corners) @ u).max() - (np.asarray(vessel_corners) @ u).max())


def boxes_to_metrics(boxes: list, meta: dict = None, settings: dict = None) -> dict:
    """boxes: [{"cls": 0|1, "conf": float, "corners": 4x2}] for ONE photo.
    Keeps the most confident vessel and shadow. Returns a metrics row."""
    meta, settings = meta or {}, settings or S.load()
    best = {}
    for b in boxes:
        if b["cls"] not in best or b["conf"] > best[b["cls"]]["conf"]:
            best[b["cls"]] = b
    out = {"n_detections": len(boxes)}
    vb = sb = None
    if 0 in best:
        vb = corners_to_box(best[0]["corners"])
        out.update(vessel_conf=round(best[0]["conf"], 3), vessel_cx=vb["cx"], vessel_cy=vb["cy"],
                   vessel_length_px=vb["length"], vessel_width_px=vb["width"], vessel_axis_deg=round(vb["axis_deg"], 1))
    if 1 in best:
        sb = corners_to_box(best[1]["corners"])
        out.update(shadow_conf=round(best[1]["conf"], 3), shadow_cx=sb["cx"], shadow_cy=sb["cy"],
                   shadow_length_px=sb["length"], shadow_width_px=sb["width"], shadow_axis_deg=round(sb["axis_deg"], 1),
                   shadow_aspect_ratio=round(sb["length"] / sb["width"], 3) if sb["width"] else None)
    out["status"] = "OK" if vb and sb else ("PARTIAL" if vb or sb else "REJECT")
    if vb and sb:
        # shadow direction in the image: from the vessel centre toward the shadow centre
        d = (math.degrees(math.atan2(sb["cx"] - vb["cx"], -(sb["cy"] - vb["cy"]))) + 360) % 360
        out["shadow_dir_img_deg"] = round(d, 1)
        reach_img = overhang_px(vb["corners"], sb["corners"], d)
        out["reach_img_px"] = round(reach_img, 1)
        scale = meta.get("px_per_mm_ground")
        cam = meta.get("camera_angle")
        if scale and scale == scale and cam is not None and cam == cam:
            # un-foreshorten the reach along the camera's look axis, then pixels -> mm
            f = math.cos(math.radians(S.off_nadir(settings, cam)))
            a = math.radians(d)
            look = settings["camera_look_direction_in_image"]
            along, across = (math.cos(a), math.sin(a)) if look in ("up", "down") else (math.sin(a), math.cos(a))
            ground = reach_img * math.hypot(across, along / f)
            reach_mm = ground / scale
            out["reach_ground_mm"] = round(reach_mm, 1)
            sun = meta.get("sun_elevation")
            v = S.vessel(settings, meta.get("vessel"))
            if sun and sun > 0:
                theo = v["shadow_height_mm"] / math.tan(math.radians(sun))
                out.update(theoretical_reach_mm=round(theo, 1), K_ground=round(reach_mm / theo, 3),
                           height_from_shadow_mm=round(reach_mm * math.tan(math.radians(sun)), 1))
    return out


# ------------------------------------------------------------------ Ultralytics wrappers
def _yolo():
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("Module 3 needs Ultralytics:  pip install ultralytics")
    return YOLO


def train(data, model="yolo11n-obb.pt", epochs=100, imgsz=1024, batch=8, device=None, project=None):
    YOLO = _yolo()
    m = YOLO(model)
    # flips/rotations are fine (the ship turns on the turntable anyway); colour jitter kept mild
    # so the shadow's darkness - which is what we measure - isn't distorted.
    return m.train(data=str(data), epochs=epochs, imgsz=imgsz, batch=batch, device=device, project=project,
                   degrees=180, fliplr=0.5, flipud=0.5, hsv_v=0.2, hsv_s=0.3, mosaic=0.5)


def predict(weights, images, metrics_csv=None, settings=None, conf=0.25, imgsz=1024):
    YOLO = _yolo()
    settings = settings or S.load()
    model = YOLO(str(weights))
    paths = sorted(p for p in Path(images).rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    # session info (scale, angles) from Module 1, by image name
    info = {}
    if metrics_csv:
        m = pd.read_csv(metrics_csv)
        scale = m.groupby("session")["px_per_mm_ground"].median()
        for _, r in m.iterrows():
            info[Path(str(r["image_path"])).name + "|" + Path(str(r["session"])).name] = {
                **r.to_dict(), "px_per_mm_ground": scale.get(r["session"])}
    rows = []
    for p in paths:
        res = model.predict(str(p), conf=conf, imgsz=imgsz, verbose=False)[0]
        boxes = []
        if res.obb is not None and len(res.obb):
            for c, k, s in zip(res.obb.xyxyxyxy.cpu().numpy(), res.obb.cls.cpu().numpy(), res.obb.conf.cpu().numpy()):
                boxes.append({"cls": int(k), "conf": float(s), "corners": c})
        meta = info.get(p.name + "|" + p.parent.name, {})
        rows.append({"image_path": str(p), "session": p.parent.name,
                     **{k: meta.get(k) for k in ("vessel", "sun_elevation", "camera_angle", "rotation")},
                     **boxes_to_metrics(boxes, meta, settings)})
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train / run the YOLO-OBB vessel+shadow detector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--data", default="lab_out/yolo/dataset.yaml")
    t.add_argument("--model", default="yolo11n-obb.pt")
    t.add_argument("--epochs", type=int, default=100)
    t.add_argument("--imgsz", type=int, default=1024)
    t.add_argument("--batch", type=int, default=8)
    t.add_argument("--device", default=None, help="0 for the first GPU, cpu for CPU")
    p = sub.add_parser("predict")
    p.add_argument("--weights", required=True)
    p.add_argument("--images", required=True)
    p.add_argument("--metrics", default=None, help="Module 1 CSV, for mm scale and angles")
    p.add_argument("--settings", default=None)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--out", default="lab_out")
    a = ap.parse_args(argv)
    if a.cmd == "train":
        train(a.data, a.model, a.epochs, a.imgsz, a.batch, a.device)
        print("Done. Weights are in runs/obb/train*/weights/best.pt")
    else:
        df = predict(a.weights, a.images, a.metrics, S.load(a.settings), a.conf)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / "predicted_metrics.csv", index=False)
        print(df["status"].value_counts().to_string())
        print(f"Wrote {out / 'predicted_metrics.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
