from datetime import datetime, timedelta, timezone

from app.analytics.geo import knots_to_kmh, move_point
from app.analytics.identity import mmsi_problems, valid_imo
from app.analytics.spoofing import (detect_for_vessel, detect_gaps, detect_identity_changes, detect_imo_clones,
                                    detect_jumps, detect_on_off_cycling, detect_speed_mismatch)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
M = "273000001"


def track(lat=70.0, lon=35.0, course=60, kn=12, hours=12, every=10, start=T0, mmsi=M, **kw):
    out = []
    for i in range(int(hours * 60 / every) + 1):
        out.append({"mmsi": mmsi, "ts": start + timedelta(minutes=i * every), "lat": lat, "lon": lon, "sog": kn,
                    "cog": course, "source": "test", **kw})
        lat, lon = move_point(lat, lon, course, knots_to_kmh(kn) * every / 60)
    return out


def kinds(found):
    return sorted(a["kind"] for a in found)


def test_clean_track_has_no_anomalies():
    assert detect_for_vessel(track(name="CLEAN", imo="9074729"), "273000001", "Russia") == []


def test_gap_detected_and_sts_flagged():
    t = track(hours=48)
    gap = t[:50] + [{**p, "ts": p["ts"] + timedelta(hours=20), "lat": t[49]["lat"], "lon": t[49]["lon"] + 0.01}
                    for p in t[50:51]]
    [a] = detect_gaps(gap, M)
    assert a["kind"] == "ais_gap" and a["severity"] == "high" and a["details"]["possible_sts"]


def test_short_gap_not_flagged():
    t = track(hours=6)
    assert detect_gaps(t[:10] + t[20:], M) == []          # 100 min silence < 6 h


def test_on_off_cycling():
    t = track(hours=24)
    keep = [p for i, p in enumerate(t) if not (10 <= i < 17 or 30 <= i < 37 or 50 <= i < 57)]
    [a] = detect_on_off_cycling(keep, M)
    assert a["details"]["count"] == 3


def test_single_fake_position_is_teleport():
    t = track(hours=4)
    t[10] = {**t[10], "lat": 75.0, "lon": 55.0}
    [a] = detect_jumps(t, M)
    assert a["kind"] == "teleport" and a["details"].get("outlier")


def test_two_transmitters_is_collision():
    a = track(lat=70, lon=30, kn=0.1, hours=2, every=20)
    b = track(lat=72.5, lon=42, kn=0.1, hours=2, every=20, start=T0 + timedelta(minutes=10))
    found = detect_jumps(sorted(a + b, key=lambda p: p["ts"]), M)
    assert "mmsi_collision" in kinds(found)


def test_speed_mismatch():
    t = track(kn=12, hours=6)
    lying = [{**p, "sog": 3.0} for p in t]                  # says 3 kn, actually does 12
    [a] = detect_speed_mismatch(lying, M)
    assert a["details"]["count"] >= 3


def test_identity_change():
    t = track(hours=2, name="ARCTIC ONE", imo="9074729")
    t2 = track(hours=2, start=T0 + timedelta(hours=2, minutes=10), name="NORTH ONE", imo="9074729")
    [a] = detect_identity_changes(t + t2, M)
    assert a["details"]["changed"] == ["name"] and a["severity"] == "medium"
    assert a["details"]["before"]["name"] == "ARCTIC ONE"


def test_rename_ignoring_spaces_is_not_a_change():
    t = track(hours=1, name="SCF SAKHALIN") + track(hours=1, start=T0 + timedelta(hours=2), name="SCFSAKHALIN")
    assert detect_identity_changes(t, M) == []


def test_imo_clone():
    a = track(lat=69, lon=33, hours=1, imo="9074729", name="A")
    b = track(lat=73, lon=55, hours=1, imo="9074729", name="B", mmsi="636000001")
    [x] = detect_imo_clones(a + b)
    assert x["kind"] == "imo_clone"


def test_ids():
    assert valid_imo("9074729") and not valid_imo("9074728")
    assert mmsi_problems("273123456", "Russia") == []
    assert any("Liberia" in p for p in mmsi_problems("636123456", "Russia"))
    assert mmsi_problems("123456789")
    assert mmsi_problems("27312345")


def test_rerun_is_idempotent():
    t = track(hours=24)
    t[30] = {**t[30], "lat": 75.0, "lon": 55.0}
    k1 = {a["dedupe_key"] for a in detect_for_vessel(t, M)}
    k2 = {a["dedupe_key"] for a in detect_for_vessel(list(reversed(t)), M)}
    assert k1 == k2
