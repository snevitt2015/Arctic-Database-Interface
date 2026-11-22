"""Feed parsers, tested on message samples (the live endpoints aren't reachable from CI)."""
from datetime import datetime, timezone

from app.ingest import aisstream, norway_ais


def test_aisstream_position():
    msg = {"MessageType": "PositionReport",
           "MetaData": {"MMSI": 273123456, "ShipName": "SAMPLE SHIP   ", "latitude": 70.1, "longitude": 33.2,
                        "time_utc": "2026-09-20 10:15:30.123456789 +0000 UTC"},
           "Message": {"PositionReport": {"UserID": 273123456, "Latitude": 70.1, "Longitude": 33.2, "Sog": 11.5,
                                          "Cog": 45.0, "TrueHeading": 511, "NavigationalStatus": 0}}}
    p = aisstream.parse(msg)
    assert p["mmsi"] == "273123456" and p["name"] == "SAMPLE SHIP" and p["heading"] is None
    assert p["ts"] == datetime(2026, 9, 20, 10, 15, 30, 123456, tzinfo=timezone.utc)
    assert p["nav_status"] == "under way using engine"


def test_aisstream_static():
    msg = {"MessageType": "ShipStaticData",
           "MetaData": {"MMSI": 273123456, "ShipName": "SAMPLE", "time_utc": "2026-09-20 10:15:30 +0000 UTC"},
           "Message": {"ShipStaticData": {"ImoNumber": 9074729, "CallSign": "UBXY ", "Name": "SAMPLE SHIP",
                                          "Dimension": {"A": 200, "B": 50, "C": 20, "D": 24},
                                          "MaximumStaticDraught": 11.2}}}
    p = aisstream.parse(msg)
    assert p["imo"] == "9074729" and p["length_m"] == 250 and p["beam_m"] == 44 and "lat" not in p


def test_aisstream_subscription_covers_arctic():
    sub = aisstream.subscription("k")
    assert sub["BoundingBoxes"][0][0][0] == 60


def test_norway_decoded_position_and_static():
    ts = datetime(2026, 9, 20, tzinfo=timezone.utc)
    p = norway_ais.parse_decoded({"msg_type": 1, "mmsi": 257123000, "lat": 78.2, "lon": 15.6, "speed": 102.3,
                                  "course": 360.0, "heading": 511, "status": 0}, ts)
    assert p["sog"] is None and p["cog"] is None and p["heading"] is None and p["lat"] == 78.2
    s = norway_ais.parse_decoded({"msg_type": 5, "mmsi": 257123000, "shipname": "NORSK@@", "imo": 9074729,
                                  "callsign": "LA123", "to_bow": 100, "to_stern": 20, "to_port": 8,
                                  "to_starboard": 8}, ts)
    assert s["name"] == "NORSK" and s["length_m"] == 120 and s["imo"] == "9074729"


def test_pyais_roundtrip():
    """Real NMEA sentence through pyais -> our parser."""
    from pyais import decode
    d = decode(b"!AIVDM,1,1,,B,15M67FC000G?ufbE`FepT@3n00Sa,0*5C").asdict()
    p = norway_ais.parse_decoded(d, datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert p is not None and -90 <= p["lat"] <= 90
