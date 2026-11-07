"""
reference.py  -  The lab photos turned into a SAR <-> optical reference library.

Why: a satellite sees a ship at one sun elevation, from one viewing angle, at one heading.
The lab shoot covers those same three things (sun 15-50 deg, camera 20-45 deg, 360 deg of
rotation) on a scale model. If we store each lab photo as SCALE-FREE numbers, they apply to
the real ship too, and to any detection in an optical or SAR image:

    reach_over_length   shadow overhang / hull length          (depends on sun, heading)
    height_over_length  effective shadow height / hull length  (one number per model -
                        the Moss tanks make it high for an LNG carrier)
    hull_aspect         hull length / beam on the board
    shadow_aspect       shadow box length / width
    shadow_vs_hull_deg  angle between the shadow direction and the hull axis (0-90)

Files
    lab_out/optical_reference.csv    one row per kept photo (vessel, sun, camera, rotation)
    lab_out/coverage.csv             which sun x camera combinations are shot / missing

Use from Python
    from lab import reference as R
    ref = R.load("lab_out/optical_reference.csv")
    R.expected_shadow(ref, "MossTypeLNG", ship_length_m=290, sun_elev=18)        # optical
    R.shadow_score(ref, "MossTypeLNG", length_m=288, shadow_m=150, sun_elev=18,      # 0-1 likelihood
                   shadow_vs_hull_deg=R.relative_angle(ship_heading, sun_azimuth))
      (shadow_m = how far the shadow sticks out past the hull, measured the same way as the lab)
    R.sar_expectation(ref, "MossTypeLNG", ship_length_m=290, incidence_deg=38)   # SAR

SAR has no sun shadow. What carries over is the HEIGHT: tall tanks/superstructure give a
bright layover toward the satellite and a radar shadow away from it, both set by height and
incidence angle. The lab camera off-nadir angle (90 - camera elevation) plays the part of the
SAR incidence angle when comparing how much of the side of the ship is visible.

Run:   python -m lab.reference build  --validated lab_out/validated_metrics.csv \
                                      --heights lab_out/height_fit.csv --out lab_out
       python -m lab.reference lookup --vessel MossTypeLNG --length 290 --sun 18 [--incidence 38]
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from lab import settings as S

SUN_PLAN = list(range(15, 55, 5))        # the planned shoot: sun 15/20-50 deg ...
CAMERA_PLAN = list(range(20, 50, 5))     # ... camera 20-45 deg, 5 deg steps
SENTINEL1_IW = (29.0, 46.0)              # Sentinel-1 IW incidence range, deg


# ------------------------------------------------------------------ build
def build(validated: pd.DataFrame, heights: pd.DataFrame = None, settings: dict = None) -> pd.DataFrame:
    settings = settings or S.load()
    df = validated[validated["keep"].astype(bool)].copy() if "keep" in validated else validated.copy()
    rows = []
    for _, r in df.iterrows():
        v = S.vessel(settings, r["vessel"])
        L, B = v["model_length_mm"], v["model_beam_mm"]
        reach = r.get("reach_ground_mm")
        sun = float(r["sun_elevation"])
        # angle between the lamp's shadow direction and the hull axis, on the board (0 = shadow
        # runs along the hull, 90 = straight out to the side). Satellites give the same angle from
        # the sun azimuth and the ship's heading - see relative_angle().
        lamp = r.get("session_shadow_dir_deg", np.nan)
        lamp = r.get("shadow_dir_deg", np.nan) if lamp != lamp else lamp
        ang = abs(((lamp - r.get("hull_axis_ground_deg", np.nan)) + 90) % 180 - 90)
        rows.append({
            "vessel": r["vessel"], "sun_elevation": sun, "camera_angle": r["camera_angle"],
            "off_nadir": S.off_nadir(settings, r["camera_angle"]), "rotation": r["rotation"],
            "session": r["session"], "image": Path(str(r["image_path"])).name,
            "reach_mm": reach,
            "reach_over_length": round(reach / L, 4) if reach == reach and reach is not None else None,
            "height_mm": round(reach * math.tan(math.radians(sun)), 1) if reach == reach and reach is not None else None,
            "height_over_length": round(reach * math.tan(math.radians(sun)) / L, 4) if reach == reach and reach is not None else None,
            "hull_aspect": round(L / B, 2),
            "shadow_aspect": r.get("shadow_aspect_ratio"),
            "shadow_vs_hull_deg": round(float(ang), 1) if ang == ang else None,
            "K_ground": r.get("K_ground"),
            "scale_denominator": v["scale_denominator"],
        })
    ref = pd.DataFrame(rows)
    if heights is not None and len(heights) and len(ref):
        hf = heights.set_index(["vessel", "camera_angle"])
        for col, src in (("fitted_height_mm", "effective_height_mm"), ("fitted_offset_mm", "offset_mm")):
            ref[col] = [hf[src].get((a, b)) for a, b in zip(ref["vessel"], ref["camera_angle"])]
    return ref


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """Sun x camera grid: photos shot per combination (0 = still to shoot)."""
    got = df.groupby(["sun_elevation", "camera_angle"]).size() if len(df) else pd.Series(dtype=int)
    rows = [{"sun_elevation": s, "camera_angle": c, "photos": int(got.get((s, c), 0))}
            for s in SUN_PLAN for c in CAMERA_PLAN]
    return pd.DataFrame(rows)


def load(path="lab_out/optical_reference.csv") -> pd.DataFrame:
    return pd.read_csv(path)


# ------------------------------------------------------------------ lookups
def cot_of(df):
    return 1 / np.tan(np.radians(df["sun_elevation"].astype(float)))


def relative_angle(ship_heading_deg, sun_azimuth_deg):
    """Satellite version of the lab's shadow_vs_hull_deg: the angle (0-90) between the ship's
    axis and the direction its shadow falls (opposite the sun)."""
    shadow_az = (sun_azimuth_deg + 180) % 360
    return abs(((shadow_az - ship_heading_deg) + 90) % 180 - 90)


def vessel_geometry(ref: pd.DataFrame, vessel: str, off_nadir: float = None, settings: dict = None,
                    shadow_vs_hull_deg: float = None) -> dict:
    """Scale-free shadow model for one ship type, from the lab:
        overhang / length = height_ratio * cot(sun) + offset_ratio      (+- spread_ratio)
    height_ratio is the physical effective height / length (slope of Module 2's fit when there
    are 2+ sun angles, else from single photos). offset_ratio is the part of the shadow hidden
    under the hull itself (negative). spread_ratio is how much single photos scatter around the
    line (mostly heading). With off_nadir, prefers the closest lab camera angle."""
    settings = settings or S.load()
    r = ref[(ref["vessel"] == vessel) & ref["reach_mm"].notna()]
    if not len(r):
        return {"available": False}
    if off_nadir is not None:
        nearest = r.loc[(r["off_nadir"] - off_nadir).abs() == (r["off_nadir"] - off_nadir).abs().min()]
        if len(nearest) >= 5:
            r = nearest
    L = S.vessel(settings, vessel)["model_length_mm"]
    full = r
    fitted = r["fitted_height_mm"].dropna() if "fitted_height_mm" in r else pd.Series(dtype=float)
    if len(fitted):
        h, off, source = float(fitted.median()), float(r["fitted_offset_mm"].dropna().median()), "fit"
    else:
        h, off, source = float(r["height_mm"].median()), 0.0, "photos"
    resid = (full["reach_mm"] - (h * cot_of(full) + off)) / L
    correction, used = 0.0, full
    if shadow_vs_hull_deg is not None and "shadow_vs_hull_deg" in full:
        # the shadow sticks out further when it falls across the hull than along it: use the lab
        # photos with the same sun-vs-hull angle (+-15 deg) to correct the line and tighten the spread
        near = (full["shadow_vs_hull_deg"] - abs(shadow_vs_hull_deg)).abs() <= 15
        if near.sum() >= 4:
            used = full[near]
            correction = float(resid[near].median())
            resid = resid[near]
    spread = float((resid - resid.median()).abs().median() * 1.4826) or 0.02
    return {"available": True, "height_ratio": h / L, "offset_ratio": off / L + correction, "spread_ratio": spread,
            "source": source + (" + angle" if used is not full else ""), "photos": len(used),
            "sun_range": (float(r["sun_elevation"].min()), float(r["sun_elevation"].max())),
            "off_nadir_range": (float(r["off_nadir"].min()), float(r["off_nadir"].max()))}


def expected_shadow(ref, vessel, ship_length_m, sun_elev, off_nadir=None, height_scale=1.0,
                    shadow_vs_hull_deg=None) -> dict:
    """What an optical satellite should see for this ship type: effective height, and how far
    the shadow sticks out past the hull (metres) at this sun elevation.
    height_scale < 1 models a lower ship of the same length (e.g. 0.5 = half as tall)."""
    g = vessel_geometry(ref, vessel, off_nadir, shadow_vs_hull_deg=shadow_vs_hull_deg)
    if not g["available"]:
        return g
    cot = 1 / math.tan(math.radians(sun_elev))
    hr = g["height_ratio"] * height_scale
    over = max(0.0, (hr * cot + g["offset_ratio"]) * ship_length_m)
    return {"available": True, "height_m": round(hr * ship_length_m, 1),
            "shadow_overhang_m": round(over, 1), "shadow_sigma_m": round(g["spread_ratio"] * ship_length_m, 1),
            "model_source": g["source"],
            "in_lab_sun_range": bool(g["sun_range"][0] <= sun_elev <= g["sun_range"][1]),
            "in_lab_view_range": None if off_nadir is None else
            bool(g["off_nadir_range"][0] - 5 <= off_nadir <= g["off_nadir_range"][1] + 5)}


def shadow_score(ref, vessel, length_m, shadow_m, sun_elev, shadow_err_m=10.0, off_nadir=None,
                 height_scale=1.0, shadow_vs_hull_deg=None) -> dict:
    """0-1 likelihood that a detection with this length and shadow overhang (past the hull, m)
    is this ship type. Same bell shape as tracker.match: 1 = perfect, ~0.6 at one sigma,
    ~0.1 at two. Multiply it into match.score_candidate's score as p_shadow."""
    if not sun_elev or sun_elev <= 0:
        return {"p_shadow": None, "reason": "sun below horizon"}
    e = expected_shadow(ref, vessel, length_m, sun_elev, off_nadir, height_scale, shadow_vs_hull_deg)
    if not e["available"]:
        return {"p_shadow": None, "reason": "no lab reference for this vessel"}
    # a 0.5 deg error in the sun elevation also moves the expected shadow
    d_sun = e["height_m"] / math.sin(math.radians(sun_elev)) ** 2 * math.radians(0.5)
    sigma = math.sqrt(shadow_err_m ** 2 + e["shadow_sigma_m"] ** 2 + d_sun ** 2)
    p = math.exp(-0.5 * ((shadow_m - e["shadow_overhang_m"]) / sigma) ** 2)
    return {"p_shadow": round(p, 3), "shadow_expected_m": e["shadow_overhang_m"], "sigma_m": round(sigma, 1),
            "height_measured_m": round(height_from(shadow_m, sun_elev), 1), "height_expected_m": e["height_m"]}


def height_from(shadow_m, sun_elev):
    return shadow_m * math.tan(math.radians(sun_elev))


def sar_expectation(ref, vessel, ship_length_m, incidence_deg) -> dict:
    """What the ship's height does in a SAR image (ground-range metres):
       layover  - tops of the tanks appear shifted TOWARD the satellite by h / tan(incidence)
       radar shadow - dark area AWAY from the satellite, h * tan(incidence) long (often hidden by
                      sea clutter; clearer in calm water / ice)
    plus which lab camera angle is the closest viewing geometry."""
    g = vessel_geometry(ref, vessel)
    if not g["available"]:
        return g
    h = g["height_ratio"] * ship_length_m
    th = math.radians(incidence_deg)
    lab = ref[ref["vessel"] == vessel]["off_nadir"].dropna().unique()
    nearest = float(lab[np.argmin(np.abs(lab - incidence_deg))]) if len(lab) else None
    return {"available": True, "height_m": round(h, 1), "layover_m": round(h / math.tan(th), 1),
            "radar_shadow_m": round(h * math.tan(th), 1),
            "nearest_lab_off_nadir": nearest,
            "nearest_lab_camera_elevation": None if nearest is None else 90 - nearest,
            "in_sentinel1_iw": SENTINEL1_IW[0] <= incidence_deg <= SENTINEL1_IW[1]}


def camera_for_incidence(incidence_deg, settings=None) -> float:
    """Lab camera angle (in the folder convention) that matches a SAR incidence / satellite
    off-nadir angle. Sentinel-1 IW (29-46 deg) = camera elevation 44-61 deg: only Camera45 is
    in range today - shoot Camera50-60 sessions to cover it fully."""
    settings = settings or S.load()
    return 90 - incidence_deg if settings["camera_angle_convention"] == "elevation" else incidence_deg


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="SAR <-> optical reference from the lab photos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--validated", default="lab_out/validated_metrics.csv")
    b.add_argument("--heights", default="lab_out/height_fit.csv")
    b.add_argument("--settings", default=None)
    b.add_argument("--out", default="lab_out")
    q = sub.add_parser("lookup")
    q.add_argument("--reference", default="lab_out/optical_reference.csv")
    q.add_argument("--vessel", default="MossTypeLNG")
    q.add_argument("--length", type=float, required=True, help="real ship length, m")
    q.add_argument("--sun", type=float, help="sun elevation at the satellite image, deg")
    q.add_argument("--off-nadir", type=float, default=None)
    q.add_argument("--shadow", type=float, default=None, help="measured shadow overhang, m (gives a score)")
    q.add_argument("--incidence", type=float, default=None, help="SAR incidence angle, deg")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        st = S.load(a.settings)
        val = pd.read_csv(a.validated)
        heights = pd.read_csv(a.heights) if Path(a.heights).exists() else None
        ref = build(val, heights, st)
        cov = coverage(val[val["keep"].astype(bool)] if "keep" in val else val)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        ref.to_csv(out / "optical_reference.csv", index=False)
        cov.to_csv(out / "coverage.csv", index=False)
        grid = cov.pivot(index="sun_elevation", columns="camera_angle", values="photos")
        print(f"{len(ref)} reference photos. Coverage (photos per sun x camera, 0 = still to shoot):")
        print(grid.to_string())
        for vessel in ref["vessel"].unique():
            g = vessel_geometry(ref, vessel, settings=st)
            v = S.vessel(st, vessel)
            print(f"{vessel}: effective height / length = {g['height_ratio']:.3f} "
                  f"(= {g['height_ratio'] * v['model_length_mm']:.1f} mm on the print, from {g['source']}), "
                  f"photo scatter +-{g['spread_ratio']:.3f} of length")
        print(f"Wrote {out / 'optical_reference.csv'} and coverage.csv")
    else:
        ref = load(a.reference)
        if a.sun is not None:
            print("optical:", expected_shadow(ref, a.vessel, a.length, a.sun, a.off_nadir))
            if a.shadow is not None:
                print("score  :", shadow_score(ref, a.vessel, a.length, a.shadow, a.sun, off_nadir=a.off_nadir))
        if a.incidence is not None:
            print("SAR    :", sar_expectation(ref, a.vessel, a.length, a.incidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
