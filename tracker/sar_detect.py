"""
sar_detect.py  -  Find ships in a SAR image chip and measure them.

Ships are bright points on a darker sea in radar images. Steps:
  1. CFAR ("constant false alarm rate"): a pixel is a detection if it is much brighter
     than the sea AROUND it (local mean + K x local spread). This adapts to rough and
     calm sea in different parts of the image.
  2. Group touching bright pixels into objects.
  3. Measure each object: centre, length, width and heading (principal axes of the
     bright pixels), each with an error based on the pixel size.

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
    # an object = connected "grow" pixels that contain at least one seed; close 1-pixel gaps first
    grow = ndimage.binary_closing(grow, structure=np.ones((3, 3))) | seeds
    labels, n = ndimage.label(grow, structure=np.ones((3, 3)))
    seeded = np.unique(labels[seeds])
    labels[~np.isin(labels, seeded)] = 0
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
        cx, cy = np.average(x, weights=w), np.average(y, weights=w)
        if len(r) >= 2:
            cov = np.cov(np.vstack([x - cx, y - cy]))
            vals, vecs = np.linalg.eigh(cov)
            major = vecs[:, 1]
        else:
            major = np.array([0.0, 1.0])
        minor = np.array([-major[1], major[0]])
        along = (x - cx) * major[0] + (y - cy) * major[1]
        across = (x - cx) * minor[0] + (y - cy) * minor[1]
        length = float(along.max() - along.min() + pixel_m)
        width = float(across.max() - across.min() + pixel_m)
        if length > MAX_LENGTH_M:
            continue
        heading = (np.degrees(np.arctan2(major[0], major[1])) + 360) % 180   # 0-180, bow/stern unknown
        det = {
            "row": float(rows - 1 - cy / pixel_m), "col": float(cx / pixel_m),
            "pixels": int(len(r)), "peak": float(w.max()),
            "peak_db": float(10 * np.log10(max(w.max(), 1e-12))), "contrast_db": round(float(contrast_db), 1),
            "length_m": round(length, 1), "width_m": round(width, 1),
            "length_err_m": round(1.5 * pixel_m, 1), "width_err_m": round(1.5 * pixel_m, 1),
            "heading_deg": round(float(heading), 1),
            "heading_err_deg": round(float(np.degrees(np.arctan2(pixel_m, max(length, pixel_m)))) * 2, 1),
            "pos_err_m": round(pixel_m, 1),
        }
        if bounds is not None:
            west, south, east, north = bounds
            det["lat"] = north - (det["row"] + 0.5) / rows * (north - south)
            det["lon"] = west + (det["col"] + 0.5) / cols * (east - west)
        out.append(det)
    return sorted(out, key=lambda d: d["peak"], reverse=True)


def synthetic_chip(length_m=250, width_m=45, heading_deg=30, pixel_m=10, size=200, seed=1):
    """A fake radar chip with one ship, for tests and for trying settings."""
    rng = np.random.default_rng(seed)
    img = rng.gamma(shape=4.0, scale=0.01 / 4, size=(size, size))     # speckled sea, mean 0.01
    yy, xx = np.mgrid[0:size, 0:size]
    x = (xx - size / 2) * pixel_m
    y = (size / 2 - yy) * pixel_m
    h = np.radians(heading_deg)
    along = x * np.sin(h) + y * np.cos(h)
    across = x * np.cos(h) - y * np.sin(h)
    ship = (np.abs(along) <= length_m / 2) & (np.abs(across) <= width_m / 2)
    img[ship] += rng.uniform(0.5, 2.0, ship.sum())
    return img
