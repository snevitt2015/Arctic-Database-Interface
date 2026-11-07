"""
clustering.py  -  Shape tools for radar (and optical) ship detections.

  euclidean_clusters   group bright pixels into objects: pixels closer than GAP metres are the
                       same object, even if they don't touch. A tanker whose deck returns little
                       radar energy shows up as bow + tanks + stern pieces; this joins them.
  l_shape_fit          fit the best rectangle to an object's pixels -> length, beam, heading,
                       4 corners. Radar often lights up only the two sides facing the satellite
                       (an "L"), which pulls a plain average/PCA off-axis; the L-shape search
                       tries every angle and keeps the one where the pixels hug two edges.
  kmeans               k-means clustering (plain numpy, repeatable).
  split_merged         when one object is too wide / too long for a single ship, try splitting it
                       into 2 ships with k-means. Catches ship-to-ship (STS) transfers, where
                       two tankers lie alongside each other and merge into one radar blob.
  size_classes         k-means on many detections' length and beam -> size groups (e.g.
                       LNG carriers vs small tankers vs icebreakers) to sort unknown detections.

All lengths are metres; x = east, y = north.
"""
import math

import numpy as np
from scipy import ndimage

CLUSTER_GAP_M = 80.0      # pieces closer than this belong to the same ship (dark decks leave gaps)
MAX_BEAM_M = 70.0         # wider than this can't be one ship (largest tankers/LNG ~ 50-60 m)
MAX_SHIP_LENGTH_M = 400.0  # longer than this can't be one ship (largest ~ 400 m)
MIN_SHIP_ASPECT = 3.0     # ships are long: length / beam of each part of a split must be at least this


# ------------------------------------------------------------------ Euclidean clustering
def euclidean_clusters(mask, pixel_m, gap_m=CLUSTER_GAP_M):
    """Label the True pixels of `mask` so pixels within gap_m of each other share a label.
    (Single-linkage clustering, done fast on the grid: grow every pixel by half the gap,
    label the touching blobs, then keep the labels only on the original pixels.)"""
    r = max(1, int(round(gap_m / pixel_m / 2)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = (xx * xx + yy * yy) <= r * r
    grown = ndimage.binary_dilation(mask, structure=disk)
    labels, n = ndimage.label(grown, structure=np.ones((3, 3)))
    labels = np.where(mask, labels, 0)
    return labels, n


# ------------------------------------------------------------------ L-shape fitting
def _rect_from_angle(x, y, theta):
    e1 = np.array([math.cos(theta), math.sin(theta)])
    e2 = np.array([-math.sin(theta), math.cos(theta)])
    c1, c2 = x * e1[0] + y * e1[1], x * e2[0] + y * e2[1]
    return e1, e2, c1, c2


def l_shape_fit(x, y, pixel_m=1.0, criterion="closeness", step_deg=1.0):
    """Best-fitting rectangle to points (x, y) in metres.
    criterion: "closeness" (points near two edges - the L-shape method, Zhang et al. 2017),
               "area" (smallest box), or "variance" (tightest edges).
    Returns dict(length_m, width_m, heading_deg 0-180 [compass, bow/stern unknown],
                 cx, cy, corners [4 x 2], criterion)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3:
        return {"length_m": pixel_m, "width_m": pixel_m, "heading_deg": 0.0,
                "cx": float(np.mean(x)), "cy": float(np.mean(y)), "corners": None, "criterion": criterion}
    d0 = max(pixel_m / 2, 1e-3)
    best, best_theta = -np.inf, 0.0
    for theta in np.radians(np.arange(0, 90, step_deg)):
        _, _, c1, c2 = _rect_from_angle(x, y, theta)
        if criterion == "area":
            score = -((c1.max() - c1.min()) * (c2.max() - c2.min()))
        else:
            d1 = np.minimum(c1.max() - c1, c1 - c1.min())
            d2 = np.minimum(c2.max() - c2, c2 - c2.min())
            if criterion == "variance":
                near1 = d1 <= d2
                score = -(np.var(d1[near1]) if near1.any() else 0) - (np.var(d2[~near1]) if (~near1).any() else 0)
            else:
                score = np.sum(1.0 / np.maximum(np.minimum(d1, d2), d0))
        if score > best:
            best, best_theta = score, theta
    e1, e2, c1, c2 = _rect_from_angle(x, y, best_theta)
    a1, b1, a2, b2 = c1.min(), c1.max(), c2.min(), c2.max()
    corners = np.array([a1 * e1 + a2 * e2, b1 * e1 + a2 * e2, b1 * e1 + b2 * e2, a1 * e1 + b2 * e2])
    len1, len2 = b1 - a1 + pixel_m, b2 - a2 + pixel_m
    axis = e1 if len1 >= len2 else e2
    centre = corners.mean(0)
    return {"length_m": float(max(len1, len2)), "width_m": float(min(len1, len2)),
            "heading_deg": float((math.degrees(math.atan2(axis[0], axis[1])) + 360) % 180),
            "cx": float(centre[0]), "cy": float(centre[1]), "corners": corners, "criterion": criterion}


# ------------------------------------------------------------------ k-means
def kmeans(points, k, weights=None, init=None, iters=50):
    """Plain k-means. points [n x d]; init [k x d] starting centres (default: spread picks).
    Returns (labels, centres, inertia)."""
    P = np.asarray(points, float)
    w = np.ones(len(P)) if weights is None else np.asarray(weights, float)
    if init is None:
        order = np.argsort(P[:, 0])
        init = P[order[np.linspace(0, len(P) - 1, k).astype(int)]]
    C = np.array(init, float)
    for _ in range(iters):
        d = ((P[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        newC = np.array([np.average(P[lab == j], axis=0, weights=w[lab == j]) if (lab == j).any() else C[j]
                         for j in range(k)])
        if np.allclose(newC, C):
            break
        C = newC
    d = ((P[:, None, :] - C[None, :, :]) ** 2).sum(-1)
    lab = d.argmin(1)
    inertia = float((w * d[np.arange(len(P)), lab]).sum())
    return lab, C, inertia


def split_merged(x, y, w, pixel_m, max_k=2):
    """If the object at points (x, y) (weights w = brightness) is too big for one ship, split it.
    Default: into 2 (an STS transfer is two ships). Tries k = 2..max_k, starting the centres spread along the object's long and short axes, and
    keeps the first split where every part is ship-sized and long-and-narrow.
    Returns a list of index arrays (one per ship), or None to keep it whole."""
    whole = l_shape_fit(x, y, pixel_m)
    if is_ship_shaped(whole, pixel_m):
        return None
    # wider than two ships side by side, or longer than two end to end: ice / land, not ships
    if whole["width_m"] - 1.5 * pixel_m > 2 * MAX_BEAM_M or whole["length_m"] > 2 * MAX_SHIP_LENGTH_M:
        return None
    P = np.column_stack([x, y])
    for k in range(2, max_k + 1):
        tries = []
        for axis_deg in (whole["heading_deg"], whole["heading_deg"] + 90):   # along, then across
            a = math.radians(axis_deg)
            u = np.array([math.sin(a), math.cos(a)])
            proj = P @ u
            centre = np.average(P, axis=0, weights=w)
            init = [centre + (q - centre @ u) * u for q in np.quantile(proj, np.linspace(0.15, 0.85, k))]
            # two ways: k-means on the 2-D pixels, and k-means on their position along this
            # axis only (finds ships lying flush alongside, where 2-D k-means drifts to cutting
            # both ships in half across)
            lab2d, _, _ = kmeans(P, k, w, init)
            lab1d, _, _ = kmeans(proj[:, None], k, w, np.quantile(proj, np.linspace(0.15, 0.85, k))[:, None])
            for lab in (lab2d, lab1d):
                parts = [np.nonzero(lab == j)[0] for j in range(k)]
                if any(len(p) < 3 for p in parts):
                    continue
                fits = [l_shape_fit(x[p], y[p], pixel_m) for p in parts]
                # accept when every part looks like ONE ship: ship-sized and long-and-narrow
                # (a bright ice floe cut into pieces gives fat parts and is left alone)
                if all(is_ship_shaped(f, pixel_m) for f in fits):
                    inertia = sum(float((w[p] * ((P[p] - np.average(P[p], axis=0, weights=w[p])) ** 2).sum(1)).sum())
                                  for p in parts)
                    tries.append((inertia, parts))
        if tries:
            return min(tries, key=lambda t: t[0])[1]
    return None


def is_ship_shaped(fit, pixel_m=0.0):
    """Could this rectangle be one ship? (allowing 1.5 pixels of measuring error)"""
    slack = 1.5 * pixel_m
    L, W = fit["length_m"], max(fit["width_m"] - slack, pixel_m)
    return W <= MAX_BEAM_M and L - slack <= MAX_SHIP_LENGTH_M and L >= MIN_SHIP_ASPECT * W


# ------------------------------------------------------------------ size classes
def size_classes(lengths_m, widths_m, k=3):
    """Group many detections by size with k-means on (length, beam), each scaled by its spread.
    Returns (labels, centres as [[length_m, beam_m], ...] sorted small -> large)."""
    L, B = np.asarray(lengths_m, float), np.asarray(widths_m, float)
    ok = np.isfinite(L) & np.isfinite(B)
    labels = np.full(len(L), -1)
    if ok.sum() < k:
        return labels, np.empty((0, 2))
    X = np.column_stack([L[ok], B[ok]])
    s = X.std(0); s[s == 0] = 1
    lab, C, _ = kmeans(X / s, k)
    C = C * s
    order = np.argsort(C[:, 0])
    remap = {old: new for new, old in enumerate(order)}
    labels[ok] = [remap[l] for l in lab]
    return labels, C[order]
