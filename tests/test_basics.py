"""
Quick checks that the maths and the pipeline work.   Run:  python -m pytest
"""
import pandas as pd

from tracker.geo import distance_km, bearing_deg, move_point
from tracker.prediction import predict
from tracker.features import add_features
from tracker.excel_loader import load_vessels, load_ports
from tracker.matching import match_to_watchlist
from tracker.schema import standardize
from tracker.sources.global_fishing_watch import _events_to_rows, _accepted_ids


def test_distance_and_bearing():
    # Murmansk -> Arkhangelsk is roughly 590 km to the south-east
    d = distance_km(68.97, 33.06, 64.54, 40.52)
    assert 550 < d < 650
    assert 120 < bearing_deg(68.97, 33.06, 64.54, 40.52) < 160


def test_move_point_round_trip():
    lat, lon = move_point(70, 40, 90, 100)
    assert abs(distance_km(70, 40, lat, lon) - 100) < 0.1


def _track():
    t0 = pd.Timestamp("2026-03-01", tz="UTC")
    rows = []
    lat, lon = 70.0, 40.0
    for i in range(5):  # heading due east at 10 knots, report every 3 h
        rows.append({"timestamp": t0 + pd.Timedelta(hours=3 * i), "imo": "9904546", "mmsi": "",
                     "name": "X", "lat": lat, "lon": lon, "speed_kn": 10, "course": 90, "source": "test"})
        lat, lon = move_point(lat, lon, 90, 10 * 1.852 * 3)
    return standardize(pd.DataFrame(rows))


def test_prediction_moves_east():
    df = add_features(match_to_watchlist(_track(), load_vessels()), load_ports())
    p = predict(df, hours=24, step=6)
    dr = p[p["method"] == "dead_reckoning"]
    assert len(dr) == 4
    assert dr.iloc[-1]["lon"] > df.iloc[-1]["lon"]        # moved east
    # 24 h at 10 kn = 444 km
    assert abs(distance_km(df.iloc[-1]["lat"], df.iloc[-1]["lon"],
                           dr.iloc[-1]["lat"], dr.iloc[-1]["lon"]) - 444.5) < 2


def test_matching_by_imo():
    df = match_to_watchlist(_track(), load_vessels())
    assert (df["vessel"] == "ALEXEY KOSYGIN").all()
    assert (df["match_on"] == "IMO").all()


def test_dark_gap_flag():
    t = _track()
    t.loc[3:, "timestamp"] += pd.Timedelta(hours=12)        # 15 h of silence
    df = add_features(match_to_watchlist(t, load_vessels()), load_ports())
    assert df["dark_gap"].sum() == 1


def test_gfw_gap_event_parsing():
    ev = [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z", "position": {"lat": 70, "lon": 50},
           "vessel": {"ssvid": "273253000", "name": "ALEXEY KOSYGIN"},
           "gap": {"durationHours": 24, "distanceKm": 300, "intentionalDisabling": True,
                   "offPosition": {"lat": 70, "lon": 50}, "onPosition": {"lat": 71, "lon": 55}}}]
    rows = _events_to_rows(ev, "ais_off", {"imo": "9904546", "mmsi": "273253", "name": "ALEXEY KOSYGIN"})
    assert [r["event_type"] for r in rows] == ["ais_off", "ais_on"]


def test_gfw_identity_filter_drops_unrelated_vessel():
    entry = {"selfReportedInfo": [
        {"id": "a", "imo": "9324277", "ssvid": "636013234", "shipname": "NEO ENERGY"},
        {"id": "b", "imo": "", "ssvid": "636013234", "shipname": "NEO ENERGY"},     # same MMSI -> keep
        {"id": "c", "imo": "", "ssvid": "306002000", "shipname": "NOVA ENERGY"},    # our MMSI -> keep
        {"id": "d", "imo": "", "ssvid": "412287282", "shipname": "JIHUANGYU 35223"},  # unrelated -> drop
        {"id": "e", "imo": "1234567", "ssvid": "306002000", "shipname": "OTHER"},   # other IMO -> drop
    ]}
    vessel = {"imo": "9324277", "mmsi": "306002000", "name": "Nova Energy"}
    assert _accepted_ids(entry, vessel, log=lambda m: None) == ["a", "b", "c"]


def test_far_from_ports_has_no_nearest_port():
    t = _track()
    t["lat"] += 8                      # move to ~78N 40E, far from every port
    df = add_features(match_to_watchlist(t, load_vessels()), load_ports())
    assert (df["nearest_port"] == "").all()


def test_capstone_position_parsing():
    from tracker.capstone_loader import parse_latlon
    assert parse_latlon("Kara Sea, 77.5N 68.2E") == (77.5, 68.2)
    assert parse_latlon("St Petersburg Terminal Lat 59.87N 30.19W") == (59.87, -30.19)
    assert parse_latlon("Port 59.87:30.19") == (59.87, 30.19)
    assert parse_latlon("Barents Sea") is None


def test_capstone_ships_join_watch_list():
    v = load_vessels()
    assert "9694725" in set(v["imo"])          # icebreaker Arktika from the Capstone Database
    assert v["imo"].duplicated().sum() == 0 or (v["imo"] == "").sum() >= 1


# ---------------------------------------------------------------- position at a moment
def test_position_at_interpolates_between_fixes():
    from tracker.interpolate import position_at
    t = _track()                                   # fixes every 3 h heading east
    mid = t["timestamp"].iloc[1] + pd.Timedelta(hours=1.5)
    est = position_at(t, mid)
    assert est["method"] == "interpolated"
    a, b = t.iloc[1], t.iloc[2]
    assert abs(distance_km(a["lat"], a["lon"], est["lat"], est["lon"]) -
               distance_km(est["lat"], est["lon"], b["lat"], b["lon"])) < 0.5    # halfway
    assert est["uncertainty_km"] > 0


def test_position_at_exact_projected_and_last_known():
    from tracker.interpolate import position_at
    t = _track()
    assert position_at(t, t["timestamp"].iloc[0] + pd.Timedelta(minutes=5))["method"] == "exact"
    after = t["timestamp"].iloc[-1]
    p2 = position_at(t, after + pd.Timedelta(hours=2))
    assert p2["method"] == "projected" and p2["lon"] > t["lon"].iloc[-1]       # kept going east
    old = position_at(t, after + pd.Timedelta(days=3))
    assert old["method"] == "last_known" and old["uncertainty_km"] > 1000
    assert position_at(t, t["timestamp"].iloc[0] - pd.Timedelta(days=1))["method"] == "none"


def test_candidates_near_finds_the_right_ship():
    from tracker.interpolate import candidates_near
    df = add_features(match_to_watchlist(_track(), load_vessels()), load_ports())
    when = df["timestamp"].iloc[2]
    c = candidates_near(df, df["lat"].iloc[2], df["lon"].iloc[2], when, max_km=5)
    assert list(c["vessel"]) == ["ALEXEY KOSYGIN"]


# ---------------------------------------------------------------- sun
def test_sun_elevation_murmansk_midsummer_and_polar_night():
    from tracker.sun import sun_position, max_sun_elevation, nearest_step
    assert abs(sun_position("2026-06-21 09:20", 68.97, 33.08)["sun_elevation"] - 44.4) < 1
    assert sun_position("2026-12-21 09:20", 68.97, 33.08)["sun_elevation"] < 0
    assert max_sun_elevation(70) == 43.4
    assert nearest_step(37.2) == 35 and nearest_step(12) is None


# ---------------------------------------------------------------- data checks
def test_imo_check_digit():
    from tracker.validation import imo_is_valid, mmsi_problem
    assert imo_is_valid("9694725") and imo_is_valid(9904546.0)
    assert not imo_is_valid("9694726") and not imo_is_valid("123")
    assert mmsi_problem("273253") and not mmsi_problem("273210920")


def test_imagery_hemisphere_mistake_is_caught():
    from tracker.validation import check_imagery
    img = pd.DataFrame([{"kind": "Optical", "image_id": "Test St Petersburg Port", "location": "St Petersburg 59.87N 30.19W",
                         "lat": 59.87, "lon": -30.19, "date": "2026-09-22 10:31:00", "imo": "9658630"}])
    issues = check_imagery(img, load_ports())
    assert any("E/W" in i["problem"] for i in issues)


# ---------------------------------------------------------------- incremental download
def test_gfw_since_starts_after_saved_data(tmp_path, monkeypatch):
    import config
    from tracker import storage, pipeline
    monkeypatch.setattr(config, "POSITIONS_FILE", tmp_path / "positions.csv")
    monkeypatch.setattr(storage, "POSITIONS_FILE", tmp_path / "positions.csv")
    monkeypatch.setattr(storage, "PRIVATE_POSITIONS_FILE", tmp_path / "private.csv")
    rows = _track(); rows["source"] = "GFW"
    storage.save_positions(rows)
    since = pipeline.gfw_since(overlap_days=10)
    last = rows["timestamp"].max() - pd.Timedelta(days=10)
    assert since == {"9904546": last.strftime("%Y-%m-%d")}


def test_reloaded_gfw_event_replaces_old_copy(tmp_path, monkeypatch):
    from tracker import storage
    monkeypatch.setattr(storage, "POSITIONS_FILE", tmp_path / "positions.csv")
    monkeypatch.setattr(storage, "PRIVATE_POSITIONS_FILE", tmp_path / "private.csv")
    r = _track().head(1); r["source"] = "GFW"; r["event_id"] = "abc"
    storage.save_positions(r)
    r2 = r.copy(); r2["lat"] = r2["lat"] + 0.01            # GFW revised the event's position
    storage.save_positions(r2)
    saved = storage.load_positions()
    assert len(saved) == 1 and abs(saved["lat"].iloc[0] - r2["lat"].iloc[0]) < 1e-6


# ---------------------------------------------------------------- Airtable (no network: fake responses)
def test_airtable_records_look_like_excel_export(monkeypatch):
    import config
    from tracker import airtable
    monkeypatch.setattr(config, "AIRTABLE_TOKEN", "test")
    airtable._cache.clear()
    fake = {
        "Ships": [{"id": "recShip1", "fields": {"IMO number": 9694725, "Ship name": "ARKTIKA", "Vessel type": "Icebreaker"}}],
        "AIS Updates": [{"id": "recU1", "fields": {"Update ID": "ARKTIKA – 2026-10-28", "Ship": ["recShip1"],
                                                     "Speed (kn)": 0.2, "Latitude": 59.9}}],
    }
    monkeypatch.setattr(airtable, "fetch_records", lambda table: fake.get(table, []))
    df = airtable.load_table("AIS Updates")
    assert df.loc[0, "Ship"] == "9694725"            # linked record resolved to the ship's IMO
    assert df.loc[0, "Latitude"] == 59.9


# ---------------------------------------------------------------- area-name positions
def test_area_names_become_coarse_positions():
    from tracker.capstone_loader import area_to_position
    assert area_to_position("Barents Sea (Murmansk)")[3] == "Murmansk"           # bracketed place wins
    assert area_to_position("Gulf of Finland (Bolshoy Bor)")[3] == "Gulf of Finland"
    assert area_to_position("Inland Russia (Lower Volga)")[3] == "Lower Volga"   # longest name wins
    assert area_to_position("Barents Sea")[2] >= 500                            # big radius for a sea
    assert area_to_position("Somewhere unknown") is None


def test_area_reports_never_create_impossible_moves():
    t = _track().head(2)
    area = t.tail(1).copy()
    area["timestamp"] += pd.Timedelta(hours=1)
    area["lat"], area["lon"], area["pos_error_km"], area["event_type"] = 74.0, 60.0, 700, "area_report"
    df = add_features(match_to_watchlist(standardize(pd.concat([t, area])), load_vessels()), load_ports())
    assert not df["impossible_jump"].any()


def test_airtable_tables_with_missing_empty_fields_still_load(monkeypatch):
    """Airtable's API omits fields that are empty in every record (e.g. Latitude)."""
    import config
    from tracker import airtable, capstone_loader as cap
    monkeypatch.setattr(config, "AIRTABLE_TOKEN", "test")
    monkeypatch.setattr(cap, "_force_excel", False)
    airtable._cache.clear()
    fake = {
        "Ships": [{"id": "recS", "fields": {"IMO number": 9694725, "Ship name": "ARKTIKA", "Gross tonnage": 28494,
                                            "Vessel type": "Icebreaker", "Year built": 2020, "Flag / registry": "Russia (RUS)",
                                            "AIS Updates": ["recU"]}}],
        "AIS Updates": [{"id": "recU", "fields": {"Update ID": "ARKTIKA – 2026-10-28", "Ship": ["recS"],
                                                  "Last reported area": "Barents Sea (Murmansk)",
                                                  "Checked (UTC)": "2026-10-28T15:18:00.000Z"}}],   # no Latitude/Longitude at all
    }
    monkeypatch.setattr(airtable, "fetch_records", lambda table: fake.get(table, []))
    checks = cap.load_ais_checks()
    assert checks.loc[0, "imo"] == "9694725" and pd.isna(checks.loc[0, "lat"])
    pos = cap.ais_checks_as_positions(checks, cap.load_registry())
    assert pos.loc[0, "event_type"] == "area_report" and pos.loc[0, "pos_error_km"] == 20
    assert "9694725" in set(cap.tracked_vessels()["imo"])


def test_header_row_in_registry_is_skipped_not_an_error():
    from tracker.validation import check_registry
    reg = pd.DataFrame({"imo": ["9694725", "IMO number", "9694726"], "name": ["ARKTIKA", "Name of ship", "TYPO"]})
    issues = {i["record"]: i["severity"] for i in check_registry(reg)}
    assert issues["Name of ship"] == "info" and issues["TYPO"] == "error" and "ARKTIKA" not in issues


# ================================================================ imagery pipeline
def _gap_track():
    """A ship that switches AIS off for 20 h (GFW-style ais_off / ais_on pair)."""
    t = _track()
    t["source"] = "GFW"
    off = t.iloc[[-1]].copy(); off["event_type"] = "ais_off"; off["event_id"] = "g1"
    on = off.copy(); on["event_type"] = "ais_on"; on["event_id"] = "g1:on"
    on["timestamp"] += pd.Timedelta(hours=20); on["lon"] += 3
    return standardize(pd.concat([t, off, on]))


def test_ais_off_becomes_a_high_priority_cue():
    from tracker.cueing import build_cues
    df = add_features(match_to_watchlist(_gap_track(), load_vessels()), load_ports())
    cues = build_cues(df, load_vessels())
    gap = cues[cues["kind"] == "ais_off"].iloc[0]
    assert gap["priority"] == "high" and "20 h" in gap["reason"]
    assert gap["start"] < gap["end"] and gap["radius_km"] > 30
    assert build_cues(df, load_vessels())["cue_id"].tolist() == cues["cue_id"].tolist()   # stable ids


def test_copernicus_results_become_scene_rows(monkeypatch):
    from tracker.sources import copernicus
    cue = {"cue_id": "abc", "vessel": "Arktika", "lat": 69.0, "lon": 33.0, "radius_km": 50,
           "start": pd.Timestamp("2026-10-01T00:00Z"), "end": pd.Timestamp("2026-10-02T00:00Z")}
    fake = {"value": [{
        "Id": "uuid-1", "Name": "S1A_EW_GRDM_1SDH_20261001T061437_x.SAFE", "Online": True,
        "ContentDate": {"Start": "2026-10-01T06:14:37.000Z", "End": "2026-10-01T06:15:37.000Z"},
        "GeoFootprint": {"type": "Polygon", "coordinates": [[[30, 67], [37, 67], [37, 71], [30, 71], [30, 67]]]},
        "Attributes": [{"Name": "orbitDirection", "Value": "DESCENDING"}, {"Name": "operationalMode", "Value": "EW"},
                       {"Name": "polarisationChannels", "Value": "HH&HV"}, {"Name": "platformSerialIdentifier", "Value": "A"}]}]}
    calls = []
    monkeypatch.setattr(copernicus, "_get", lambda params, log: calls.append(params) or fake)
    rows = copernicus.search_cue(cue, kinds=("SAR",), log=lambda m: None)
    r = rows[0]
    assert r["satellite"] == "Sentinel-1A" and r["mode"] == "EW" and r["orbit_direction"] == "DESCENDING"
    assert r["covers_cue_centre"] is True and r["hours_from_window"] == 0.0
    assert "OData.CSC.Intersects" in calls[0]["$filter"] and "SENTINEL-1" in calls[0]["$filter"]


def test_detects_and_measures_a_ship_in_a_radar_chip():
    from tracker.sar_detect import synthetic_chip, detect_ships
    d = detect_ships(synthetic_chip(250, 45, 30, pixel_m=10), 10, bounds=(32.9, 68.95, 33.1, 69.05))
    assert len(d) == 1
    assert abs(d[0]["length_m"] - 250) <= d[0]["length_err_m"]
    assert abs(d[0]["heading_deg"] - 30) < 5
    assert 68.95 < d[0]["lat"] < 69.05


def test_shadow_geometry():
    from tracker.shadow import height_from_shadow, shadow_from_height, shadow_azimuth, lab_to_real
    assert abs(shadow_from_height(48, 20) - 131.9) < 0.5      # low Arctic sun: long shadow
    assert abs(height_from_shadow(131.9, 20) - 48) < 0.5
    assert shadow_azimuth(200) == 20
    assert abs(lab_to_real(427, 700) - 298.9) < 0.1


def test_match_score_prefers_the_right_size_ship():
    from tracker.match import score_candidate
    det = {"lat": 69.0, "lon": 33.0, "length_m": 300, "width_m": 50, "length_err_m": 15, "width_err_m": 15, "pos_err_m": 10}
    est = {"lat": 69.01, "lon": 33.0, "uncertainty_km": 2}
    big = score_candidate(det, est, {"length_m": 299, "beam_m": 50})
    small = score_candidate(det, est, {"length_m": 120, "beam_m": 20})
    unknown = score_candidate(det, est, None)
    assert big["score"] > 0.5 > small["score"] and "length_m" in unknown["missing"]


def test_chip_pipeline_end_to_end_with_fake_satellite(monkeypatch):
    from tracker import sar_chips
    from tracker.sar_detect import synthetic_chip
    df = add_features(match_to_watchlist(_track(), load_vessels()), load_ports())
    when = df["timestamp"].iloc[2]
    lat, lon = df["lat"].iloc[2], df["lon"].iloc[2]
    scenes = pd.DataFrame([{"cue_id": "c1", "vessel": "ALEXEY KOSYGIN", "kind": "SAR", "scene_id": "S1C_IW_TEST",
                            "product_uuid": "abcd1234", "start_utc": when.isoformat(), "end_utc": when.isoformat(),
                            "hours_from_window": 0}])
    monkeypatch.setattr(sar_chips, "is_configured", lambda: True)
    monkeypatch.setattr(sar_chips, "save_preview", lambda *a: None)
    def fake_chip(la, lo, half, w, **k):
        img = synthetic_chip(300, 50, 90, pixel_m=10, size=200)
        b = sar_chips.chip_bounds(la, lo, 1.0)
        return img, b, "HH", 10.0, None
    monkeypatch.setattr(sar_chips, "fetch_chip", fake_chip)
    out = sar_chips.process_scenes(scenes, df, pd.DataFrame(), pd.DataFrame(columns=["imo"]), log=lambda m: None)
    row = out.iloc[0]
    assert row["detection"] == 1 and row["best_vessel"] == "ALEXEY KOSYGIN"
    assert abs(row["length_m"] - 300) < 20 and "length_m" in row["missing"]
