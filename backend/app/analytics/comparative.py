"""
comparative.py  -  Which ship is this SAR detection? A confidence score built from several
independent comparisons ("comparatives"), each scored 0-1 on its own.

  ais_position     Was this ship's AIS (interpolated to the image time) where the radar sees a ship?
                   Stale AIS counts as weak evidence.
  size_class       Does the SAR length put the target in the same size class as the ship's registry
                   length? Scored as the probability that the SAR length, given its error, falls in the
                   ship's class - not a precise dimension match.
  model_reference  Comparison of the SAR chip against reference renders of this ship from the team's
                   3D models (physical build, CAD, fully electronic / Blender; B&W or colour). Each
                   comparison is entered as a 0-1 score (by an analyst now; by an automated matcher later).
  features         Ship features checked against the target: flag (automatically: the MMSI the ship broadcasts
                   vs its registry flag), plus any recorded by an analyst - radar system, superstructure
                   position, cargo system, cranes, funnel... each 0-1.
  shadow_height    Only when an optical image exists AND the sun is high enough: height from the measured
                   shadow vs the registry air draft.

confidence = weighted average of the comparatives that are AVAILABLE for that ship. The card always says
how many were available (coverage): a 0.9 from one comparative is weaker than 0.8 from four.

Nothing here measures length or width from optical imagery (dropped 22 Nov 2026: not what the team needs).
"""
import math

from app.analytics.geo import distance_km
from app.analytics.identity import mmsi_country, same_country
from app.analytics.shadow import height_error, height_from_shadow, shadow_azimuth, shadow_from_height
from app.analytics.sun import sun_position

WEIGHTS = {"ais_position": 0.30, "model_reference": 0.25, "features": 0.20, "size_class": 0.15, "shadow_height": 0.10}
LABELS = {"ais_position": "Location: AIS position at image time", "model_reference": "3D-model reference comparison",
          "features": "Ship features: flag, radar system, superstructure...", "size_class": "Length: SAR size class vs registry",
          "shadow_height": "Shadow height vs air draft"}

# Size classes by length overall (m). Boundaries follow common tanker / gas-carrier classes.
SIZE_CLASSES = [
    ("Small: tug, fishing, supply", 0, 60),
    ("Coastal / small cargo", 60, 120),
    ("Handysize / medium", 120, 180),
    ("Panamax / MR", 180, 230),
    ("Aframax / LR2", 230, 260),
    ("Suezmax / large LNG (Arc7)", 260, 310),
    ("VLCC / Q-Max", 310, 500),
]
SAR_SHIFT_KM = 1.0            # moving ships appear shifted in SAR by up to ~1 km
POS_REF_KM = 10.0             # position estimates vaguer than this count as weaker evidence
SHADOW_DIR_TOL_DEG = 20.0
LOW_SUN_DEG = 5.0
HEIGHT_REL_ERR = 0.12
MATCH_CONF, PROBABLE_CONF, MARGIN = 0.70, 0.45, 0.15
NEARBY = 0.10                 # ais_position below this = the ship wasn't really here


def _bell(diff, err):
    return math.exp(-0.5 * (diff / err) ** 2) if err and err > 0 else 1.0


def _cdf(x, mu, sd):
    return 0.5 * (1 + math.erf((x - mu) / (sd * math.sqrt(2))))


def size_class_of(length_m):
    if length_m is None:
        return None
    for i, (name, lo, hi) in enumerate(SIZE_CLASSES):
        if lo <= length_m < hi:
            return i
    return len(SIZE_CLASSES) - 1


def size_class_probs(length_m, err_m):
    """Probability the true length lies in each class, given the SAR length and its error."""
    if length_m is None:
        return []
    sd = max(err_m or 15, 1)
    out = []
    for name, lo, hi in SIZE_CLASSES:
        p = _cdf(hi, length_m, sd) - _cdf(lo, length_m, sd)
        out.append({"class": name, "from_m": lo, "to_m": hi, "probability": round(p, 3)})
    return out


def shadow_analysis(opt: dict) -> dict:
    sun = sun_position(opt["acquired_at"], opt["lat"], opt["lon"])
    elev, az = sun["sun_elevation"], sun["sun_azimuth"]
    out = {"sun_elevation_deg": elev, "sun_azimuth_deg": az,
           "expected_shadow_azimuth_deg": round(shadow_azimuth(az), 1),
           "measured_shadow_length_m": opt.get("shadow_length_m"),
           "measured_shadow_azimuth_deg": opt.get("shadow_azimuth_deg"),
           "height_m": None, "height_err_m": None, "usable": False, "notes": []}
    if elev <= 0:
        out["notes"].append("Sun below the horizon at image time (polar night): no shadow evidence")
        return out
    if elev < LOW_SUN_DEG:
        out["notes"].append(f"Very low sun ({elev:.1f}°): shadow height unreliable")
    L = opt.get("shadow_length_m")
    if L is None:
        out["notes"].append("Shadow length not measured")
        return out
    err = opt.get("shadow_err_m") or 1.5 * (opt.get("gsd_m") or 10)
    out["height_m"] = round(height_from_shadow(L, elev), 1)
    out["height_err_m"] = round(height_error(L, err, elev), 1)
    out["usable"] = elev >= LOW_SUN_DEG
    if opt.get("shadow_azimuth_deg") is not None:
        d = abs((opt["shadow_azimuth_deg"] - out["expected_shadow_azimuth_deg"] + 180) % 360 - 180)
        out["direction_error_deg"] = round(d, 1)
        if d > SHADOW_DIR_TOL_DEG:
            out["usable"] = False
            out["notes"].append(f"Measured shadow points {d:.0f}° from where the sun puts it: not the ship's shadow")
    return out


def _vessel_public(v):
    return {k: v.get(k) for k in ("id", "imo", "mmsi", "name", "flag", "ship_type", "classification",
                                  "length_m", "beam_m", "air_draft_m", "watchlist", "sanctioned")}


def _feature_items(vessel, checks, broadcast_mmsi=None):
    items = [{k: c.get(k) for k in ("feature", "expected", "observed", "match", "source")}
             for c in checks if c.get("vessel_id") == vessel.get("id")]
    if broadcast_mmsi and vessel.get("flag") and mmsi_country(broadcast_mmsi):
        country = mmsi_country(broadcast_mmsi)
        items.insert(0, {"feature": "flag", "expected": vessel["flag"], "observed": f"{country} (MMSI {broadcast_mmsi})",
                         "match": 1.0 if same_country(vessel["flag"], country) else 0.0, "source": "AIS"})
    return items


def _comparatives(det, vessel, est, shadow, models, checks=(), broadcast_mmsi=None):
    """All comparatives for one candidate ship. Returns {name: {score, detail}} and a list of what's missing."""
    comp, missing = {}, []
    # AIS position
    if est and est.get("lat") is not None:
        d = distance_km(det["lat"], det["lon"], est["lat"], est["lon"])
        err = math.sqrt((est.get("uncertainty_km") or 1) ** 2 + ((det.get("pos_err_m") or 10) / 1000) ** 2
                        + SAR_SHIFT_KM ** 2)
        comp["ais_position"] = {"score": round(_bell(d, err) * min(1.0, POS_REF_KM / err), 3),
                                "detail": f"AIS ({est.get('method')}) puts it {d:,.1f} km away, ±{err:,.1f} km"}
    else:
        missing.append("ais_position: no AIS for this ship around the image time")
    # size class
    reg_len = vessel.get("length_m")
    if det.get("length_m") is None:
        missing.append("size_class: SAR length not measured")
    elif reg_len is None:
        missing.append("size_class: registry has no length for this ship")
    else:
        ci = size_class_of(reg_len)
        name, lo, hi = SIZE_CLASSES[ci]
        sd = max(det.get("length_err_m") or 15, 1)
        p = _cdf(hi, det["length_m"], sd) - _cdf(lo, det["length_m"], sd)
        comp["size_class"] = {"score": round(p, 3),
                              "detail": f"Registry {reg_len:.0f} m = {name}; SAR {det['length_m']:.0f} ± {sd:.0f} m "
                                        f"falls in that class with probability {p:.2f}"}
    # 3D-model reference
    ms = [m for m in models if m.get("vessel_id") == vessel.get("id")]
    if ms:
        s = sum(m["score"] for m in ms) / len(ms)
        comp["model_reference"] = {"score": round(s, 3), "detail": "; ".join(
            f"{m['method']} ({m['colour']}): {m['score']:.2f}" for m in ms),
            "comparisons": [{k: m.get(k) for k in ("method", "colour", "score", "reviewer", "notes")} for m in ms]}
    else:
        missing.append("model_reference: no 3D-model comparison entered for this ship")
    # ship features (flag, radar system, ...)
    items = _feature_items(vessel, checks, broadcast_mmsi)
    if items:
        comp["features"] = {"score": round(sum(i["match"] for i in items) / len(items), 3),
                            "detail": "; ".join(f"{i['feature']}: {'fits' if i['match'] >= 0.5 else 'does NOT fit'}"
                                                f" ({i['observed']} vs {i['expected']})" for i in items),
                            "items": items}
    else:
        missing.append("features: no ship features checked yet (flag needs AIS; others are recorded per detection)")
    # shadow height
    if shadow is None:
        missing.append("shadow_height: no optical image")
    elif not shadow.get("usable"):
        missing.append("shadow_height: " + (shadow["notes"][0] if shadow["notes"] else "shadow not usable"))
    elif vessel.get("air_draft_m") is None:
        missing.append("shadow_height: registry has no air draft for this ship")
    else:
        reg = vessel["air_draft_m"]
        sc = _bell(shadow["height_m"] - reg, shadow["height_err_m"] + HEIGHT_REL_ERR * reg)
        comp["shadow_height"] = {"score": round(sc, 3),
                                 "detail": f"Shadow gives {shadow['height_m']} ± {shadow['height_err_m']} m; registry "
                                           f"air draft {reg} m (expected shadow "
                                           f"{shadow_from_height(reg, shadow['sun_elevation_deg']):.0f} m)"}
    return comp, missing


def _confidence(comp, only=None):
    keys = [k for k in comp if only is None or k in only]
    w = sum(WEIGHTS[k] for k in keys)
    if not w:
        return 0.0, 0.0
    return round(sum(WEIGHTS[k] * comp[k]["score"] for k in keys) / w, 3), round(w, 2)


def compare(det: dict, candidates: list, opt: dict = None, registry: list = None, models: list = None,
            checks: list = None) -> dict:
    """
    det         SAR detection: lat, lon, acquired_at, length_m, length_err_m, width_m, heading_deg, pos_err_m
    candidates  [{"vessel": registry dict, "est": {lat, lon, uncertainty_km, method}}] - AIS ships near it
    opt         optional optical image of the target: acquired_at, lat, lon, gsd_m, shadow_length_m,
                shadow_err_m, shadow_azimuth_deg, image_id, image_url   (shadow only - no dimensions)
    registry    [{"vessel": ..., "est": ...}] for look-alikes (watch list)
    models      [{"vessel_id", "method", "colour", "score", ...}] 3D-model comparisons for this detection
    """
    models, checks = models or [], checks or []
    shadow = shadow_analysis(opt) if opt else None
    flags = []

    rows = []
    for c in candidates:
        comp, missing = _comparatives(det, c["vessel"], c.get("est"), shadow, models, checks,
                                      broadcast_mmsi=(c.get("est") or {}).get("mmsi"))
        conf, cov = _confidence(comp)
        rows.append({"vessel": _vessel_public(c["vessel"]), "confidence": conf, "coverage": cov,
                     "comparatives": comp, "missing": missing,
                     "ais_estimate": {k: (c.get("est") or {}).get(k) for k in ("lat", "lon", "method", "uncertainty_km")}})
    rows.sort(key=lambda r: (-r["confidence"] * (0.5 + 0.5 * r["coverage"])))

    nearby = [r for r in rows if r["comparatives"].get("ais_position", {}).get("score", 0) >= NEARBY]
    best = nearby[0] if nearby else None
    second = nearby[1]["confidence"] if len(nearby) > 1 else 0.0
    verdict, identified = "dark", None
    if best:
        n = len(best["comparatives"])
        size = best["comparatives"].get("size_class", {}).get("score")
        model = best["comparatives"].get("model_reference", {}).get("score")
        if best["comparatives"]["ais_position"]["score"] >= 0.5 and ((size is not None and size < 0.1)
                                                                     or (model is not None and model < 0.2)):
            verdict, identified = "identity_mismatch", best
            flags.append(f"{best['vessel']['name']}'s AIS is here, but its hull doesn't fit the radar target "
                         "(size class or 3D-model comparison): its AIS identity may belong to another ship")
        elif best["confidence"] >= MATCH_CONF and n >= 2 and best["confidence"] - second >= MARGIN:
            verdict, identified = "match", best
        elif best["confidence"] >= PROBABLE_CONF:
            verdict, identified = "probable", best
        else:
            verdict = "ambiguous"

    # look-alikes (comparatives that don't depend on position) for anything not identified
    lookalikes = []
    if verdict in ("dark", "ambiguous", "identity_mismatch") and registry:
        for r in registry:
            comp, missing = _comparatives(det, r["vessel"], None, shadow, models, checks)
            comp.pop("ais_position", None)
            if not comp:
                continue
            conf, cov = _confidence(comp)
            est = r.get("est") or {}
            far = round(distance_km(det["lat"], det["lon"], est["lat"], est["lon"]), 1) if est.get("lat") is not None else None
            lookalikes.append({"vessel": _vessel_public(r["vessel"]), "confidence": conf, "coverage": cov,
                               "comparatives": comp, "ais_reports_km_away": far, "ais_method": est.get("method")})
        lookalikes = sorted([x for x in lookalikes if x["confidence"] >= 0.5],
                            key=lambda x: (-x["confidence"] * (0.5 + 0.5 * x["coverage"])))[:5]
        if lookalikes:
            # rank by confidence weighted by coverage: a ship strong on 3 comparatives beats one equally strong
            # on 2 (it simply hasn't been tested on the rest - and the card says which are missing)
            eff = lambda x: x["confidence"] * (0.5 + 0.5 * x["coverage"])  # noqa: E731
            top = lookalikes[0]
            close = [x for x in lookalikes if eff(x) >= eff(top) - 0.1]
            if (len(close) == 1 and len(top["comparatives"]) >= 2 and top["confidence"] >= MATCH_CONF
                    and (top["ais_reports_km_away"] or 0) > 50):
                flags.append(f"Radar target matches {top['vessel']['name']} on {len(top['comparatives'])} comparatives "
                             f"(confidence {top['confidence']:.2f}), whose AIS places it "
                             f"{top['ais_reports_km_away']:,.0f} km away: possible location spoofing")
            elif len(close) > 1:
                flags.append(f"{len(close)} registry ships fit equally on the available comparatives "
                             f"({', '.join(x['vessel']['name'] for x in close)}): more comparatives needed")

    if det.get("possible_sts"):
        flags.append("Radar blob split into two hulls side by side: possible ship-to-ship transfer")
    if not det.get("ship_shaped", True):
        flags.append("Target is not ship-shaped (ice, platform or artefact?)")

    v = identified["vessel"] if identified else None
    headline = {"match": f"{v['name']} (IMO {v['imo']})" if v else "",
                "probable": f"Probably {v['name']} (IMO {v['imo']})" if v else "",
                "identity_mismatch": f"AIS says {v['name']} - hull doesn't fit" if v else "",
                "dark": "Unidentified - no AIS ship broadcasting here",
                "ambiguous": "Ambiguous - comparatives don't single out one ship"}[verdict]

    probs = size_class_probs(det.get("length_m"), det.get("length_err_m"))
    return {
        "verdict": verdict, "headline": headline,
        "confidence": identified["confidence"] if identified else 0.0,
        "coverage": identified["coverage"] if identified else 0.0,
        "identified_vessel": v,
        "position": {"lat": round(det["lat"], 5), "lon": round(det["lon"], 5)},
        "sar": {k: det.get(k) for k in ("satellite", "acquired_at", "length_m", "length_err_m", "width_m",
                                         "heading_deg", "pos_err_m", "contrast_db", "possible_sts")},
        "size_class": sorted([p for p in probs if p["probability"] >= 0.05], key=lambda p: -p["probability"]),
        "optical": None if not opt else {k: opt.get(k) for k in ("image_id", "satellite", "acquired_at", "image_url")},
        "shadow": shadow,
        "candidates": rows[:5], "lookalikes": lookalikes, "flags": flags,
        "method": {"weights": WEIGHTS, "labels": LABELS,
                   "note": "confidence = weighted average of the comparatives available for that ship; "
                           "coverage = share of the total weight they represent"},
    }
