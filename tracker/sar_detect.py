"""
sar_detect.py  -  Find ships in a SAR image chip and measure them.

Ships are bright points on a darker sea in radar images. Steps:
  1. CFAR ("constant false alarm rate"): a pixel is a detection if it is much brighter
     than the sea AROUND it (local mean + K x local spread). This adapts to rough and
     calm sea in different parts of the image.
  2. Group bright pixels into objects with EUCLIDEAN CLUSTERING: pieces closer than
     CLUSTER_GAP_M are one ship (a tanker's dark deck can split it into bow/tanks/stern).
  3. Too wide or too long for one ship? Try a K-MEANS split into 2 ships - two tankers
     alongside each other (ship-to-ship transfer) merge into one blob. Flagged possible_sts.
  4. Measure each ship with L-SHAPE FITTING (best rectangle; robust when radar only lights
     up two sides): centre, length, width, heading, corners, each with a pixel-based error.

Input: a 2-D array of radar backscatter (sigma0, linear units), north-up, plus the
pixel size in metres and the chip's lat/lon bounds. Works on any SAR source
(Sentinel-1, TerraSAR-X, ...) - only the pixel size changes.

Limits to keep in mind:
  * Sentinel-1 pixels are ~10 m (IW) or ~40 m (EW over Arctic sea ice); length error
    is about one to two pixels, so ships of similar size can't be told apart.
  * Sea ice, ice ridges and icebergs are bright too. Pass an ice/land mask.
  * A MOVING ship appears shifted along the satellite's flight direction (Doppler);
    its wake points back to the true position. match.py allows for this.
  * Heading from SAR is ambiguous by 180° (bow vs stern) unless a wake is visible.
"""
import numpy as np
from scipy import ndimage

from tracker.clustering import euclidean_clusters, l_shape_fit, split_merged, is_ship_shaped, CLUSTER_GAP_M

K_SIGMA = 5.0            # a "seed" pixel is this many local standard deviations brighter than the sea
K_GROW = 4.0             # ...and the ship's outline is grown out to pixels this much brighter
MIN_CONTRAST_DB = 10.0   # and its brightest pixel must be at least 10 dB (10x) above the local sea
MIN_PIXELS = 3           # smaller objects are noise
MAX_LENGTH_M = 450       # bigger objects are land, ice or artefacts
# The sea around a pixel is measured in a ring OUTSIDE a guard window bigger than any ship,
# so a big tanker's own brightness doesn't raise its "background" (sizes follow the pixel size).


def _local_stats(img, outer, inner):
    """Mean and std of the ring between an inner (guard) and outer window around each pixel."""
    def box_sum(a, size):
        return ndimage.uniform_filter(a, size=size, mode="reflect") * size * size
    n = outer * outer - inner * inner
    s = box_sum(img, outer) - box_sum(img, inner)
    s2 = box_sum(img * img, outer) - box_sum(img * img, inner)
    mean = s / n
    var = np.maximum(s2 / n - mean * mean, 1e-12)
    return mean, np.sqrt(var)


def detect_ships(sigma0, pixel_m, bounds=None, mask=None, k=K_SIGMA):
    """Detect and measure ships.

    sigma0 : 2-D array, linear backscatter, row 0 = north edge
    pixel_m: pixel size in metres
    bounds : (west, south, east, north) in degrees, to give each detection a lat/lon
    mask   : boolean array, True where NOT to look (land, ice)
    Returns a list of dicts sorted by brightness."""
    img = np.nan_to_num(np.asarray(sigma0, dtype="float64"), nan=0.0)
    guard = int(np.ceil(MAX_LENGTH_M / pixel_m)) | 1
    outer = min(2 * guard + 1, (min(img.shape) // 2) * 2 - 1)
    guard = min(guard, outer - 2)
    mean, std = _local_stats(img, outer, guard)
    seeds = img > mean + k * std
    grow = img > mean + K_GROW * std
    if mask is not None:
        seeds &= ~mask
        grow &= ~mask
    # objects = Euclidean clusters of "grow" pixels that contain at least one seed
    # 1) touching pieces that contain a seed (drops sea speckle that only passed the low bar)
    grow = ndimage.binary_closing(grow, structure=np.ones((3, 3))) | seeds
    pieces, n_pieces = ndimage.label(grow, structure=np.ones((3, 3)))
    # a piece is kept only if it is a real return: a seed, at least MIN_PIXELS, and a peak
    # MIN_CONTRAST_DB above the sea (lone speckle spikes near a ship would otherwise get joined on)
    idx = np.arange(1, n_pieces + 1)
    size = ndimage.sum(np.ones_like(img), pieces, idx)
    peak_ratio = ndimage.maximum(img / np.maximum(mean, 1e-12), pieces, idx)
    has_seed = ndimage.maximum(seeds.astype(float), pieces, idx) > 0
    good = idx[(size >= MIN_PIXELS) & has_seed & (peak_ratio >= 10 ** (MIN_CONTRAST_DB / 10))]
    keep = np.isin(pieces, good)
    # 2) join pieces closer than CLUSTER_GAP_M (one ship broken up by a dark deck)
    labels, n = euclidean_clusters(keep, pixel_m, max(CLUSTER_GAP_M, 1.5 * pixel_m))
    rows, cols = img.shape
    out = []
    for i in range(1, n + 1):
        r, c = np.nonzero(labels == i)
        if len(r) < MIN_PIXELS:
            continue
        w = img[r, c]
        sea = float(np.median(mean[r, c]))
        contrast_db = 10 * np.log10(max(w.max(), 1e-12) / max(sea, 1e-12))
        if contrast_db < MIN_CONTRAST_DB:
            continue
        # position in metres: x east, y north (rows grow southwards)
        x = c * pixel_m
        y = (rows - 1 - r) * pixel_m
        if np.ptp(x) > 2 * MAX_LENGTH_M or np.ptp(y) > 2 * MAX_LENGTH_M:
            continue                                   # land / ice edge, not ships
        parts = split_merged(x, y, w, pixel_m) if len(r) >= 6 else None
        groups = parts if parts else [np.arange(len(r))]
        for part_no, g in enumerate(groups):
            det = _measure(x[g], y[g], w[g], r[g], c[g], pixel_m, rows, cols, bounds, contrast_db)
            if det is None:
                continue
            det["split_from_blob"] = i if parts else None
            det["possible_sts"] = bool(parts)          # side-by-side ships: ship-to-ship transfer?
            out.append(det)
    return sorted(out, key=lambda d: d["peak"], reverse=True)


def _measure(x, y, w, r, c, pixel_m, rows, cols, bounds, contrast_db):
    """Length / width / heading of one ship from its pixels (L-shape fit), plus the old
    principal-axis heading for comparison."""
    fit = l_shape_fit(x, y, pixel_m)
    length, width = fit["length_m"], fit["width_m"]
    if length > MAX_LENGTH_M:
        return None
    cx, cy = np.average(x, weights=w), np.average(y, weights=w)
    if len(x) >= 2:
        vals, vecs = np.linalg.eigh(np.cov(np.vstack([x - cx, y - cy])))
        major = vecs[:, 1]
        pca_heading = (np.degrees(np.arctan2(major[0], major[1])) + 360) % 180
    else:
        pca_heading = np.nan
    det = {
        "row": float(rows - 1 - cy / pixel_m), "col": float(cx / pixel_m),
        "pixels": int(len(x)), "peak": float(w.max()),
        "peak_db": float(10 * np.log10(max(w.max(), 1e-12))), "contrast_db": round(float(contrast_db), 1),
        "length_m": round(length, 1), "width_m": round(width, 1),
        "length_err_m": round(1.5 * pixel_m, 1), "width_err_m": round(1.5 * pixel_m, 1),
        "heading_deg": round(fit["heading_deg"], 1),            # 0-180, bow/stern unknown
        "heading_pca_deg": round(float(pca_heading), 1) if pca_heading == pca_heading else None,
        "heading_err_deg": round(float(np.degrees(np.arctan2(pixel_m, max(length, pixel_m)))) * 2, 1),
        "pos_err_m": round(pixel_m, 1),
        # wider than any ship and not splittable into ships: ice, platform, wreck... (kept, but flagged)
        "ship_shaped": is_ship_shaped(fit, pixel_m),
        "corners_m": None if fit["corners"] is None else [[round(a, 1), round(b, 1)] for a, b in fit["corners"]],
    }
    if bounds is not None:
        west, south, east, north = bounds
        det["lat"] = north - (det["row"] + 0.5) / rows * (north - south)
        det["lon"] = west + (det["col"] + 0.5) / cols * (east - west)
    return det


def synthetic_chip(length_m=250, width_m=45, heading_deg=30, pixel_m=10, size=200, seed=1,
                   ships=None, deck_gap_m=0.0, lit_sides_only=False):
    """A fake radar chip, for tests and for trying settings.
    ships: list of (offset_east_m, offset_north_m, length_m, width_m, heading_deg); default one
           ship at the centre. deck_gap_m: a dark band across the middle of each ship (low-return
           deck). lit_sides_only: only two sides bright (the "L" radar often shows)."""
    rng = np.random.default_rng(seed)
    img = rng.gamma(shape=4.0, scale=0.01 / 4, size=(size, size))     # speckled sea, mean 0.01
    yy, xx = np.mgrid[0:size, 0:size]
    ships = ships or [(0.0, 0.0, length_m, width_m, heading_deg)]
    for ox, oy, L, W, hd in ships:
        x = (xx - size / 2) * pixel_m - ox
        y = (size / 2 - yy) * pixel_m - oy
        h = np.radians(hd)
        along = x * np.sin(h) + y * np.cos(h)
        across = x * np.cos(h) - y * np.sin(h)
        ship = (np.abs(along) <= L / 2) & (np.abs(across) <= W / 2)
        if deck_gap_m:
            ship &= np.abs(along) > deck_gap_m / 2
        if lit_sides_only:
            ship &= (along > L / 2 - 1.5 * pixel_m) | (across > W / 2 - 1.5 * pixel_m)
        img[ship] += rng.uniform(0.5, 2.0, ship.sum())
    return img
