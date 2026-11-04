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
