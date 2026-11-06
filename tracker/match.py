"""
match.py  -  Which ship is this image detection? A first, explainable confidence score.

For each candidate ship (from interpolate.candidates_near) we compare:

  position  How far the detection is from where we estimate the ship was at the image
            time, relative to how uncertain that estimate is. Moving ships appear shifted
            in SAR (Doppler) by up to ~1 km, so that is added to the allowed error.
  length    Measured length vs the registry's length overall (if known), relative to the
            measurement error.
  width     Same for beam.

Each part gives a 0-1 likelihood (a bell curve: 1 when it matches perfectly, about 0.6
at one error-width off, about 0.1 at two). The score is their product. A part with no
registry data is skipped and listed in `missing`, so you can see why a score is weak.

This is a STARTING POINT. Once analysts have confirmed matches in the Airtable
"Matches" table, use them to check and tune these numbers (or replace this with a
trained model) - see docs/airtable_fields.md.
"""
import math

from tracker.geo import distance_km

SAR_SHIFT_KM = 1.0          # allowed Doppler shift of a moving ship in SAR
UNMATCHED_SCORE = 0.05      # below this for every candidate -> "possible dark / unknown vessel"


def _bell(diff, err):
    return math.exp(-0.5 * (diff / err) ** 2) if err and err > 0 else 1.0


def score_candidate(det: dict, cand: dict, ship: dict = None, sensor: str = "SAR") -> dict:
    """det: detection (lat, lon, length_m, width_m, *_err_m). cand: position estimate
    (lat, lon, uncertainty_km). ship: registry row (length_m, beam_m) or None."""
    ship = ship or {}
    d_km = distance_km(det["lat"], det["lon"], cand["lat"], cand["lon"])
    pos_err = math.sqrt(cand["uncertainty_km"] ** 2 + (det.get("pos_err_m", 10) / 1000) ** 2 +
                        (SAR_SHIFT_KM ** 2 if sensor == "SAR" else 0))
    parts, missing = {"position": _bell(d_km, pos_err)}, []
    for key, reg_key in [("length", "length_m"), ("width", "beam_m")]:
        reg = ship.get(reg_key)
        if reg is None or reg != reg:          # None or NaN
            missing.append(reg_key)
            continue
        parts[key] = _bell(det[f"{key}_m"] - float(reg), det.get(f"{key}_err_m", 15) + 0.02 * float(reg))
    score = math.prod(parts.values())
    return {"score": round(score, 3), "km_away": round(d_km, 2), "position_error_km": round(pos_err, 2),
            **{f"p_{k}": round(v, 3) for k, v in parts.items()}, "missing": ",".join(missing)}


def rank_candidates(det: dict, candidates, registry_by_imo: dict, sensor="SAR") -> list:
    """Score every candidate ship for one detection, best first."""
    rows = []
    for _, c in candidates.iterrows():
        ship = registry_by_imo.get(c.get("imo")) if registry_by_imo else None
        rows.append({"vessel": c["vessel"], "imo": c.get("imo", ""), "estimate_method": c["method"],
                     **score_candidate(det, c, ship, sensor)})
    return sorted(rows, key=lambda r: r["score"], reverse=True)
