"""End to end: build the test-only fixture, then call every tab's endpoints like the frontend will."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="module")
def client():
    import fixture_data
    fixture_data.main()
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_overview(client):
    o = client.get("/api/overview").json()
    assert o["counts"]["watchlist"] == 9 and o["counts"]["open_anomalies"] >= 8
    assert o["comparison_verdicts"] == {"match": 1, "dark": 1, "identity_mismatch": 1}
    assert o["top_risk"][0]["risk_score"] > 0
    m = client.get("/api/overview/map").json()
    layers = {f["properties"]["layer"] for f in m["features"]}
    assert {"vessels", "anomalies", "anomaly_links", "sar"} <= layers


def test_live(client):
    assert len(client.get("/api/live/positions", params={"hours": 72}).json()["features"]) >= 10
    assert len(client.get("/api/live/feed", params={"limit": 5}).json()) == 5
    src = {s["id"]: s for s in client.get("/api/live/sources").json()}
    assert src["test"]["positions"] > 1000 and "aisstream" in src
    csv = b"MMSI,TIMESTAMP,LAT,LON,SPEED,SHIPNAME\n273999999,2026-09-21T00:00:00Z,70.1,33.0,10,UPLOAD TEST\n" \
          b"273999999,2026-09-21T00:10:00Z,70.12,33.05,10,UPLOAD TEST\n"
    r = client.post("/api/live/import", files={"file": ("mt.csv", csv)}, data={"source": "MarineTraffic"}).json()
    assert r["positions_added"] == 2


def test_spoofing(client):
    rows = client.get("/api/spoofing/anomalies").json()
    kinds = {r["kind"] for r in rows}
    assert {"ais_gap", "on_off_cycling", "teleport", "mmsi_collision", "identity_change", "imo_clone",
            "invalid_id"} <= kinds
    gap = next(r for r in rows if r["kind"] == "ais_gap")
    d = client.get(f"/api/spoofing/anomalies/{gap['id']}").json()
    assert d["evidence"] and d["label"] == "AIS switched off"
    assert client.patch(f"/api/spoofing/anomalies/{gap['id']}", json={"status": "confirmed"}).json()["status"] == "confirmed"
    assert client.get("/api/spoofing/summary").json()["by_kind"]["ais_gap"] == 1
    assert client.post("/api/spoofing/rerun", params={"full": True}).status_code == 200
    # analyst status survives a re-run
    assert client.get(f"/api/spoofing/anomalies/{gap['id']}").json()["status"] == "confirmed"


def test_sar(client):
    dets = client.get("/api/sar/detections").json()
    by = {d["verdict"]: d for d in dets}
    card = client.get(f"/api/sar/detections/{by['match']['detection_id']}").json()
    assert card["identified_vessel"]["imo"] and card["shadow"]["usable"]
    assert {"ais_position", "size_class", "shadow_height"} <= set(card["candidates"][0]["comparatives"])
    assert card["coverage"] == 0.75         # location + length + flag (from AIS) + shadow; no 3D-model check yet
    assert card["candidates"][0]["comparatives"]["features"]["items"][0]["feature"] == "flag"
    dark = client.get(f"/api/sar/detections/{by['dark']['detection_id']}").json()
    assert "location spoofing" in " ".join(dark["flags"]) and "TEST KARA SPIRIT" in " ".join(dark["flags"])


def test_sar_chip_pipeline(client):
    from app.analytics.sar_detect import synthetic_chip
    buf = io.BytesIO()
    np.save(buf, synthetic_chip(length_m=250, width_m=44, heading_deg=45, pixel_m=10))
    r = client.post("/api/sar/process-chip", files={"file": ("chip.npy", buf.getvalue())},
                    data={"pixel_m": 10, "west": 34.9, "south": 69.99, "east": 35.1, "north": 70.01,
                          "acquired_at": "2026-09-20T02:00:00Z", "scene_id": "TEST_CHIP", "satellite": "Sentinel-1A"})
    out = r.json()
    assert r.status_code == 200 and out["detections"] >= 1 and out["comparisons"][0]["verdict"] != "pending"


def test_optical_triggers_recompare(client):
    dets = client.get("/api/sar/detections", params={"verdict": "identity_mismatch"}).json()
    d = dets[0]
    r = client.post("/api/sar/optical", json={"image_id": "T", "acquired_at": "2026-09-20T02:20:00Z",
                                               "lat": d["lat"], "lon": d["lon"], "gsd_m": 10,
                                               "sar_detection_id": d["detection_id"]}).json()
    assert r["comparison"]["optical"]["image_id"] == "T" and "sun_elevation" in r


def test_vessels(client):
    vs = client.get("/api/vessels", params={"q": "TEST"}).json()
    v = next(x for x in vs if x["name"] == "TEST VOSTOK-1")
    prof = client.get(f"/api/vessels/{v['id']}").json()
    assert prof["osint_links"][0]["source"] == "MarineTraffic"
    assert client.get(f"/api/vessels/{v['id']}/track").json()["features"]


def test_interface_and_fleet(client):
    r = client.get("/")
    assert r.status_code == 200 and "Arctic Vessel Watch" in r.text
    fl = client.get("/api/overview/fleet").json()
    assert fl["features"] and "age_hours" in fl["features"][0]["properties"]


def test_model_comparison_feeds_confidence(client):
    d = client.get("/api/sar/detections", params={"verdict": "match"}).json()[0]
    before = client.get(f"/api/sar/detections/{d['detection_id']}").json()
    r = client.post(f"/api/sar/detections/{d['detection_id']}/model-comparisons",
                    json={"vessel_id": d["vessel_id"], "method": "physical", "colour": "bw", "score": 0.8}).json()
    assert r["coverage"] == 1.0 and r["coverage"] > before["coverage"]
    f = client.post(f"/api/sar/detections/{d['detection_id']}/feature-checks",
                    json={"vessel_id": d["vessel_id"], "feature": "radar_system", "expected": "X-band on mast",
                          "observed": "not resolved", "match": 0.5}).json()
    assert len(f["candidates"][0]["comparatives"]["features"]["items"]) == 2
    assert "model_reference" in r["candidates"][0]["comparatives"]
    bad = client.post(f"/api/sar/detections/{d['detection_id']}/model-comparisons",
                      json={"vessel_id": d["vessel_id"], "method": "guess", "colour": "bw", "score": 0.8})
    assert bad.status_code == 400


def test_routes_stay_at_sea_and_skip_conflicts(client):
    r = client.get("/api/data/routes", params={"days": 3650}).json()
    assert r["features"] and "estimate" in r["note"]
    for f in r["features"]:
        p = f["properties"]
        assert p["how"] in ("direct (short hop)", "along sea lanes (reconstructed)",
                            "straight through the ice (north of the sea-lane network)")
        if p["how"].startswith("direct"):
            assert p["km"] < 25                     # never a long straight line
        # no leg is faster than a ship can sail: conflicting records are not joined
        assert p["km"] / max(p["hours"], 1 / 60) / 1.852 <= 30.5
    lanes = client.get("/api/data/shipping-lanes").json()
    assert lanes["features"] and "MARNET" in lanes["source"]
