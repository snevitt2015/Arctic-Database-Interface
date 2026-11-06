"""
sar_chips.py  -  Pull a small Sentinel-1 image crop ("chip") around a ship's estimated
position, find ships in it, and score which watch-list ship each detection could be.

Uses the Copernicus Data Space "Sentinel Hub" Process API, which cuts out just the area we
need (a few MB) instead of downloading a whole ~1 GB scene.

Setup (free):
  1. Create a Copernicus Data Space account: https://dataspace.copernicus.eu
  2. Dashboard -> User settings -> OAuth clients -> Create (copy id + secret)
  3. GitHub secrets CDSE_CLIENT_ID and CDSE_CLIENT_SECRET (or put them in .env)
Without them, chips are skipped and the rest of the pipeline still runs.

A chip is only pulled when we know roughly where the ship was at the image time
(position uncertainty <= CHIP_MAX_UNCERTAINTY_KM). Bigger areas need whole-scene
detection - use Global Fishing Watch's SAR detections for that (see docs).
"""
import io
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from tracker.interpolate import position_at, candidates_near
from tracker.sar_detect import detect_ships
from tracker.match import rank_candidates, UNMATCHED_SCORE

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
CHIP_MAX_UNCERTAINTY_KM = 15     # only pull chips when we roughly know where to look
CHIP_PIXEL_M = 10                # resample to 10 m pixels (Sentinel-1 IW native ~10 m)
CHIP_MAX_PX = 2500               # API limit per side
MAX_CHIPS_PER_RUN = 10
CHIP_DIR = Path(__file__).resolve().parents[1] / "data" / "chips"

EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: [{ bands: ["%(band)s", "dataMask"] }],
           output: { bands: 2, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.%(band)s, s.dataMask]; }
"""


def credentials():
    return os.getenv("CDSE_CLIENT_ID", ""), os.getenv("CDSE_CLIENT_SECRET", "")


def is_configured() -> bool:
    return all(credentials())


_token = {"value": None}


def _get_token():
    if _token["value"]:
        return _token["value"]
    cid, secret = credentials()
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": cid,
                                       "client_secret": secret}, timeout=60)
    r.raise_for_status()
    _token["value"] = r.json()["access_token"]
    return _token["value"]


def chip_bounds(lat, lon, half_km):
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def fetch_chip(lat, lon, half_km, when, minutes=30, pixel_m=CHIP_PIXEL_M):
    """Sigma0 (linear) array, north-up, plus bounds. Tries HH (Arctic EW/IW) then VV."""
    import tifffile
    bounds = chip_bounds(lat, lon, half_km)
    side = min(CHIP_MAX_PX, int(2 * half_km * 1000 / pixel_m))
    t0 = (pd.Timestamp(when) - pd.Timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (pd.Timestamp(when) + pd.Timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_error = None
    for band in ("HH", "VV"):
        body = {
            "input": {"bounds": {"bbox": list(bounds), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                      "data": [{"type": "sentinel-1-grd",
                                "dataFilter": {"timeRange": {"from": t0, "to": t1}, "mosaickingOrder": "mostRecent"},
                                "processing": {"orthorectify": True, "backCoeff": "SIGMA0_ELLIPSOID"}}]},
            "output": {"width": side, "height": side,
                       "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": EVALSCRIPT % {"band": band},
        }
        r = requests.post(PROCESS_URL, json=body, headers={"Authorization": f"Bearer {_get_token()}"}, timeout=180)
        if r.status_code == 200:
            arr = tifffile.imread(io.BytesIO(r.content))
            sigma0, valid = arr[..., 0], arr[..., 1] > 0
            if valid.mean() < 0.2:
                last_error = "chip mostly outside the scene"
                continue
            real_pixel_m = 2 * half_km * 1000 / side
            return sigma0, bounds, band, real_pixel_m, ~valid
        last_error = f"{r.status_code}: {r.text[:200]}"
    raise RuntimeError(f"No usable chip ({last_error})")


def save_preview(sigma0, path):
    """Small PNG of the chip (dB stretched) for the viewer. Sentinel data may be shared."""
    from PIL import Image
    db = 10 * np.log10(np.clip(sigma0, 1e-6, None))
    lo, hi = np.nanpercentile(db, 2), np.nanpercentile(db, 99.8)
    img = (np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype("uint8")
    im = Image.fromarray(img)
    if max(im.size) > 800:
        im.thumbnail((800, 800))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)


def process_scenes(scenes: pd.DataFrame, df: pd.DataFrame, cues: pd.DataFrame,
                   registry: pd.DataFrame, previous: pd.DataFrame = None, log=print) -> pd.DataFrame:
    """For SAR scenes inside a cue window where the cued ship's position is known well
    enough: pull a chip, detect, and score candidates. Returns one row per detection."""
    previous = previous if previous is not None else pd.DataFrame()
    done = set(previous["scene_id"] + "|" + previous["cue_id"]) if not previous.empty else set()
    if not is_configured():
        log("SAR chips: no CDSE_CLIENT_ID / CDSE_CLIENT_SECRET - skipping (scene search still ran)")
        return previous
    if scenes.empty:
        return previous
    reg = registry.set_index("imo").to_dict("index") if not registry.empty else {}
    imo_of = dict(zip(df["vessel"], df["imo"]))
    rows, pulled = [], 0
    sar = scenes[(scenes["kind"] == "SAR")].copy()
    sar["hours_from_window"] = pd.to_numeric(sar["hours_from_window"], errors="coerce").fillna(99)
    for _, s in sar.sort_values("hours_from_window").iterrows():
        key = f"{s['scene_id']}|{s['cue_id']}"
        if key in done or pulled >= MAX_CHIPS_PER_RUN:
            continue
        mid = pd.Timestamp(s["start_utc"]) + (pd.Timestamp(s["end_utc"]) - pd.Timestamp(s["start_utc"])) / 2
        est = position_at(df[df["vessel"] == s["vessel"]], mid)
        if est["lat"] is None or est["uncertainty_km"] is None or est["uncertainty_km"] > CHIP_MAX_UNCERTAINTY_KM:
            continue
        half = max(5.0, est["uncertainty_km"] + 2)
        try:
            sigma0, bounds, band, px, nodata = fetch_chip(est["lat"], est["lon"], half, mid)
        except Exception as e:
            log(f"SAR chips: {s['vessel']} {s['scene_id'][:40]}: {e}")
            continue
        pulled += 1
        preview = CHIP_DIR / f"{s['cue_id']}_{s['product_uuid'][:8]}.png"
        save_preview(sigma0, preview)
        dets = detect_ships(sigma0, px, bounds=bounds, mask=nodata)
        log(f"SAR chips: {s['vessel']} {mid:%Y-%m-%d %H:%M} - {len(dets)} detections ({band}, {px:.0f} m)")
        base = {"cue_id": s["cue_id"], "scene_id": s["scene_id"], "time_utc": mid.isoformat(), "band": band,
                "pixel_m": round(px, 1), "chip": f"data/chips/{preview.name}", "chip_bounds": ",".join(f"{b:.5f}" for b in bounds),
                "cued_vessel": s["vessel"], "cued_estimate": est["method"], "cued_uncertainty_km": est["uncertainty_km"]}
        if not dets:
            rows.append({**base, "detection": 0, "note": "no ship found in chip"})
        for i, d in enumerate(dets[:10], start=1):
            cands = candidates_near(df, d["lat"], d["lon"], mid, max_km=20)
            if not cands.empty:
                cands["imo"] = cands["vessel"].map(imo_of)
            ranked = rank_candidates(d, cands, reg) if not cands.empty else []
            best = ranked[0] if ranked else {}
            rows.append({**base, "detection": i, **{k: d.get(k) for k in ("lat", "lon", "length_m", "length_err_m",
                         "width_m", "width_err_m", "heading_deg", "contrast_db")},
                         "best_vessel": best.get("vessel", ""), "best_score": best.get("score", 0.0),
                         "score_parts": ", ".join(f"{k}={v}" for k, v in best.items() if k.startswith("p_")),
                         "missing": best.get("missing", ""),
                         "dark_candidate": (not ranked) or best.get("score", 0) < UNMATCHED_SCORE,
                         "other_candidates": "; ".join(f"{r['vessel']} {r['score']}" for r in ranked[1:4])})
    new = pd.DataFrame(rows)
    return pd.concat([previous, new], ignore_index=True) if not previous.empty else new
