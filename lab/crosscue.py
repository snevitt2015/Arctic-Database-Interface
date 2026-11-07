"""
crosscue.py  -  Test the SAR <-> optical cross-cue with the lab reference.

Two tests, both written to lab_out/:

A. LAB SELF-TEST  (crosscue_selftest.csv)
   Every 5th turntable position is held back. The reference is built from the rest, then
   each held-back photo is blown up to real size (x scale) and treated as if a satellite had
   seen it, with satellite-sized measurement noise. Each one is scored against:
       right ship        the true type and length                   -> should score HIGH
       wrong length      same type but 20 % shorter / longer         -> should score LOWER
       low-profile ship  same length, half the height
                         (illustrative stand-in for a tanker without Moss tanks)
   The hull length is "measured" too (with noise), like a real detection.
   height_told = the shadow alone ranks the right ship above the half-height one
   hit         = shadow + length together rank the right ship above all others
   This tells you at which sun angles and image resolutions the shadow is worth using.

B. SCENE CROSS-CUE  (crosscue_scenes.csv)
   For every ping (AIS off / spoofing / silent ...) in data/output_cues.csv, the SAR and
   optical images found by the daily run (data/output_scenes.csv) are paired up, and for each
   one we write what the lab says the ship SHOULD look like:
       optical  sun elevation at the image -> expected shadow past the hull (m), and the lab
                session (SunXXCameraYY) to compare it with, and whether you've shot it yet
       SAR      incidence angle (mid-swath for the mode) -> expected layover and radar shadow
   The lab model is a Moss-type LNG carrier, so only LNG tankers get numbers; other types
   are listed with "no lab model".

Ship lengths: put real lengths in data/vessel_dimensions.csv (columns imo,name,length_m,
beam_m,containment). Without one, LNG tankers are assumed 290 m and marked "assumed".

Run:  python -m lab.crosscue --lab lab_out
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from lab import settings as S
from lab import reference as R

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
ASSUMED_LNG_LENGTH_M = 290.0
# sensor: (shadow measurement error, hull length measurement error), metres
SENSORS = {"Sentinel-2 (10 m)": (10.0, 15.0), "very high-res optical (0.5 m)": (1.0, 2.0)}
SAR_MODE_INCIDENCE = {"IW": (29.0, 46.0), "EW": (19.0, 47.0), "SM": (20.0, 45.0)}
HOLD_OUT_EVERY = 5


# ------------------------------------------------------------------ A. self-test
def self_test(ref: pd.DataFrame, settings: dict, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for vessel, g in ref.groupby("vessel"):
        v = S.vessel(settings, vessel)
        L_real = v["model_length_mm"] * v["scale_denominator"] / 1000
        rots = sorted(g["rotation"].unique())
        held = set(rots[HOLD_OUT_EVERY - 1::HOLD_OUT_EVERY]) or {rots[-1]}
        train, test = g[~g["rotation"].isin(held)], g[g["rotation"].isin(held)]
        if len(train) < 5 or not len(test):
            continue
        for _, t in test.iterrows():
            true_shadow = t["reach_mm"] * v["scale_denominator"] / 1000
            for sensor, (err, len_err) in SENSORS.items():
                seen = max(0.0, true_shadow + rng.normal(0, err))
                seen_len = L_real + rng.normal(0, len_err)       # the hull length is measured too
                hyps = {"right ship": (L_real, 1.0), "20% shorter": (0.8 * L_real, 1.0),
                        "20% longer": (1.2 * L_real, 1.0), "low-profile ship": (L_real, 0.5)}
                rec = {"vessel": vessel, "session": t["session"], "image": t["image"],
                       "sun_elevation": t["sun_elevation"], "camera_angle": t["camera_angle"],
                       "rotation": t["rotation"], "sensor": sensor, "shadow_true_m": round(true_shadow, 1),
                       "shadow_seen_m": round(seen, 1), "length_seen_m": round(seen_len, 1)}
                best_shadow, best_both = {}, {}
                for name, (Lh, hs) in hyps.items():
                    # score with the length the hypothesis says (shadow scales with ship size)
                    ps = R.shadow_score(train, vessel, Lh, seen, t["sun_elevation"], err, height_scale=hs,
                                        shadow_vs_hull_deg=t.get("shadow_vs_hull_deg"))["p_shadow"] or 0.0
                    pl = math.exp(-0.5 * ((seen_len - Lh) / len_err) ** 2)
                    key = name.replace(" ", "_").replace("%", "pct")
                    rec[f"p_shadow_{key}"] = round(ps, 3)
                    rec[f"p_both_{key}"] = round(ps * pl, 3)
                    best_shadow[name], best_both[name] = ps, ps * pl
                # what the SHADOW adds: right ship vs a lower ship of the same length
                rec["shadow_tells_height"] = best_shadow["right ship"] > best_shadow["low-profile ship"]
                # shadow + measured length together: right ship beats every other hypothesis
                rec["hit"] = best_both["right ship"] >= max(best_both.values()) and best_both["right ship"] > 0
                rows.append(rec)
    return pd.DataFrame(rows)


def self_test_report(st: pd.DataFrame) -> str:
    if st.empty:
        return "Self-test: not enough photos (need 5+ turntable positions in a session)."
    lines = ["Self-test (held-back turntable positions scored as if seen by a satellite):"]
    tab = st.groupby(["sensor", "sun_elevation"]).agg(
        photos=("hit", "size"),
        height_told=("shadow_tells_height", "mean"),
        p_right=("p_shadow_right_ship", "median"), p_low=("p_shadow_low-profile_ship", "median"),
        hit_rate=("hit", "mean")).round(2)
    lines.append(tab.to_string())
    lines.append("height_told = share where the shadow scored the right ship above a half-height ship of the same length\n"
                 "p_right / p_low = median shadow scores for those two\n"
                 "hit_rate = share where shadow + measured length picked the right ship over all hypotheses\n"
                 "           (right ship, 20% shorter, 20% longer, half height)")
    return "\n".join(lines)


# ------------------------------------------------------------------ B. scene cross-cue
def ship_lengths() -> pd.DataFrame:
    f = DATA / "vessel_dimensions.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["imo", "name", "length_m"])


def _length_for(vessel_row, dims):
    if vessel_row is not None and len(dims):
        m = dims[(dims["imo"].astype(str) == str(vessel_row.get("imo"))) |
                 (dims["name"].astype(str).str.lower() == str(vessel_row.get("name")).lower())]
        if len(m) and pd.notna(m.iloc[0].get("length_m")):
            return float(m.iloc[0]["length_m"]), "vessel_dimensions.csv"
    return ASSUMED_LNG_LENGTH_M, "assumed"


def scene_crosscue(ref, settings, cues, scenes, vessels, shot_sessions) -> pd.DataFrame:
    dims = ship_lengths()
    lab_vessel = settings["default_vessel"]
    shot = {(round(s), round(c)) for s, c in shot_sessions}
    vtab = vessels.copy()
    vtab["imo_s"] = vtab["imo"].map(lambda x: str(int(x)) if pd.notna(x) else "")
    scenes = scenes.copy()
    scenes["t"] = pd.to_datetime(scenes["start_utc"], utc=True, format="mixed")
    rows = []
    for _, c in cues.iterrows():
        sc = scenes[scenes["cue_id"] == c["cue_id"]]
        if not len(sc):
            continue
        imo = str(int(c["imo"])) if pd.notna(c.get("imo")) else ""
        vr = vtab[(vtab["imo_s"] == imo) & (imo != "")] if imo else vtab[vtab["name"].str.lower() == str(c["vessel"]).lower()]
        vrow = vr.iloc[0].to_dict() if len(vr) else None
        vtype = (vrow or {}).get("type", "")
        is_lng = "lng" in str(vtype).lower()
        length, length_src = _length_for(vrow, dims) if is_lng else (None, "")
        sar, opt = sc[sc["kind"] == "SAR"], sc[sc["kind"] == "Optical"]
        # best cross-cue pair: SAR and optical closest in time (prefer images covering the ping centre)
        pair_gap = None
        if len(sar) and len(opt):
            a = sar.sort_values("covers_cue_centre", ascending=False)
            b = opt.sort_values("covers_cue_centre", ascending=False)
            gaps = np.abs(a["t"].values[:, None] - b["t"].values[None, :]).astype("timedelta64[m]").astype(float) / 60
            i, j = np.unravel_index(np.argmin(gaps), gaps.shape)
            pair_gap = round(float(gaps[i, j]), 1)
            pair = {a.index[i], b.index[j]}
        else:
            pair = set()
        for idx, s in sc.iterrows():
            row = {"cue_id": c["cue_id"], "ping": c["kind"], "vessel": c["vessel"], "type": vtype,
                   "image": s["kind"], "satellite": s["satellite"], "time_utc": s["start_utc"],
                   "covers_ping_centre": s["covers_cue_centre"], "best_crosscue_pair": idx in pair,
                   "sar_optical_gap_h": pair_gap if idx in pair else None,
                   "length_m": length, "length_source": length_src, "lab_note": ""}
            if s["kind"] == "Optical":
                sun = s.get("sun_elevation")
                row["sun_elevation"] = sun
                row["cloud_cover"] = s.get("cloud_cover")
                if pd.notna(sun):
                    lab_sun = 5 * round(float(sun) / 5)
                    row["lab_sun_session"] = lab_sun
                    row["lab_session_shot"] = any(k[0] == lab_sun for k in shot)
                    if sun <= 0:
                        row["lab_note"] = "sun below horizon (polar night) - optical useless, rely on SAR"
                    elif is_lng:
                        e = R.expected_shadow(ref, lab_vessel, length, float(sun))
                        row.update(expected_height_m=e.get("height_m"), expected_shadow_m=e.get("shadow_overhang_m"),
                                   shadow_sigma_m=e.get("shadow_sigma_m"))
                        if not e.get("in_lab_sun_range"):
                            row["lab_note"] = "sun angle outside what's been shot - extrapolated"
            else:
                lo, hi = SAR_MODE_INCIDENCE.get(str(s.get("mode")), (29.0, 46.0))
                inc = (lo + hi) / 2
                row["incidence_deg_mid"] = inc
                row["lab_camera_equiv"] = R.camera_for_incidence(inc, settings)
                if is_lng:
                    e = R.sar_expectation(ref, lab_vessel, length, inc)
                    row.update(expected_height_m=e.get("height_m"), expected_layover_m=e.get("layover_m"),
                               expected_radar_shadow_m=e.get("radar_shadow_m"))
                    row["lab_note"] = (f"incidence varies {lo:.0f}-{hi:.0f} across the swath; "
                                       f"exact value comes with the SAR crop")
            if not is_lng:
                row["lab_note"] = "no lab model for this type yet (lab reference = Moss LNG)"
            rows.append(row)
    return pd.DataFrame(rows)


def scene_report(sc: pd.DataFrame) -> str:
    if sc.empty:
        return "Scene cross-cue: no pings with images yet (the daily run fills data/output_scenes.csv)."
    lines = ["Scene cross-cue (per ping: images found, best SAR+optical pair, what the lab predicts):"]
    for (cue, vessel, ping, vtype), g in sc.groupby(["cue_id", "vessel", "ping", "type"], sort=False):
        nsar, nopt = (g["image"] == "SAR").sum(), (g["image"] == "Optical").sum()
        gap = g["sar_optical_gap_h"].dropna()
        line = f"  {vessel:<20} {ping:<9} {nsar:>3} SAR {nopt:>3} optical"
        line += f"  best pair {gap.iloc[0]:.1f} h apart" if len(gap) else "  no SAR+optical pair"
        best = g[g["best_crosscue_pair"]]
        o = best[best["image"] == "Optical"]
        s = best[best["image"] == "SAR"]
        if len(o) and pd.notna(o.iloc[0].get("expected_shadow_m", np.nan)):
            r = o.iloc[0]
            line += (f"\n      optical: sun {r['sun_elevation']:.1f} deg -> shadow ~{r['expected_shadow_m']:.0f} "
                     f"+- {r['shadow_sigma_m']:.0f} m past hull; compare lab Sun{int(r['lab_sun_session'])} "
                     f"({'shot' if r['lab_session_shot'] else 'NOT SHOT YET'})")
        if len(s) and pd.notna(s.iloc[0].get("expected_layover_m", np.nan)):
            r = s.iloc[0]
            line += (f"\n      SAR    : ~{r['incidence_deg_mid']:.0f} deg incidence -> layover ~{r['expected_layover_m']:.0f} m,"
                     f" radar shadow ~{r['expected_radar_shadow_m']:.0f} m (lab Camera{int(r['lab_camera_equiv'])} view)")
        if not ("lng" in str(vtype).lower()):
            line += "\n      (no lab model for this type yet)"
        lines.append(line)
    if (sc["length_source"] == "assumed").any():
        lines.append("  Lengths marked 'assumed' (290 m) - add real ones in data/vessel_dimensions.csv.")
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Test the SAR <-> optical cross-cue with the lab reference")
    ap.add_argument("--lab", default="lab_out", help="folder with optical_reference.csv (from lab.reference build)")
    ap.add_argument("--settings", default=None)
    a = ap.parse_args(argv)
    st = S.load(a.settings)
    lab = Path(a.lab)
    ref_path = lab / "optical_reference.csv"
    if not ref_path.exists():
        print(f"No {ref_path} - run lab.reference build first (or lab.run, which does everything).")
        return 1
    ref = R.load(ref_path)

    selft = self_test(ref, st)
    selft.to_csv(lab / "crosscue_selftest.csv", index=False)
    print(self_test_report(selft))

    cues, scenes, vessels = (DATA / "output_cues.csv", DATA / "output_scenes.csv", DATA / "output_vessels.csv")
    if cues.exists() and scenes.exists() and vessels.exists():
        shot = ref[["sun_elevation", "camera_angle"]].drop_duplicates().itertuples(index=False)
        sc = scene_crosscue(ref, st, pd.read_csv(cues), pd.read_csv(scenes), pd.read_csv(vessels), list(shot))
        sc.to_csv(lab / "crosscue_scenes.csv", index=False)
        print("\n" + scene_report(sc))
    else:
        print("\nScene cross-cue skipped: data/output_cues.csv / output_scenes.csv not found. "
              "Run `git pull` to get the latest daily outputs.")
    print(f"\nWrote {lab / 'crosscue_selftest.csv'}" + (" and crosscue_scenes.csv" if cues.exists() else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
