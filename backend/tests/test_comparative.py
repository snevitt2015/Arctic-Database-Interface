from datetime import datetime, timedelta, timezone

from app.analytics.comparative import compare, shadow_analysis, size_class_of, size_class_probs
from app.analytics.sar_detect import detect_ships, synthetic_chip
from app.analytics.shadow import shadow_azimuth, shadow_from_height
from app.analytics.sun import sun_position

T = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
LNG = {"id": 1, "imo": "9074729", "mmsi": "273000001", "name": "LNG ONE", "length_m": 299, "beam_m": 50, "air_draft_m": 52}
LNG2 = {"id": 3, "imo": "9074743", "mmsi": "273000003", "name": "LNG TWO", "length_m": 299, "beam_m": 50, "air_draft_m": 52}
TUG = {"id": 2, "imo": "9074731", "mmsi": "273000002", "name": "TUG", "length_m": 40, "beam_m": 12, "air_draft_m": 18}


def det(**kw):
    return {"lat": 72.0, "lon": 60.0, "acquired_at": T, "length_m": 292, "length_err_m": 15, "pos_err_m": 10, **kw}


def optical(height=52):
    t = T + timedelta(minutes=30)
    sun = sun_position(t, 72.0, 60.0)
    return {"acquired_at": t, "lat": 72.001, "lon": 60.002, "gsd_m": 10, "shadow_err_m": 10,
            "shadow_length_m": shadow_from_height(height, sun["sun_elevation"]),
            "shadow_azimuth_deg": shadow_azimuth(sun["sun_azimuth"])}


HERE = {"lat": 72.0005, "lon": 60.001, "uncertainty_km": 0.5, "method": "exact"}
FAR = {"lat": 70.0, "lon": 25.0, "uncertainty_km": 1, "method": "exact"}


def test_match_uses_available_comparatives():
    card = compare(det(), [{"vessel": LNG, "est": HERE}], optical())
    assert card["verdict"] == "match" and card["confidence"] > 0.7
    assert set(card["candidates"][0]["comparatives"]) == {"ais_position", "size_class", "shadow_height"}
    assert card["coverage"] == 0.55
    assert any("model_reference" in m for m in card["candidates"][0]["missing"])


def test_flag_feature_from_ais():
    ru = {**LNG, "flag": "Russia"}
    ok = compare(det(), [{"vessel": ru, "est": {**HERE, "mmsi": "273000001"}}])
    bad = compare(det(), [{"vessel": ru, "est": {**HERE, "mmsi": "636000001"}}])
    assert ok["candidates"][0]["comparatives"]["features"]["score"] == 1.0
    assert bad["candidates"][0]["comparatives"]["features"]["score"] == 0.0


def test_one_comparative_is_never_a_match():
    card = compare(det(length_m=None), [{"vessel": LNG, "est": HERE}])
    assert card["verdict"] == "probable" and card["coverage"] == 0.3


def test_model_reference_separates_sister_ships():
    reg = [{"vessel": LNG, "est": FAR}, {"vessel": LNG2, "est": {**FAR, "lat": 69}}]
    no_models = compare(det(), [], optical(), registry=reg)
    assert no_models["verdict"] == "dark" and "fit equally" in " ".join(no_models["flags"])
    models = [{"vessel_id": 1, "method": "electronic", "colour": "bw", "score": 0.9},
              {"vessel_id": 3, "method": "electronic", "colour": "bw", "score": 0.3}]
    with_models = compare(det(), [], optical(), registry=reg, models=models)
    assert "location spoofing" in " ".join(with_models["flags"]) and "LNG ONE" in " ".join(with_models["flags"])


def test_identity_mismatch_from_size_class():
    card = compare(det(), [{"vessel": TUG, "est": HERE}])
    assert card["verdict"] == "identity_mismatch" and card["identified_vessel"]["name"] == "TUG"


def test_identity_mismatch_from_model_reference():
    models = [{"vessel_id": 1, "method": "physical", "colour": "color", "score": 0.1}]
    card = compare(det(length_m=None), [{"vessel": LNG, "est": HERE}], models=models)
    assert card["verdict"] == "identity_mismatch"


def test_stale_ais_is_dark():
    card = compare(det(), [{"vessel": LNG, "est": {"lat": 70.0, "lon": 50.0, "uncertainty_km": 600, "method": "last_known"}}])
    assert card["verdict"] == "dark"


def test_size_classes():
    assert size_class_of(299) == size_class_of(270) != size_class_of(250)
    p = size_class_probs(245, 15)
    assert abs(sum(x["probability"] for x in p) - 1) < 0.01


def test_shadow_polar_night_and_wrong_direction():
    night = shadow_analysis({"acquired_at": datetime(2026, 12, 21, 9, tzinfo=timezone.utc), "lat": 75, "lon": 60,
                             "shadow_length_m": 100})
    assert night["sun_elevation_deg"] < 0 and not night["usable"]
    o = optical()
    o["shadow_azimuth_deg"] = (o["shadow_azimuth_deg"] + 90) % 360
    assert not shadow_analysis(o)["usable"]


def test_cfar_measures_synthetic_tanker():
    chip = synthetic_chip(length_m=250, width_m=44, heading_deg=30, pixel_m=10)
    [d] = [x for x in detect_ships(chip, 10, bounds=(59, 71.9, 61, 72.1)) if x["ship_shaped"]]
    assert abs(d["length_m"] - 250) <= 30 and abs(d["heading_deg"] - 30) <= 10
