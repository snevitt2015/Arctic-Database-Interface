"""
annotate.py  (Module 1)  -  Find the hull and its shadow in every photo, repair shadows cut
by the turntable ring, measure them, and write YOLO-OBB training labels.

    python -m lab.annotate --manifest lab_out/manifest.csv --preview 5 --debug-save-dir lab_out/debug
    python -m lab.annotate --manifest lab_out/manifest.csv --out lab_out --debug-save-dir lab_out/debug

How it works (per photo):
  1. Background model. Fit a smooth surface to the board's brightness (separately inside and
     outside the turntable), ignoring anything that doesn't fit. Its leftover scatter is
     "background fit rms": above ~3 the board is unevenly lit or dirty.
  2. Ring. Find the turntable ring - an ELLIPSE in oblique photos - and mark a narrow band
     around its edge as "don't trust" (the raised lip brightens the shadow there).
  3. Split hull from shadow. Shadow = darker than the board, same colour, smooth.
     Hull = everything else that differs from the board (bright tanks, edges, texture), plus
     anything inside the hull's outline (so a grey hull isn't mistaken for shadow).
  4. Repair the shadow across the ring:
       a) ignore the ring band,
       b) close gaps with a LINE-shaped kernel pointing along the shadow (bridges the band
          without fattening the shadow sideways); only pixels inside the ring band may be added,
       c) merge shadow fragments that lie on the same ray from the hull, within a short gap.
          Fragments off the ray or far away are left out (not someone else's shadow / dirt).
  5. Measure: oriented boxes (cv2.minAreaRect), shadow direction, reach, area, aspect ratio,
     core darkness and penumbra sharpness; convert to millimetres on the board.
  6. Write YOLO-OBB labels (class 0 vessel, 1 shadow) and a train/val split with dataset.yaml.

Everything measured goes to vessel_shadow_metrics.csv - the optical reference library.
"""
import argparse
import math
import shutil
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from lab import settings as S

CLASS_NAMES = {0: "vessel", 1: "shadow"}


# ============================================================== 1. background
def _fit_surface(values, xs, ys, degree=2, iters=4, clip=2.5):
    """Robust 2-D polynomial fit. Returns (coeffs, inlier rms, inlier mask)."""
    def design(x, y):
        cols = [np.ones_like(x)]
        for d in range(1, degree + 1):
            for i in range(d + 1):
                cols.append((x ** (d - i)) * (y ** i))
        return np.stack(cols, axis=1)
    A = design(xs, ys)
    # robust start: ship and shadow can cover much of a zone, so begin from the median and its spread
    med = np.median(values)
    mad = 1.4826 * np.median(np.abs(values - med)) + 1e-6
    keep = np.abs(values - med) < 2.5 * mad
    coef, rms = None, float("nan")
    for _ in range(iters):
        if keep.sum() < 20:
            break
        coef, *_ = np.linalg.lstsq(A[keep], values[keep], rcond=None)
        res = values - A @ coef
        rms = float(np.std(res[keep]))
        mad_r = 1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))) + 1e-6
        keep = np.abs(res - np.median(res[keep])) < clip * max(min(rms, mad_r * 1.5), 0.5)
    return coef, rms, design


def background_model(lab, zones, step=8):
    """Smooth background for each Lab channel, per zone.

    zones: list of (apply_mask, sample_mask, degree). The surface is FITTED on sample_mask and
    APPLIED over apply_mask. Inside the turntable the ship and shadow can cover most of the
    disc, so that zone is sampled only on a thin rim just inside the ring (bare turntable).
    Returns (background, rms of L on the FIRST zone = the board, in Lab units)."""
    h, w = lab.shape[:2]
    bg = np.zeros_like(lab, dtype=np.float32)
    yy, xx = np.mgrid[0:h:step, 0:w:step]
    board_rms = float("nan")
    for zi, (apply_mask, sample_mask, degree) in enumerate(zones):
        zs = sample_mask[0:h:step, 0:w:step]
        if zs.sum() < 30:
            zs = apply_mask[0:h:step, 0:w:step]
        if zs.sum() < 30:
            continue
        xs, ys = xx[zs] / w, yy[zs] / h
        full_y, full_x = np.nonzero(apply_mask)
        for ch in range(3):
            coef, rms, design = _fit_surface(lab[0:h:step, 0:w:step, ch][zs].astype(np.float64), xs, ys, degree=degree)
            if coef is None:
                continue
            bg[full_y, full_x, ch] = (design(full_x / w, full_y / h) @ coef).astype(np.float32)
            if ch == 0 and zi == 0:
                board_rms = rms
    return bg, board_rms


# ============================================================== 2. ring
def find_ring(gray, setting, band_px, expected_ratio=None):
    """Return (ellipse ((cx,cy),(major,minor),angle) or None, band mask). setting: 'auto', 'none', or
    [x_frac, y_frac, rx_frac, ry_frac]."""
    h, w = gray.shape
    band = np.zeros((h, w), np.uint8)
    if setting == "none":
        return None, band.astype(bool)
    if isinstance(setting, tuple) and len(setting) == 3 and isinstance(setting[0], tuple):
        ell = setting                                          # an ellipse found earlier for this session
    elif isinstance(setting, (list, tuple)):
        x, y, rx = setting[0] * w, setting[1] * h, setting[2] * w
        ry = (setting[3] if len(setting) > 3 else setting[2]) * w
        ell = ((x, y), (2 * rx, 2 * ry), 0.0)
    else:
        ell = _ransac_ring(gray, expected_ratio=expected_ratio)
    if ell is None:
        return None, band.astype(bool)
    cv2.ellipse(band, ell, 1, int(2 * band_px))
    return ell, band.astype(bool)



def _ellipse_residual(pts, ell):
    """Distance (px, approx) of points from an ellipse outline."""
    (cx, cy), (ma, mi), ang = ell
    a, b = max(ma, 1) / 2, max(mi, 1) / 2
    t = math.radians(ang)
    x, y = pts[:, 0] - cx, pts[:, 1] - cy
    xr = x * math.cos(t) + y * math.sin(t)
    yr = -x * math.sin(t) + y * math.cos(t)
    r = np.sqrt((xr / a) ** 2 + (yr / b) ** 2)
    return np.abs(r - 1) * min(a, b)


def _ransac_ring(gray, tries=400, tol_px=4.0, seed=0, expected_ratio=None):
    """Find the turntable ring: a large, roughly central ellipse traced by thin bright/dark lines.
    The ship and its shadow break the ring into arcs, so we fit ellipses through random groups of
    arcs and keep the one that the most line pixels agree with (RANSAC)."""
    h, w = gray.shape
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    lines = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k).astype(np.int32) + \
            cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k).astype(np.int32)
    lines = np.clip(lines, 0, 255).astype(np.uint8)
    _, bw = cv2.threshold(lines, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    arcs = [c.reshape(-1, 2).astype(np.float32) for c in contours if len(c) >= 60]
    if len(arcs) < 1:
        return None
    allpts = np.concatenate(arcs)
    if len(allpts) > 20000:
        allpts = allpts[np.random.default_rng(seed).choice(len(allpts), 20000, replace=False)]
    rng = np.random.default_rng(seed)
    weights = np.array([len(a) for a in arcs], float); weights /= weights.sum()
    best, best_score = None, 0.0
    for _ in range(tries):
        pick = rng.choice(len(arcs), size=min(len(arcs), rng.integers(1, 4)), replace=False, p=weights)
        pts = np.concatenate([arcs[i] for i in pick])
        if len(pts) < 5:
            continue
        ell = cv2.fitEllipse(pts)
        (ex, ey), (ma, mi), _ = ell
        big, small = max(ma, mi), min(ma, mi)
        if not (0.12 * w < big < 0.95 * w and small > 0.06 * h and small / big > 0.15) or not (0 < ex < w and 0 < ey < h):
            continue
        # the ring lies flat on the board: its long axis runs across the image (perpendicular to the
        # camera's look direction) and it is flattened by about sin(camera elevation)
        ang = ell[2] % 180
        long_axis_horizontal = (ma >= mi and (ang < 20 or ang > 160)) or (mi > ma and 70 < ang < 110)
        if expected_ratio and expected_ratio < 0.95 and not long_axis_horizontal:
            continue
        if expected_ratio and abs(small / big - expected_ratio) > 0.25:
            continue
        inl = _ellipse_residual(allpts, ell) < tol_px
        perimeter = math.pi * (big + small) / 2
        coverage = min(1.0, inl.sum() / perimeter)        # fraction of the outline backed by line pixels
        central = 1 - min(1, math.hypot(ex - w / 2, ey - h / 2) / (0.6 * w))
        score = coverage * (0.4 + central) * math.sqrt(big)
        if coverage > 0.35 and score > best_score:
            best, best_score = ell, score
    if best is not None:                                    # refine on all agreeing pixels
        inl = _ellipse_residual(allpts, best) < tol_px
        if inl.sum() >= 5:
            best = cv2.fitEllipse(allpts[inl])
    return best

def ellipse_mask(shape, ell, grow=0):
    m = np.zeros(shape, np.uint8)
    (cx, cy), (ma, mi), ang = ell
    cv2.ellipse(m, ((cx, cy), (max(ma + 2 * grow, 1), max(mi + 2 * grow, 1)), ang), 1, -1)
    return m.astype(bool)


# ============================================================== 3. hull vs shadow
def split_hull_shadow(lab, bg, rms, ring_band):
    """Boolean masks (vessel, shadow_raw) plus the darkness threshold used."""
    d = lab.astype(np.float32) - bg
    dL = d[..., 0]
    chroma = np.hypot(d[..., 1], d[..., 2])
    L = lab[..., 0].astype(np.float32)
    # "crisp detail": hull edges, tank outlines, deck fittings are sharp; a lamp's shadow edge is soft.
    # Difference of a fine and a coarser blur responds to sharp edges far more than to soft ones.
    fine = cv2.GaussianBlur(L, (0, 0), 0.8)
    soft = cv2.GaussianBlur(L, (0, 0), 2.0)
    tex = cv2.GaussianBlur(np.abs(fine - soft), (0, 0), 2)
    t_dark = max(8.0, 4.0 * rms)
    t_diff = max(10.0, 4.0 * rms)
    t_tex = max(1.5, np.percentile(tex, 90) * 4)
    different = (np.abs(dL) > t_diff) | (chroma > max(8.0, 3 * rms)) | (tex > t_tex)
    shadow_like = (dL < -t_dark) & (chroma < max(10.0, 3 * rms)) & (tex <= t_tex)
    # dark detail right next to the shadow is the shadow's own edge, not the ship
    near_shadow = cv2.dilate(shadow_like.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    ring_zone = cv2.dilate(ring_band.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool)
    # ...unless it is CRISP (a printed hull edge), which a lamp's soft shadow edge never is
    soft_shadow_edge = near_shadow & (dL < 0) & (tex < 2.5 * t_tex)
    vessel_seed = different & ~shadow_like & ~ring_zone & ~soft_shadow_edge
    vessel_seed = cv2.morphologyEx(vessel_seed.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab_cc, stats, cents = cv2.connectedComponentsWithStats(vessel_seed, 8)
    if n <= 1:
        return np.zeros_like(ring_band), shadow_like & ~ring_band, t_dark
    h, w = ring_band.shape
    # the vessel = big seed blobs near the middle; join the main one with nearby ones (tanks + hull edges)
    score = stats[1:, cv2.CC_STAT_AREA] / (1 + np.hypot(cents[1:, 0] - w / 2, cents[1:, 1] - h / 2) / (0.25 * w))
    main = 1 + int(np.argmax(score))
    keep = np.zeros(n, bool); keep[main] = True
    mx, my, mw, mh = stats[main, :4]
    reach = 0.6 * max(mw, mh)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 30 and math.hypot(cents[i, 0] - cents[main, 0], cents[i, 1] - cents[main, 1]) < reach + 0.5 * max(stats[i, 2], stats[i, 3]):
            keep[i] = True
    seed = keep[lab_cc]
    pts = cv2.findNonZero(seed.astype(np.uint8))
    hull_poly = cv2.convexHull(pts)
    vessel = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(vessel, hull_poly, 1)
    vessel = vessel.astype(bool)
    shadow = shadow_like & ~vessel & ~ring_band
    return vessel, shadow, t_dark


# ============================================================== 4. shadow repair across the ring
def _components(mask, min_px=40):
    n, cc, stats, cents = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    return [(cc == i, stats[i], cents[i]) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_px]


def repair_shadow(shadow, vessel, ring_band, band_px, max_angle_deg=15):
    """Returns (shadow mask, bridged-pixels mask, unit direction vector or None)."""
    vy, vx = np.nonzero(vessel)
    if len(vx) == 0:
        return shadow, np.zeros_like(shadow), None
    vc = np.array([vx.mean(), vy.mean()])
    vessel_near = cv2.dilate(vessel.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    comps = _components(shadow)
    if not comps:
        return shadow, np.zeros_like(shadow), None
    attached = [c for c in comps if (c[0] & vessel_near).any()]
    main = max(attached or comps, key=lambda c: c[1][cv2.CC_STAT_AREA])
    my, mx = np.nonzero(main[0])
    u = np.array([mx.mean(), my.mean()]) - vc
    if np.linalg.norm(u) < 1:
        return shadow, np.zeros_like(shadow), None
    u /= np.linalg.norm(u)

    merged = main[0].copy()
    band_near = cv2.dilate(ring_band.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    max_gap = 2 * band_px + 25
    # (c) add fragments that sit on the same ray from the hull, close to what we already have
    changed = True
    while changed:
        changed = False
        for m, st, cen in comps:
            if (m & merged).any():
                continue
            v = cen - vc
            on_ray = np.linalg.norm(v) >= 1 and np.degrees(math.acos(np.clip(v @ u / np.linalg.norm(v), -1, 1))) <= max_angle_deg
            touches_hull = bool((m & vessel_near).any())       # dark, smooth and touching the ship = its shadow
            # cut by the ring: both pieces end at the ring band, so the band is what separates them
            across_ring = bool((m & band_near).any() and (merged & band_near).any())
            if not (on_ray or touches_hull or across_ring):
                continue
            # allowed gap: the ring band plus a margin (radius, in pixels)
            gap = cv2.dilate(merged.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * max_gap + 1, 2 * max_gap + 1)))
            if (m & gap.astype(bool)).any():
                merged |= m
                changed = True
    my, mx = np.nonzero(merged)
    u2 = np.array([mx.mean(), my.mean()]) - vc
    if np.linalg.norm(u2) >= 1:
        u = u2 / np.linalg.norm(u2)
    # (b) directional closing along the shadow, only allowed to add pixels inside the ring band
    length = int(2 * band_px + 15) | 1
    k = np.zeros((length, length), np.uint8)
    c = length // 2
    cv2.line(k, (int(c - u[0] * c), int(c - u[1] * c)), (int(c + u[0] * c), int(c + u[1] * c)), 1, 3)
    closed = cv2.morphologyEx(merged.astype(np.uint8), cv2.MORPH_CLOSE, k).astype(bool)
    bridge = closed & ~merged & ring_band & ~vessel
    out = merged | bridge
    return out, bridge, u


# ============================================================== 5. measurements
def obb(mask):
    pts = cv2.findNonZero(mask.astype(np.uint8))
    if pts is None or len(pts) < 5:
        return None
    (cx, cy), (w, h), ang = cv2.minAreaRect(pts)
    length, width = max(w, h), min(w, h)
    # angle of the long side, degrees clockwise from image "up" (0-180)
    corners = cv2.boxPoints(((cx, cy), (w, h), ang))
    e1, e2 = corners[1] - corners[0], corners[2] - corners[1]
    long_edge = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
    axis = (math.degrees(math.atan2(long_edge[0], -long_edge[1])) + 360) % 180
    return {"cx": cx, "cy": cy, "length": length, "width": width, "axis_deg": axis, "corners": corners}


def to_ground(points_xy, look_dir="up", foreshortening=1.0):
    """Undo the camera's foreshortening (affine approximation): stretch the image axis along the
    camera's look direction by 1/f."""
    p = np.asarray(points_xy, float).copy()
    if look_dir in ("up", "down"):
        p[:, 1] /= foreshortening
    else:
        p[:, 0] /= foreshortening
    return p


def measure(img_bgr, vessel, shadow, u, settings, meta, scale_back):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    out = {}
    vb, sb = obb(vessel), obb(shadow) if shadow.any() else None
    if vb:
        out.update(vessel_cx=vb["cx"] * scale_back, vessel_cy=vb["cy"] * scale_back, vessel_length_px=vb["length"] * scale_back,
                   vessel_width_px=vb["width"] * scale_back, vessel_axis_deg=round(vb["axis_deg"], 1))
    if sb:
        out.update(shadow_cx=sb["cx"] * scale_back, shadow_cy=sb["cy"] * scale_back, shadow_length_px=sb["length"] * scale_back,
                   shadow_width_px=sb["width"] * scale_back, shadow_axis_deg=round(sb["axis_deg"], 1),
                   shadow_aspect_ratio=round(sb["length"] / max(sb["width"], 1e-6), 3),
                   shadow_area_px=int(shadow.sum() * scale_back ** 2))
    if not (vb and sb is not None and u is not None):
        return out, vb, sb
    vy, vx = np.nonzero(vessel); sy, sx = np.nonzero(shadow)
    vc = np.array([vx.mean(), vy.mean()])
    t_shadow = (np.stack([sx, sy], 1) - vc) @ u
    t_vessel = (np.stack([vx, vy], 1) - vc) @ u
    tip_i = int(np.argmax(t_shadow))
    tip = np.array([sx[tip_i], sy[tip_i]])
    out.update(shadow_dir_deg=round((math.degrees(math.atan2(u[0], -u[1])) + 360) % 360, 1),
               reach_from_centre_px=float(t_shadow.max() * scale_back),
               reach_from_hull_px=float(max(0.0, t_shadow.max() - t_vessel.max()) * scale_back),
               shadow_tip_x=float(tip[0] * scale_back), shadow_tip_y=float(tip[1] * scale_back))
    # core darkness and penumbra sharpness
    core = cv2.erode(shadow.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    ring_out = cv2.dilate(shadow.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & ~shadow & ~vessel
    edge = (cv2.dilate(shadow.astype(np.uint8), np.ones((5, 5), np.uint8)) - cv2.erode(shadow.astype(np.uint8), np.ones((5, 5), np.uint8))).astype(bool) & ~vessel
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.hypot(gx, gy) / 8.0
    core_mean = float(gray[core].mean()) if core.any() else float(gray[shadow].mean())
    board_mean = float(gray[ring_out].mean()) if ring_out.any() else float("nan")
    contrast = board_mean - core_mean
    g = float(np.percentile(grad[edge], 90)) if edge.any() else float("nan")
    out.update(shadow_core_gray=round(core_mean, 1), board_gray=round(board_mean, 1),
               shadow_contrast=round(contrast, 1),
               penumbra_gradient=round(g, 2),                                   # grey levels per pixel at the edge
               penumbra_width_px=round(contrast / g * scale_back, 1) if g and g > 0 else None)
    # ---- on the board, in millimetres
    # Undo the camera's foreshortening, then measure how far the shadow sticks out BEYOND the hull
    # in each direction. For an object of height h the largest overhang is h / tan(sun), and it
    # occurs in the sun's direction (shadow = hull footprint swept along the sun vector), so the
    # same search gives both the true shadow direction and the shadow length on the board.
    v = S.vessel(settings, meta.get("vessel"))
    cam = meta.get("camera_angle")
    cam_ok = cam is not None and cam == cam
    f = math.cos(math.radians(S.off_nadir(settings, cam))) if cam_ok else 1.0
    look = settings["camera_look_direction_in_image"]
    step = max(1, int(math.sqrt((len(vx) + len(sx)) / 40000)))
    V = to_ground(np.stack([vx[::step], vy[::step]], 1), look, f)
    Sg = to_ground(np.stack([sx[::step], sy[::step]], 1), look, f)
    angles = np.radians(np.arange(0, 360, 1.0))
    U = np.stack([np.sin(angles), -np.cos(angles)], 1)            # image-style: 0 deg = up, clockwise
    overhang = (Sg @ U.T).max(0) - (V @ U.T).max(0)
    # smooth over +-3 deg so one stray pixel can't pick the direction
    k = np.ones(7) / 7
    overhang_s = np.convolve(np.r_[overhang[-3:], overhang, overhang[:3]], k, mode="valid")
    best = int(np.argmax(overhang_s))
    reach_ground_px = float(overhang[best])
    ug = U[best]
    # back to an image direction for drawing / reporting
    ui = np.array([ug[0], ug[1] * f]) if look in ("up", "down") else np.array([ug[0] * f, ug[1]])
    ui /= np.linalg.norm(ui)
    ti = int(np.argmax(Sg @ ug))
    tip_img = np.array([sx[::step][ti], sy[::step][ti]])
    base_img = tip_img - ui * (reach_ground_px * (np.linalg.norm(ui * [1, 1]) if True else 1))
    out["_profile"] = overhang                      # overhang (ground px) for every direction 0..359 deg
    out["_f"], out["_look"] = f, look
    out.update(shadow_dir_deg=round(float(np.degrees(angles[best])) % 360, 1),
               shadow_dir_img_deg=round((math.degrees(math.atan2(ui[0], -ui[1])) + 360) % 360, 1),
               shadow_tip_x=float(tip_img[0] * scale_back), shadow_tip_y=float(tip_img[1] * scale_back))
    rect = cv2.minAreaRect(V.astype(np.float32))
    hull_ground_px = max(rect[1])
    px_per_mm = hull_ground_px / v["model_length_mm"] if v.get("model_length_mm") else None
    # How trustworthy is that scale? Tall parts (tanks) lean toward the camera, which stretches the
    # hull along the look direction. The length is cleanest when the hull lies ACROSS the look
    # direction, so remember |sin(angle to look axis)| (1 = across, 0 = pointing at the camera).
    c = V - V.mean(0)
    w, vec = np.linalg.eigh(c.T @ c)
    ax = vec[:, -1]
    look_axis = np.array([0.0, 1.0]) if look in ("up", "down") else np.array([1.0, 0.0])
    out["scale_quality"] = float(abs(ax[0] * look_axis[1] - ax[1] * look_axis[0]))
    if px_per_mm:
        reach_mm = reach_ground_px * step ** 0 / px_per_mm
        sun = meta.get("sun_elevation")
        theo = v["shadow_height_mm"] / math.tan(math.radians(sun)) if sun and sun > 0 else None
        phi = math.radians(cam) if cam_ok else 0.0
        out.update(px_per_mm_ground=round(px_per_mm, 4), reach_ground_mm=round(reach_mm, 1),
                   shadow_area_mm2=round(out["shadow_area_px"] / scale_back ** 2 / (px_per_mm ** 2) / f, 0),
                   theoretical_reach_mm=round(theo, 1) if theo else None,
                   # K on the board (what Module 2 checks): 1.0 = shadow exactly as long as the sun angle predicts
                   K_ground=round(reach_mm / theo, 3) if theo else None,
                   # K exactly as first specified, h / (tan(sun) * cos(cam)) - kept for comparison only
                   K_requested=round(reach_mm / (v["shadow_height_mm"] / (math.tan(math.radians(sun)) * math.cos(phi))), 3) if theo else None,
                   height_from_shadow_mm=round(reach_mm * math.tan(math.radians(sun)), 1) if sun and sun > 0 else None)
    return out, vb, sb


# ============================================================== 6. YOLO-OBB labels
def obb_label(cls, corners, img_w, img_h):
    """'cls x1 y1 ... x4 y4' normalised to 0-1 (clipped). Returns (line, was_clipped)."""
    xy = np.asarray(corners, float) / [img_w, img_h]
    clipped = bool(((xy < 0) | (xy > 1)).any())
    xy = np.clip(xy, 0, 1)
    return f"{cls} " + " ".join(f"{v:.6f}" for v in xy.ravel()), clipped


# ============================================================== one photo
def process_image(path, meta, settings, fixed_ring=None):
    raw = cv2.imread(str(path))
    if raw is None:
        return {"status": "REJECT", "reasons": "unreadable_file"}, None
    H, W = raw.shape[:2]
    s = min(1.0, settings["work_max_side_px"] / max(H, W))
    img = cv2.resize(raw, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA) if s < 1 else raw.copy()
    h, w = img.shape[:2]
    band_px = settings["ring_band_px"]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cam = meta.get("camera_angle")
    ratio = math.cos(math.radians(S.off_nadir(settings, cam))) if cam is not None and cam == cam else None
    ring_setting = fixed_ring if fixed_ring is not None else settings["ring"]
    ring, band = find_ring(gray, ring_setting, band_px, expected_ratio=ratio)
    if ring is not None:
        inside = ellipse_mask((h, w), ring, grow=-band_px)
        outside = ~ellipse_mask((h, w), ring, grow=band_px)
        rim = inside & ~ellipse_mask((h, w), ring, grow=-band_px - max(12, int(0.06 * min(ring[1]))))
        zones = [(outside | band, outside, 2), (inside, rim, 1)]
    else:
        everything = np.ones((h, w), bool)
        zones = [(everything, everything, 2)]
    bg, rms = background_model(lab, zones)
    vessel, shadow_raw, _ = split_hull_shadow(lab, bg, rms, band)
    shadow, bridge, u = repair_shadow(shadow_raw, vessel, band, band_px) if vessel.any() else (shadow_raw, np.zeros_like(shadow_raw), None)
    scale_back = 1 / s
    m, vb, sb = measure(img, vessel, shadow, u, settings, meta, scale_back)

    reasons = []
    border = np.zeros((h, w), bool); border[:3, :] = border[-3:, :] = border[:, :3] = border[:, -3:] = True
    if not vessel.any():
        reasons.append("no_vessel")
    elif vessel.sum() < 0.002 * h * w:
        reasons.append("vessel_too_small")
    elif (vessel & border).any():
        reasons.append("vessel_touches_border")
    if vessel.any() and not shadow.any():
        reasons.append("no_shadow")
    if shadow.any() and (shadow & border).any():
        reasons.append("shadow_touches_border")
    if rms > settings["background_rms_limit"]:
        reasons.append("uneven_background")
    status = "REJECT" if any(r in reasons for r in ("no_vessel", "vessel_too_small", "vessel_touches_border")) else \
        "PARTIAL" if any(r in reasons for r in ("no_shadow", "shadow_touches_border")) else "OK"
    row = {"status": status, "reasons": ",".join(reasons), "background_rms": round(rms, 2),
           "ring_found": ring is not None, "ring_bridged_px": int(bridge.sum() * scale_back ** 2),
           "image_w": W, "image_h": H, **m}
    labels = []
    for cls, box in ((0, vb), (1, sb)):
        if box is not None:
            line, clipped = obb_label(cls, box["corners"] * scale_back, W, H)
            labels.append(line)
            if clipped:
                row["reasons"] = ",".join(filter(None, [row["reasons"], f"{CLASS_NAMES[cls]}_box_clipped"]))
    debug = {"img": img, "vessel": vessel, "shadow": shadow, "bridge": bridge, "ring": ring, "band": band,
             "vb": vb, "sb": sb, "u": u, "row": row, "scale": s}
    return row, (labels, debug)


def session_ring(paths, settings, cam_angle, n=5):
    """Find the ring on up to n photos of a session and take the median ellipse. The ring doesn't
    move during a session, so this is far more reliable than finding it photo by photo."""
    if settings["ring"] != "auto":
        return None
    ratio = math.cos(math.radians(S.off_nadir(settings, cam_angle))) if cam_angle is not None and cam_angle == cam_angle else None
    found, size = [], None
    for p in list(paths)[:: max(1, len(paths) // n)][:n]:
        raw = cv2.imread(str(p))
        if raw is None:
            continue
        H, W = raw.shape[:2]
        s = min(1.0, settings["work_max_side_px"] / max(H, W))
        g = cv2.cvtColor(cv2.resize(raw, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA) if s < 1 else raw, cv2.COLOR_BGR2GRAY)
        size = g.shape
        e = _ransac_ring(g, expected_ratio=ratio)
        if e is not None:
            (cx, cy), (ma, mi), ang = e
            if ma < mi:                       # normalise so the first axis is the long, horizontal one
                ma, mi, ang = mi, ma, ang - 90
            found.append((cx, cy, ma, mi, (ang + 90) % 180 - 90))
    if len(found) < max(1, min(3, len(paths)) // 2 + (1 if len(paths) >= 3 else 0)):
        return None
    m = np.median(np.array(found), axis=0)
    return ((float(m[0]), float(m[1])), (float(m[2]), float(m[3])), float(m[4]))


def apply_session_direction(rows, profiles, settings, min_photos=5, window_deg=20):
    """The lamp stays put during a session; only the ship turns. So the shadow direction ON THE
    BOARD is the same in every photo of a session. Estimate it once (long shadows count more),
    then re-measure each photo's reach as the largest overhang within +-window_deg of that
    direction. (Exactly along it is worse: the overhang of a long hull with tanks changes fast
    with direction, so a few degrees of error costs a lot. Unrestricted, short shadows can make a
    photo pick a direction far off.)
    settings["lamp_direction_deg"] (optional) = a measured direction to use instead."""
    fixed = settings.get("lamp_direction_deg")
    by_session = {}
    for r in rows:
        if r.get("image_path") in profiles and r.get("status") != "REJECT":
            by_session.setdefault(r["session"], []).append(r)
    for sess, rs in by_session.items():
        # One scale per session: the camera doesn't move, so mm-per-pixel on the board is fixed.
        # Use photos where the hull lies across the camera's view (see measure); fall back to all.
        good = [r["px_per_mm_ground"] for r in rs if r.get("px_per_mm_ground") and r.get("scale_quality", 0) > 0.9]
        if len(good) < 3:
            good = [r["px_per_mm_ground"] for r in rs if r.get("px_per_mm_ground")]
        if len(good) >= 3:
            sc = float(np.median(good))
            for r in rs:
                r["px_per_mm_photo"] = r.get("px_per_mm_ground")
                r["px_per_mm_ground"] = round(sc, 4)
        if isinstance(fixed, dict):
            d = fixed.get(sess)
        else:
            d = fixed
        if d is None:
            if len(rs) < min_photos:
                continue
            ang = np.radians([r["shadow_dir_deg"] for r in rs])
            wts = np.array([max(profiles[r["image_path"]].max(), 0) for r in rs]) ** 2
            d = float(np.degrees(np.arctan2((np.sin(ang) * wts).sum(), (np.cos(ang) * wts).sum())) % 360)
        window = np.arange(int(round(d)) - window_deg, int(round(d)) + window_deg + 1) % 360
        for r in rs:
            prof = profiles[r["image_path"]]
            best = int(window[np.argmax(prof[window])])
            r["session_shadow_dir_deg"] = round(d, 1)
            r["photo_shadow_dir_deg"] = r.get("shadow_dir_deg")
            r["shadow_dir_deg"] = float(best)
            r["reach_photo_mm"] = r.get("reach_ground_mm")           # the photo's own estimate, kept for comparison
            if r.get("px_per_mm_ground"):
                reach = float(prof[best]) / r["px_per_mm_ground"]
                r["reach_ground_mm"] = round(reach, 1)
                if r.get("theoretical_reach_mm"):
                    r["K_ground"] = round(reach / r["theoretical_reach_mm"], 3)
                    r["height_from_shadow_mm"] = round(reach * math.tan(math.radians(r["sun_elevation"])), 1)
    return rows


# ============================================================== debug overlay
def draw_debug(debug, meta):
    img = debug["img"].copy()
    over = img.copy()
    over[debug["band"]] = (0.5 * over[debug["band"]] + [127, 0, 127]).astype(np.uint8)          # ring band, magenta tint
    over[debug["bridge"]] = (144, 238, 144)                                                     # bridged pixels, light green
    img = cv2.addWeighted(over, 0.6, img, 0.4, 0)
    for mask, colour in ((debug["vessel"], (255, 0, 0)), (debug["shadow"], (0, 200, 0))):      # blue hull, green shadow
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, cs, -1, colour, 2)
    for box in (debug["vb"], debug["sb"]):
        if box is not None:
            cv2.polylines(img, [box["corners"].astype(np.int32)], True, (0, 0, 255), 2)        # red OBBs
    if debug["ring"] is not None:
        cv2.ellipse(img, debug["ring"], (255, 0, 255), 1)
    r, s = debug["row"], debug["scale"]
    if "shadow_tip_x" in r and "shadow_dir_img_deg" in r:
        a = math.radians(r["shadow_dir_img_deg"])
        u = np.array([math.sin(a), -math.cos(a)])
        tip = np.array([r["shadow_tip_x"] * s, r["shadow_tip_y"] * s])
        vy, vx = np.nonzero(debug["vessel"])
        hull_edge = ((np.stack([vx, vy], 1) - tip) @ u).max()            # hull's furthest point along the sun line
        start = tip + u * hull_edge
        cv2.arrowedLine(img, tuple(int(v) for v in start), tuple(int(v) for v in tip), (0, 255, 255), 3, tipLength=0.08)
        cv2.line(img, tuple(int(v) for v in start - u[::-1] * [1, -1] * 40), tuple(int(v) for v in start + u[::-1] * [1, -1] * 40), (0, 255, 255), 1)
    cv2.rectangle(img, (0, 0), (img.shape[1], 92), (20, 20, 20), -1)
    lines = [f"{Path(meta['image_path']).name}  {meta.get('vessel')}  sun {meta.get('sun_elevation')}  cam {meta.get('camera_angle')}  rot {meta.get('rotation')}",
             f"{r['status']} {r['reasons']}   background rms {r['background_rms']}",
             f"shadow reach {r.get('reach_ground_mm', '?')} mm  theory {r.get('theoretical_reach_mm', '?')} mm  K {r.get('K_ground', '?')}"]
    for i, t in enumerate(lines):
        cv2.putText(img, t, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
    return img


# ============================================================== dataset
def write_dataset(rows, labels_by_path, out_dir, val_every=5):
    """Copy OK/PARTIAL photos + labels into YOLO folders. Validation = every 5th rotation of
    each session (so near-identical neighbours don't leak between train and val)."""
    root = Path(out_dir) / "yolo"
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    n = {"train": 0, "val": 0}
    for r in rows:
        if r["status"] == "REJECT" or not labels_by_path.get(r["image_path"]):
            continue
        split = "val" if int(r.get("index_in_session", 0)) % val_every == val_every - 1 else "train"
        src = Path(r["image_path"])
        stem = f"{Path(r['session']).name}_{src.stem}".replace(" ", "_")
        shutil.copy2(src, root / "images" / split / f"{stem}{src.suffix.lower()}")
        (root / "labels" / split / f"{stem}.txt").write_text("\n".join(labels_by_path[r["image_path"]]) + "\n")
        n[split] += 1
    (root / "dataset.yaml").write_text(
        f"path: {root.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: vessel\n  1: shadow\n")
    return root / "dataset.yaml", n


def preview_text(r, meta):
    lines = [f"{meta['session']}/{Path(meta['image_path']).name}",
             f"  metadata : vessel={meta['vessel']} sun={meta['sun_elevation']} cam={meta['camera_angle']} rot={meta['rotation']}",
             f"  status   : {r['status']} {r['reasons']}"]
    if "vessel_length_px" in r:
        lines.append(f"  vessel   : {r['vessel_length_px']:.0f} x {r['vessel_width_px']:.0f} px, axis {r['vessel_axis_deg']:.0f} deg")
    if "shadow_length_px" in r:
        lines.append(f"  shadow   : {r['shadow_length_px']:.0f} x {r['shadow_width_px']:.0f} px, axis {r['shadow_axis_deg']:.0f} deg,"
                     f" reach {r.get('reach_ground_mm', '?')} mm (theory {r.get('theoretical_reach_mm', '?')}, K {r.get('K_ground', '?')})")
    lines.append(f"  ring     : {'found' if r['ring_found'] else 'not found'}; bridged {r['ring_bridged_px']} px")
    lines.append(f"  noise    : background fit rms {r['background_rms']} (Lab units; >3 means an uneven/dirty board)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="lab_out/manifest.csv")
    ap.add_argument("--out", default="lab_out")
    ap.add_argument("--settings")
    ap.add_argument("--preview", type=int, default=0, help="only process N photos spread across sessions and print them")
    ap.add_argument("--debug-save-dir", help="save diagnostic overlays here")
    ap.add_argument("--ring", help="override ring: auto | none | x,y,rx,ry (fractions)")
    a = ap.parse_args(argv)
    st = S.load(a.settings)
    if a.ring:
        st["ring"] = a.ring if a.ring in ("auto", "none") else [float(v) for v in a.ring.split(",")]
    man = pd.read_csv(a.manifest)
    if a.preview:
        man = man.iloc[np.linspace(0, len(man) - 1, min(a.preview, len(man))).astype(int)]
        print(f"Previewing {len(man)} of {len(pd.read_csv(a.manifest))} images (spread):\n")
    dbg_dir = Path(a.debug_save_dir) if a.debug_save_dir else None
    if dbg_dir:
        dbg_dir.mkdir(parents=True, exist_ok=True)
    rows, labels, profiles = [], {}, {}
    full = pd.read_csv(a.manifest)
    rings = {}
    for sess, g in man.groupby("session"):
        all_paths = full.loc[full["session"] == sess, "image_path"].tolist()
        rings[sess] = session_ring(all_paths, st, g["camera_angle"].iloc[0])
        print(f"ring for {sess}: {'found' if rings[sess] else 'per photo / none'}")
    for i, (_, meta) in enumerate(man.iterrows(), 1):
        meta = meta.to_dict()
        try:
            r, extra = process_image(meta["image_path"], meta, st, fixed_ring=rings.get(meta["session"]))
        except Exception as e:                  # one bad photo must not stop the batch
            r, extra = {"status": "REJECT", "reasons": f"error:{type(e).__name__}:{e}"}, None
        prof = r.pop("_profile", None); r.pop("_f", None); r.pop("_look", None)
        if prof is not None:
            profiles[meta["image_path"]] = prof
        rows.append({**meta, **r})
        if extra:
            labels[meta["image_path"]] = extra[0]
            if dbg_dir:
                cv2.imwrite(str(dbg_dir / f"{Path(str(meta['session'])).name}_{Path(meta['image_path']).stem}.jpg".replace(" ", "_")),
                            draw_debug(extra[1], meta))
        if a.preview:
            print(preview_text(r, meta) + "\n")
        elif i % 25 == 0:
            print(f"  {i}/{len(man)} photos")
    rows = apply_session_direction(rows, profiles, st)
    df = pd.DataFrame(rows)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    name = "vessel_shadow_metrics_preview.csv" if a.preview else "vessel_shadow_metrics.csv"
    df.to_csv(out / name, index=False)
    print(df["status"].value_counts().to_string())
    print(f"Wrote {out / name}")
    if not a.preview:
        yaml_path, n = write_dataset(rows, labels, out)
        print(f"YOLO dataset: {n['train']} train / {n['val']} val -> {yaml_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
