"""
validate.py  -  Module 2: check the lab measurements against physics before we trust them.

Input : lab_out/vessel_shadow_metrics.csv   (from lab.annotate)
Output: lab_out/validated_metrics.csv       every photo + flags + a keep/drop decision
        lab_out/session_summary.csv         one row per session (sun, camera)
        lab_out/height_fit.csv              effective shadow height per vessel and camera angle

The physics we check
--------------------
On a flat board a part of height h throws a shadow that sticks out past the hull by

        reach = h / tan(sun elevation)  =  h * cot(sun)

so, for one vessel and camera angle, reach plotted against cot(sun) must be a straight line
through (about) zero. Its slope is the model's EFFECTIVE SHADOW HEIGHT - the height of
whatever casts the tip of the shadow (the Moss tanks on an LNG carrier). That number, times
the print scale, is what we expect to see from a satellite. The offset should be small; a big
one means the hull/shadow split is biased (e.g. part of the hull counted as shadow).

Checks per photo
    K_ground   reach / theoretical reach. 1.0 = exactly as the settings' shadow_height predicts.
               The settings height is a guess until you measure your print, so K is judged
               against the session median first (outliers) and the fitted height second.
    outlier    |K - session median| > 3.5 robust sigmas (MAD) -> flag "k_outlier"
    status     annotate's REJECT -> dropped; PARTIAL -> kept but flagged
Checks per vessel + camera + rotation (same rotation in different sun sessions)
    trend      longer shadows at lower sun: reach must fall as sun rises -> "trend_break"
    fit        slope (effective height), offset, R^2 of reach vs cot(sun); the vessel + camera
               summary is the median over rotations. Rotation numbers must mean the same hull
               heading in every session (start each 360 deg sweep from the same mark).

Run:  python -m lab.validate --metrics lab_out/vessel_shadow_metrics.csv --out lab_out
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from lab import settings as S

MAD_LIMIT = 3.5        # robust z-score above which a photo is an outlier in its session
MIN_FIT_SUNS = 2       # sun angles needed before a height fit means anything


def robust_z(x: pd.Series) -> pd.Series:
    """(x - median) / (1.4826 * MAD). Ignores NaN. All zeros when the spread is zero."""
    med = x.median()
    mad = (x - med).abs().median() * 1.4826
    if not mad or mad != mad:
        return pd.Series(0.0, index=x.index)
    return (x - med) / mad


def fit_height(cot_sun, reach_mm):
    """Least-squares reach = slope * cot(sun) + offset. Returns slope, offset, r2, n."""
    x, y = np.asarray(cot_sun, float), np.asarray(reach_mm, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 2 or np.ptp(x) == 0:
        return None, None, None, len(x)
    slope, offset = np.polyfit(x, y, 1)
    pred = slope * x + offset
    ss = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - pred) ** 2).sum() / ss if ss > 0 else None
    return float(slope), float(offset), (float(r2) if r2 is not None else None), len(x)


def validate(df: pd.DataFrame, settings: dict):
    df = df.copy()
    df["flags"] = ""

    def flag(mask, name):
        df.loc[mask, "flags"] = df.loc[mask, "flags"].where(df.loc[mask, "flags"] == "",
                                                            df.loc[mask, "flags"] + ";") + name

    usable = df["status"].isin(["OK", "PARTIAL"]) & df["reach_ground_mm"].notna()
    flag(df["status"] == "PARTIAL", "partial")
    flag(~usable & (df["status"] != "REJECT"), "no_reach")

    # per-session outliers (lamp, camera and print fixed, only rotation changes)
    df["k_robust_z"] = np.nan
    for _, idx in df[usable].groupby("session").groups.items():
        df.loc[idx, "k_robust_z"] = robust_z(df.loc[idx, "K_ground"]).round(2)
    flag(df["k_robust_z"].abs() > MAD_LIMIT, "k_outlier")

    df["cot_sun"] = 1 / np.tan(np.radians(df["sun_elevation"].astype(float)))
    # The overhang depends on how the hull is turned (a shadow running along the hull hides
    # behind it), so compare like with like: fit reach vs cot(sun) for each ROTATION, then
    # summarise per vessel + camera with the median over rotations.
    good = df[usable & ~(df["k_robust_z"].abs() > MAD_LIMIT)]
    rot_fits, trend_breaks = [], set()
    for (vessel, cam, rot), g in good.groupby(["vessel", "camera_angle", "rotation"]):
        by_sun = g.groupby("sun_elevation")["reach_ground_mm"].median().sort_index()
        # trend: each step up in sun must shorten the shadow (2 mm slack for noise)
        for (s1, r1), (s2, r2) in zip(by_sun.items(), list(by_sun.items())[1:]):
            if r2 > r1 + 2:
                trend_breaks.add((vessel, cam, rot, s2))
        slope, offset, r2, n = fit_height(g["cot_sun"], g["reach_ground_mm"])
        enough = len(by_sun) >= MIN_FIT_SUNS
        rot_fits.append({"vessel": vessel, "camera_angle": cam, "rotation": rot, "sun_angles": len(by_sun),
                         "slope": slope if enough else None, "offset": offset if enough else None,
                         "r2": r2 if enough else None,
                         "h_one": float((g["reach_ground_mm"] / g["cot_sun"]).median())})
    rf = pd.DataFrame(rot_fits)
    fits = []
    if len(rf):
        for (vessel, cam), g in rf.groupby(["vessel", "camera_angle"]):
            v = S.vessel(settings, vessel)
            sl = g["slope"].dropna()
            h = float(sl.median()) if len(sl) else None
            fits.append({
                "vessel": vessel, "camera_angle": cam, "rotations_fitted": len(sl),
                "sun_angles": int(g["sun_angles"].max()),
                "effective_height_mm": round(h, 1) if h is not None else None,
                "offset_mm": round(float(g["offset"].dropna().median()), 1) if len(sl) else None,
                "r2_median": round(float(g["r2"].dropna().median()), 3) if g["r2"].notna().any() else None,
                # with one sun angle: still give a height, straight from h = reach * tan(sun)
                "height_one_sun_mm": round(float(g["h_one"].median()), 1),
                "settings_height_mm": v.get("shadow_height_mm"),
                "effective_height_real_m": round(h * v["scale_denominator"] / 1000, 1)
                if h is not None and v.get("scale_denominator") else None,
            })
    for vessel, cam, rot, sun in trend_breaks:
        flag((df["vessel"] == vessel) & (df["camera_angle"] == cam) & (df["rotation"] == rot)
             & (df["sun_elevation"] == sun), "trend_break")

    df["keep"] = usable & ~df["flags"].str.contains("k_outlier")
    summary = (df.groupby("session").agg(
        vessel=("vessel", "first"), sun_elevation=("sun_elevation", "first"), camera_angle=("camera_angle", "first"),
        photos=("image_path", "size"), kept=("keep", "sum"),
        rejected=("status", lambda s: int((s == "REJECT").sum())),
        reach_median_mm=("reach_ground_mm", "median"),
        reach_spread_mm=("reach_ground_mm", lambda s: s.quantile(.9) - s.quantile(.1)),
        K_median=("K_ground", "median"), K_mad=("K_ground", lambda s: (s - s.median()).abs().median()),
        lamp_dir_deg=("session_shadow_dir_deg", "first") if "session_shadow_dir_deg" in df else ("shadow_dir_deg", "median"),
        px_per_mm=("px_per_mm_ground", "median"),
    ).reset_index().round(3))
    rotations = df.groupby("session")["rotation"].nunique()
    summary["rotations"] = summary["session"].map(rotations)
    return df, summary, pd.DataFrame(fits)


def report(df, summary, fits) -> str:
    lines = [f"{len(df)} photos, {int(df['keep'].sum())} kept, {int((df['status'] == 'REJECT').sum())} rejected",
             "", "Per session:"]
    for _, r in summary.iterrows():
        lines.append(f"  {r['session']:<26} sun {r['sun_elevation']:>4} cam {r['camera_angle']:>4}  "
                     f"{int(r['kept'])}/{int(r['photos'])} kept  reach {r['reach_median_mm']:.1f} mm  K {r['K_median']:.2f}")
    lines += ["", "Effective shadow height (slope of reach vs cot(sun)):"]
    for _, f in fits.iterrows():
        h = f["effective_height_mm"]
        lines.append(f"  {f['vessel']} cam {f['camera_angle']}: " +
                     (f"{h} mm (offset {f['offset_mm']} mm, median R^2 {f['r2_median']}, "
                       f"{f['rotations_fitted']} rotations) = {f['effective_height_real_m']} m real"
                      if h is not None and h == h else
                      f"one sun angle only -> {f['height_one_sun_mm']} mm (shoot more sun angles to fit)"))
    flagged = df[df["flags"] != ""]
    if len(flagged):
        lines += ["", f"Flagged photos ({len(flagged)}):"]
        lines += [f"  {Path(str(r['image_path'])).name:<14} {r['session']:<26} rot {r['rotation']:>5}  {r['flags']}"
                  for _, r in flagged.head(20).iterrows()]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check lab shadow measurements against physics")
    ap.add_argument("--metrics", default="lab_out/vessel_shadow_metrics.csv")
    ap.add_argument("--out", default="lab_out")
    ap.add_argument("--settings", default=None)
    a = ap.parse_args(argv)
    df, summary, fits = validate(pd.read_csv(a.metrics), S.load(a.settings))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "validated_metrics.csv", index=False)
    summary.to_csv(out / "session_summary.csv", index=False)
    fits.to_csv(out / "height_fit.csv", index=False)
    print(report(df, summary, fits))
    print(f"\nWrote {out / 'validated_metrics.csv'}, session_summary.csv, height_fit.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
