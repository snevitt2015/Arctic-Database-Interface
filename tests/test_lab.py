"""
Checks for the lab (3D-print photo) modules, using synthetic photos with known answers.
Run:  python -m pytest tests/test_lab.py
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

cv2 = pytest.importorskip("cv2")

from lab import settings as S
from lab.ingest import parse_path
from lab.validate import fit_height, robust_z
from lab.train import corners_to_box, boxes_to_metrics
from lab import reference as R


@pytest.mark.parametrize("folder, sun, cam, date", [
    ("10-23-26_Sun15Camera20", 15, 20, "2026-10-23"),
    ("10-26-26Sun20Camera35", 20, 35, "2026-10-26"),
    ("11-3-26Sun25Camera25", 25, 25, "2026-11-03"),
])
def test_folder_names(folder, sun, cam, date):
    root = Path("/x")
    m = parse_path(root / folder / "IMG_6383.JPG", root)
    assert m["sun_elevation"] == sun and m["camera_angle"] == cam and m["capture_date"] == date
    assert m["rotation"] is None            # IMG numbers are never read as a rotation


def test_annotate_measures_synthetic_shadow(tmp_path):
    from lab.synthetic import render
    from lab.annotate import process_image
    img, truth = render(sun_elev=25, cam_elev=35, rotation=90, ring_r_frac=0.28, seed=1)
    p = tmp_path / "IMG_0001.JPG"
    cv2.imwrite(str(p), img)
    meta = {"vessel": "MossTypeLNG", "sun_elevation": 25, "camera_angle": 35, "rotation": 90,
            "session": "11-3-26Sun25Camera35"}
    r, extra = process_image(p, meta, S.load())
    assert r["status"] == "OK"
    assert r["ring_found"]
    assert abs(r["reach_ground_mm"] - truth["overhang_mm"]) < 0.25 * truth["overhang_mm"]
    assert abs(r["px_per_mm_ground"] - 2.0) < 0.2
    assert extra and len(extra[0]) == 2     # one vessel + one shadow YOLO-OBB label


def test_height_fit_recovers_slope():
    sun = np.array([20, 30, 40, 50.0])
    cot = 1 / np.tan(np.radians(sun))
    slope, offset, r2, n = fit_height(cot, 60 * cot - 10)
    assert abs(slope - 60) < 1e-6 and abs(offset + 10) < 1e-6 and r2 > 0.999 and n == 4


def test_robust_z_flags_outlier():
    z = robust_z(pd.Series([1.0, 1.02, 0.98, 1.01, 0.99, 0.4]))
    assert abs(z.iloc[-1]) > 3.5 and (z.iloc[:-1].abs() < 3.5).all()


def test_corners_to_box_and_metrics():
    # a 100 x 20 px hull lying left-right, and a shadow box below-right of it
    hull = [(0, 0), (100, 0), (100, 20), (0, 20)]
    b = corners_to_box(hull)
    assert abs(b["length"] - 100) < 1e-6 and abs(b["axis_deg"] - 90) < 1e-6
    shadow = [(10, 10), (110, 10), (110, 60), (10, 60)]
    m = boxes_to_metrics([{"cls": 0, "conf": .9, "corners": hull}, {"cls": 1, "conf": .8, "corners": shadow}],
                         {"px_per_mm_ground": 1.0, "camera_angle": 90, "sun_elevation": 45,
                          "vessel": "MossTypeLNG"})
    assert m["status"] == "OK" and m["reach_ground_mm"] > 0


def _ref():
    rows = []
    for sun in (20, 30, 40):
        for rot in range(0, 360, 30):
            reach = 60 / math.tan(math.radians(sun)) - 10
            rows.append({"vessel": "MossTypeLNG", "sun_elevation": sun, "camera_angle": 30, "off_nadir": 60,
                         "rotation": rot, "reach_mm": reach, "height_mm": reach * math.tan(math.radians(sun)),
                         "fitted_height_mm": 60.0, "fitted_offset_mm": -10.0})
    return pd.DataFrame(rows)


def test_reference_expected_and_score():
    ref = _ref()
    L = 287.0                                # 410 mm x 700
    e = R.expected_shadow(ref, "MossTypeLNG", L, 30)
    assert abs(e["height_m"] - 60 / 410 * L) < 0.1
    s = R.shadow_score(ref, "MossTypeLNG", L, e["shadow_overhang_m"], 30)
    assert s["p_shadow"] > 0.99
    assert R.shadow_score(ref, "MossTypeLNG", L, e["shadow_overhang_m"] + 200, 30)["p_shadow"] < 0.01


def test_sar_expectation():
    sar = R.sar_expectation(_ref(), "MossTypeLNG", 287.0, 45)
    assert abs(sar["layover_m"] - sar["radar_shadow_m"]) < 0.1      # equal at 45 deg incidence
    assert sar["in_sentinel1_iw"]
    assert R.camera_for_incidence(38) == 52
