"""
synthetic.py  -  Fake turntable photos with a KNOWN answer, for testing the pipeline.

Draws a Moss-type LNG carrier (hull + spherical tanks) on a turntable disc on a board,
lit by a lamp at a given elevation, seen by a camera at a given elevation (lengths
toward/away from the camera are foreshortened). The turntable's raised ring lip
catches light and BREAKS the shadow where it crosses - the artefact seen in real photos.

    from lab.synthetic import render
    img, truth = render(sun_elev=20, cam_elev=30, rotation=40)
"""
import math
import numpy as np
import cv2


def render(sun_elev=25.0, cam_elev=35.0, rotation=0.0, lamp_dir_deg=135.0, size=(1600, 1200),
           px_per_mm=2.0, hull_mm=(410.0, 70.0), tank_height_mm=60.0, ring=True, ring_r_frac=0.30,
           noise=3.0, gradient=18.0, seed=0):
    """Returns (BGR image, truth dict).

    lamp_dir_deg: direction the SHADOW points on the board, degrees clockwise from image 'up'
    rotation:     ship heading on the turntable, degrees clockwise from image 'up'
    """
    rng = np.random.default_rng(seed)
    w, h = size
    cx, cy = w / 2, h * 0.55
    f = math.sin(math.radians(cam_elev))          # foreshortening along the camera's look direction

    def to_img(pts_mm):
        """Ground (x right, y away from camera) in mm, relative to turntable centre -> image px."""
        p = np.asarray(pts_mm, dtype=float)
        return np.stack([cx + p[:, 0] * px_per_mm, cy - p[:, 1] * px_per_mm * f], axis=1)

    # ---- board: uneven light grey with a gradient and sensor noise
    yy, xx = np.mgrid[0:h, 0:w]
    board = 205 - gradient * (xx / w) - gradient * 0.5 * (yy / h)
    img = np.repeat(board[..., None], 3, axis=2).astype(np.float32)
    img[..., 0] -= 4          # slight warm tint
    # ---- turntable disc (slightly darker) and ring lip
    R_img = ring_r_frac * w
    disc = (((xx - cx) / R_img) ** 2 + ((yy - cy) / (R_img * f)) ** 2) <= 1
    img[disc] -= 10
    lip = np.zeros((h, w), bool)
    if ring:
        rr = np.sqrt(((xx - cx) / R_img) ** 2 + ((yy - cy) / (R_img * f)) ** 2)
        lip = np.abs(rr - 1) < (7 / R_img)
        img[lip] += 28        # bright lip catches the lamp
        seam = np.abs(rr - 1 - 9 / R_img) < (1.5 / R_img)
        img[seam] -= 35       # dark seam just outside

    # ---- hull outline on the ground (pointed bow, square stern)
    L, B = hull_mm
    hull = np.array([[0, L / 2], [B / 2, L / 2 - B * 0.9], [B / 2, -L / 2], [-B / 2, -L / 2], [-B / 2, L / 2 - B * 0.9]])
    th = math.radians(rotation)
    rot = np.array([[math.cos(th), math.sin(th)], [-math.sin(th), math.cos(th)]])
    hull_g = hull @ rot.T

    # ---- shadow on the ground: tanks of height H cast shadows H/tan(sun) long
    d = tank_height_mm / math.tan(math.radians(sun_elev))
    ld = math.radians(lamp_dir_deg)
    sv = np.array([math.sin(ld), math.cos(ld)])                 # shadow direction on the ground (x right, y away)
    tank_xy = [np.array([0, y]) @ rot.T for y in np.linspace(-L * 0.32, L * 0.30, 4)]
    tank_r = B * 0.42
    shadow_pts = list(hull_g) + list(hull_g + sv * d * 0.35)
    for t in tank_xy:
        for a in np.linspace(0, 2 * math.pi, 24, endpoint=False):
            c = t + tank_r * np.array([math.cos(a), math.sin(a)])
            shadow_pts += [c, c + sv * d]
    shadow_img = to_img(shadow_pts).astype(np.float32)
    smask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(smask, cv2.convexHull(shadow_img).astype(np.int32), 1)
    smask = cv2.GaussianBlur(smask.astype(np.float32), (0, 0), 2.5)       # soft penumbra
    shade = 1 - 0.42 * smask
    shade[lip] = 1 - 0.08 * smask[lip]                                     # ring lip breaks the shadow
    img *= shade[..., None]

    # ---- hull and tanks (drawn last: they sit on top of the shadow)
    hull_img = to_img(hull_g).astype(np.int32)
    cv2.fillPoly(img, [hull_img], (120, 118, 112))
    cv2.polylines(img, [hull_img], True, (70, 70, 70), 2)
    for t in tank_xy:
        c = to_img([t])[0]
        cv2.ellipse(img, (int(c[0]), int(c[1])), (int(tank_r * px_per_mm), int(tank_r * px_per_mm)), 0, 0, 360, (225, 225, 220), -1)
        cv2.ellipse(img, (int(c[0]), int(c[1])), (int(tank_r * px_per_mm), int(tank_r * px_per_mm)), 0, 0, 360, (150, 150, 150), 2)

    img += rng.normal(0, noise, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)

    # exact expected overhang: how far the shadow polygon reaches past the ship (hull + tanks) along the sun line
    vessel_pts = np.vstack([hull_g] + [t + tank_r * np.array([[math.cos(a), math.sin(a)] for a in np.linspace(0, 2 * math.pi, 48)]) for t in tank_xy])
    overhang_mm = float(np.max(np.asarray(shadow_pts) @ sv) - np.max(vessel_pts @ sv))
    sv_img = np.array([sv[0], -sv[1] * f])
    sv_img /= np.linalg.norm(sv_img)
    truth = {
        "sun_elev": sun_elev, "cam_elev": cam_elev, "rotation": rotation,
        "shadow_ground_mm": d, "overhang_mm": overhang_mm, "shadow_dir_img": sv_img.tolist(),
        "shadow_angle_img_deg": (math.degrees(math.atan2(sv_img[0], -sv_img[1])) + 360) % 360,
        "ring": (cx, cy, R_img) if ring else None, "px_per_mm": px_per_mm, "foreshortening": f,
        "hull_mm": hull_mm, "tank_height_mm": tank_height_mm,
    }
    return img, truth


def write_session(root, sun_elev, cam_elev, rotations, date="11-3-26", start_index=7000, **kw):
    """Write fake photos in the same folder/file naming as the real shoot
    (e.g. 11-3-26Sun25Camera35/IMG_7000.JPG) and return manifest rows with the true answers."""
    import cv2
    from pathlib import Path
    folder = Path(root) / f"{date}Sun{int(sun_elev)}Camera{int(cam_elev)}"
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, rot in enumerate(rotations):
        img, truth = render(sun_elev, cam_elev, rot, seed=i + int(sun_elev) * 100 + int(cam_elev), **kw)
        path = folder / f"IMG_{start_index + i:04d}.JPG"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        rows.append({"image_path": str(path), "vessel": "MossTypeLNG", "sun_elevation": sun_elev,
                     "camera_angle": cam_elev, "rotation": rot, "session": folder.name,
                     "index_in_session": i, "true_overhang": truth["overhang_mm"]})
    return rows


if __name__ == "__main__":
    # python -m lab.synthetic OUT_DIR  -> a small 3-sun x 12-rotation fake shoot + manifest.csv
    import sys
    import pandas as pd
    out = sys.argv[1] if len(sys.argv) > 1 else "lab_out/synthetic"
    rows = []
    for sun in (20, 30, 40):
        rows += write_session(out, sun, 30, list(range(0, 360, 30)), ring_r_frac=0.28)
    pd.DataFrame(rows).to_csv(f"{out}/manifest.csv", index=False)
    print(f"{len(rows)} fake photos + {out}/manifest.csv")
