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
