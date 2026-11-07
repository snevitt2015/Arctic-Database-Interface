"""Kalman filter, Euclidean clustering, L-shape fitting and k-means splits."""
import numpy as np
import pandas as pd

from tracker import kalman
from tracker.geo import move_point, distance_km
from tracker.sar_detect import synthetic_chip, detect_ships
from tracker.clustering import l_shape_fit, size_classes


def _track(n=12, speed_kn=12, course=60, hours=3, start=(70.0, 30.0)):
    t0 = pd.Timestamp("2026-09-01", tz="UTC")
    rows = []
    for i in range(n):
        lat, lon = move_point(*start, course, speed_kn * 1.852 * hours * i)
        rows.append({"timestamp": t0 + pd.Timedelta(hours=hours * i), "lat": lat, "lon": lon,
                     "event_type": "position", "pos_error_km": 0.5, "vessel": "TEST", "imo": "1"})
    return pd.DataFrame(rows)


def test_kalman_learns_speed_and_predicts():
    tr = _track()
    res = kalman.kalman_track(tr)
    assert abs(res["kf_speed_kn"].iloc[-1] - 12) < 1.5
    assert not res["kf_unexpected"].any()
    p = kalman.kalman_predict(tr, hours=12, step=6)
    truth = move_point(tr["lat"].iloc[-1], tr["lon"].iloc[-1], 60, 12 * 1.852 * 12)
    end = p[p["hours_ahead"] == 12].iloc[0]
    assert distance_km(end["lat"], end["lon"], *truth) < end["uncertainty_km"]
    assert p["uncertainty_km"].is_monotonic_increasing


def test_kalman_flags_one_off_bad_position():
    tr = _track()
    lat, lon = move_point(tr.loc[6, "lat"], tr.loc[6, "lon"], 150, 300)   # 300 km off track
    tr.loc[6, ["lat", "lon"]] = lat, lon
    res = kalman.kalman_track(tr)
    assert res.loc[6, "kf_unexpected"]
    assert not res.drop(index=6)["kf_unexpected"].any()     # track continues normally after it


def test_kalman_restarts_after_long_silence():
    tr = _track()
    tr.loc[8:, "timestamp"] += pd.Timedelta(days=30)
    tr.loc[8:, ["lat", "lon"]] = (75.0, 60.0)
    res = kalman.kalman_track(tr)
    assert res.loc[8, "kf_reset"] and not res["kf_unexpected"].any()


def test_euclidean_clustering_joins_dark_deck():
    d = detect_ships(synthetic_chip(280, 45, 60, deck_gap_m=60), 10)
    assert len(d) == 1 and abs(d[0]["length_m"] - 280) < 30


def test_kmeans_splits_ships_alongside():
    d = detect_ships(synthetic_chip(ships=[(0, 0, 280, 46, 70), (52 * 0.34, -52 * 0.94, 250, 44, 70)], size=240), 10)
    assert len(d) == 2 and all(x["possible_sts"] for x in d)
    assert all(abs(x["heading_deg"] - 70) <= 3 for x in d)


def test_ice_block_not_split_into_ships():
    d = detect_ships(synthetic_chip(200, 150, 10, size=240), 10)
    assert len(d) == 1 and not d[0]["possible_sts"] and not d[0]["ship_shaped"]


def test_l_shape_heading_when_only_two_sides_lit():
    d = detect_ships(synthetic_chip(300, 50, 20, lit_sides_only=True, size=240), 10)
    assert abs(d[0]["heading_deg"] - 20) <= 2


def test_l_shape_rectangle():
    yy, xx = np.mgrid[0:30, 0:200]
    a = np.radians(35)
    x = xx.ravel() * np.sin(a) + yy.ravel() * np.cos(a)
    y = xx.ravel() * np.cos(a) - yy.ravel() * np.sin(a)
    f = l_shape_fit(x, y, 1.0)
    assert abs(f["length_m"] - 200) < 3 and abs(f["width_m"] - 30) < 3 and abs(f["heading_deg"] - 35) <= 1


def test_size_classes():
    L = [290, 295, 288, 180, 175, 185, 130, 128, 135]
    B = [48, 49, 47, 32, 31, 33, 28, 27, 29]
    lab, C = size_classes(L, B, 3)
    assert list(lab) == [2, 2, 2, 1, 1, 1, 0, 0, 0] and C[0][0] < C[1][0] < C[2][0]


def test_kalman_tighter_with_real_ais_speed():
    t = _track()
    loose = kalman.kalman_predict(t, 24, 24)["uncertainty_km"].iloc[0]
    t["speed_kn"], t["course"] = 12, 60
    tight = kalman.kalman_predict(t, 24, 24)["uncertainty_km"].iloc[0]
    assert tight < loose / 3
