"""
fixture_data.py  -  TEST-ONLY synthetic tracks used by the automated tests.

This is NOT data and is never loaded into a database anyone uses. It exists so the tests can
check each detector and verdict against a case whose right answer is known in advance.
It refuses to run outside pytest (conftest.py sets ARCTIC_TESTING=1 and a throwaway database).

Cases (all ships fictional, named "TEST ..."):
  clean track -> match; 30 h gap with near-stop; on/off cycling; location spoof; two transmitters on
  one MMSI; identity change; IMO clone; placeholder MMSI; tug-vs-tanker identity mismatch; one teleport.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if os.environ.get("ARCTIC_TESTING") != "1":
    raise SystemExit("fixture_data.py is test-only: run pytest instead")

from app.analytics.geo import knots_to_kmh, move_point  # noqa: E402
from app.analytics.shadow import shadow_azimuth, shadow_from_height  # noqa: E402
from app.analytics.sun import sun_position  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import Vessel  # noqa: E402
from app.services.ingest import store_pings  # noqa: E402
from app.services.sar import add_detections, compare_detection, get_or_create_scene  # noqa: E402
from app.models import ModelComparison, OpticalObservation  # noqa: E402

T0 = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)


def imo_with_check(six: str) -> str:
    total = sum(int(d) * w for d, w in zip(six, range(7, 1, -1)))
    return six + str(total % 10)


REGISTRY = [
    # name, mmsi, imo6, flag, type, class, L, B, air draft
    ("TEST POLAR TRADER", "273100001", "990001", "Russia", "Tanker", "Aframax / ice-class shuttle tanker", 250, 44, 48),
    ("TEST ICE LANTERN", "273100002", "990002", "Russia", "LNG tanker", "LNG carrier, Arc7 / Yamalmax", 299, 50, 52),
    ("TEST NORTHERN WAY", "273100003", "990003", "Russia", "Tanker", "MR product tanker", 183, 32, 40),
    ("TEST KARA SPIRIT", "273100004", "990004", "Russia", "LNG tanker", "LNG carrier, Arc7 / Yamalmax", 299, 50, 52),
    ("TEST TWIN STAR", "273100005", "990005", "Russia", "Tanker", "Panamax / LR1 tanker", 228, 32, 44),
    ("TEST VOSTOK", "273100006", "990006", "Russia", "Tanker", "Suezmax tanker", 275, 48, 50),
    ("TEST CLONE A", "273100007", "990007", "Russia", "Tanker", "Aframax / ice-class shuttle tanker", 245, 42, 46),
    ("TEST SMALL TUG", "273100009", "990009", "Russia", "Tug", "Tug", 40, 12, 18),
    ("TEST JUMPER", "273100010", "990010", "Russia", "Tanker", "MR product tanker", 183, 32, 40),
]


def track(mmsi, start, lat, lon, course, knots, hours, every_min=10, **extra):
    out, t = [], start
    for _ in range(int(hours * 60 / every_min) + 1):
        out.append({"mmsi": mmsi, "ts": t, "lat": lat, "lon": lon, "sog": knots, "cog": course,
                    "source": "test", **extra})
        lat, lon = move_point(lat, lon, course, knots_to_kmh(knots) * every_min / 60)
        t += timedelta(minutes=every_min)
    return out


def static(mmsi, ts, name, imo, callsign=None):
    return {"mmsi": mmsi, "ts": ts, "name": name, "imo": imo, "callsign": callsign, "source": "test"}


def main():
    Base.metadata.drop_all(engine)
    init_db()
    s = SessionLocal()
    for name, mmsi, imo6, flag, typ, cls, L, B, air in REGISTRY:
        s.add(Vessel(name=name, mmsi=mmsi, imo=imo_with_check(imo6), flag=flag, ship_type=typ, classification=cls,
                     length_m=L, beam_m=B, air_draft_m=air, watchlist=True, notes="TEST FIXTURE - NOT A REAL SHIP"))
    s.commit()
    imo = {r[0]: imo_with_check(r[2]) for r in REGISTRY}
    pings = []

    # 1. clean tanker leaving Murmansk, NE at 12 kn
    pings += [static("273100001", T0, "TEST POLAR TRADER", imo["TEST POLAR TRADER"])]
    pings += track("273100001", T0, 69.6, 34.0, 45, 12, 48)

    # 2. AIS gap with near-stop (possible STS)
    tr = track("273100002", T0, 71.0, 38.0, 70, 13, 40)
    pings += [static("273100002", T0, "TEST ICE LANTERN", imo["TEST ICE LANTERN"])]
    pings += tr[:60] + [{**tr[60], "ts": tr[59]["ts"] + timedelta(hours=30), "lat": tr[59]["lat"] + 0.05,
                         "lon": tr[59]["lon"] + 0.1, "sog": 12}] \
        + [{**p, "ts": p["ts"] + timedelta(hours=30)} for p in track("273100002", tr[59]["ts"], tr[59]["lat"] + 0.05,
                                                                     tr[59]["lon"] + 0.1, 70, 13, 8)[1:]]

    # 3. on/off cycling: four 60-110 min silences within 20 h
    tr = track("273100003", T0, 70.2, 31.0, 90, 11, 30)
    drop = set()
    for start_idx, n in [(20, 7), (40, 9), (60, 6), (85, 11)]:
        drop |= set(range(start_idx, start_idx + n))
    pings += [static("273100003", T0, "TEST NORTHERN WAY", imo["TEST NORTHERN WAY"])]
    pings += [p for i, p in enumerate(tr) if i not in drop]

    # 4. location spoofing: AIS says it's sailing off northern Norway...
    pings += [static("273100004", T0, "TEST KARA SPIRIT", imo["TEST KARA SPIRIT"])]
    pings += track("273100004", T0, 71.2, 24.0, 80, 11, 48)

    # 5. two transmitters, one MMSI (alternating every 10 min between two areas)
    a = track("273100005", T0 + timedelta(hours=10), 70.5, 30.0, 0, 0.1, 3, every_min=20)
    b = track("273100005", T0 + timedelta(hours=10, minutes=10), 72.5, 42.0, 0, 0.1, 3, every_min=20)
    pings += [static("273100005", T0, "TEST TWIN STAR", imo["TEST TWIN STAR"])] + a + b

    # 6. identity change under one MMSI
    pings += [static("273100006", T0, "TEST VOSTOK", imo["TEST VOSTOK"], "UBAA1")]
    first = track("273100006", T0, 69.9, 36.0, 30, 10, 12)
    pings += first
    pings += [static("273100006", T0 + timedelta(hours=12, minutes=5), "TEST VOSTOK-1", imo_with_check("990016"), "UBAA2")]
    pings += track("273100006", T0 + timedelta(hours=12), first[-1]["lat"], first[-1]["lon"], 30, 10, 12)[1:]

    # 7. IMO clone: two MMSIs broadcasting the same IMO 900 km apart
    pings += [static("273100007", T0, "TEST CLONE A", imo["TEST CLONE A"]),
              static("636100008", T0, "TEST CLONE B", imo["TEST CLONE A"])]
    pings += [{**p, "imo": imo["TEST CLONE A"]} for p in track("273100007", T0, 69.3, 33.5, 0, 0.2, 6, every_min=30, name="TEST CLONE A")]
    pings += [{**p, "imo": imo["TEST CLONE A"]} for p in track("636100008", T0 + timedelta(minutes=5), 73.0, 55.0,
                                                                90, 11, 6, every_min=30, name="TEST CLONE B")]

    # 8. fake MMSI
    pings += [static("123456789", T0, "TEST GHOST", None)] + track("123456789", T0, 70.8, 45.0, 200, 9, 6)

    # 9. small tug AIS where SAR will see a big tanker hull
    pings += [static("273100009", T0, "TEST SMALL TUG", imo["TEST SMALL TUG"])]
    pings += track("273100009", T0 + timedelta(hours=20), 70.9, 47.5, 120, 8, 12)

    # 10. one teleport
    tr = track("273100010", T0, 69.8, 40.0, 10, 10, 10)
    tr[30] = {**tr[30], "lat": 74.5, "lon": 52.0}
    pings += [static("273100010", T0, "TEST JUMPER", imo["TEST JUMPER"])] + tr

    res = store_pings(s, pings)
    print("pings:", res)

    # ---------------- SAR + optical
    # A) TEST POLAR TRADER seen by SAR at 26 h; optical 40 min later with measured shadow
    t_sar = T0 + timedelta(hours=26)
    pt = [p for p in pings if p["mmsi"] == "273100001" and p.get("lat") and p["ts"] == t_sar][0]
    scene = get_or_create_scene(s, "TEST_S1A_IW_GRDH_20260920T020000", "Sentinel-1A", t_sar, pixel_m=10, mode="IW",
                                source="test")
    [det_a] = add_detections(s, scene, [{"lat": pt["lat"] + 0.003, "lon": pt["lon"] + 0.01, "length_m": 243,
                                          "width_m": 47, "length_err_m": 15, "width_err_m": 15, "heading_deg": 47,
                                          "pos_err_m": 10, "contrast_db": 22}], source="test", run_compare=False)
    t_opt = t_sar + timedelta(hours=7, minutes=40)      # Sentinel-2 morning pass
    lat_o, lon_o = move_point(pt["lat"], pt["lon"], 45, knots_to_kmh(12) * (7 + 40 / 60))
    sun = sun_position(t_opt, lat_o, lon_o)
    s.add(OpticalObservation(image_id="TEST_S2B_MSIL2A_20260920", satellite="Sentinel-2B", acquired_at=t_opt,
                             lat=lat_o, lon=lon_o, gsd_m=10,
                             shadow_length_m=round(shadow_from_height(47, sun["sun_elevation"]) + 4, 1), shadow_err_m=12,
                             shadow_azimuth_deg=round(shadow_azimuth(sun["sun_azimuth"]) + 3, 1),
                             sar_detection_id=det_a, source="test"))
    s.commit()

    # B) the TEST KARA SPIRIT, loitering in the Kara Sea with no AIS
    t_sar_b = T0 + timedelta(hours=28)
    scene_b = get_or_create_scene(s, "TEST_S1A_EW_GRDM_20260920T040000", "Sentinel-1A", t_sar_b, pixel_m=40,
                                  mode="EW", source="test")
    [det_b] = add_detections(s, scene_b, [{"lat": 72.40, "lon": 63.10, "length_m": 290, "width_m": 56,
                                            "length_err_m": 40, "width_err_m": 40, "heading_deg": 115,
                                            "pos_err_m": 40, "contrast_db": 25}], source="test", run_compare=False)
    t_opt_b = t_sar_b + timedelta(hours=3, minutes=10)
    sun_b = sun_position(t_opt_b, 72.405, 63.12)
    s.add(OpticalObservation(image_id="TEST_S2A_MSIL2A_20260920_KARA", satellite="Sentinel-2A", acquired_at=t_opt_b,
                             lat=72.405, lon=63.12, gsd_m=10,
                             shadow_length_m=round(shadow_from_height(52, sun_b["sun_elevation"]) - 6, 1),
                             shadow_err_m=12, shadow_azimuth_deg=round(shadow_azimuth(sun_b["sun_azimuth"]) - 4, 1),
                             sar_detection_id=det_b, source="test"))
    s.commit()

    # C) big hull exactly where TEST SMALL TUG reports itself
    t_sar_c = T0 + timedelta(hours=26)
    tug = [p for p in pings if p["mmsi"] == "273100009" and p.get("lat") and p["ts"] == t_sar_c][0]
    [det_c] = add_detections(s, scene, [{"lat": tug["lat"], "lon": tug["lon"] + 0.005, "length_m": 248,
                                         "width_m": 45, "length_err_m": 15, "width_err_m": 15, "heading_deg": 120,
                                         "pos_err_m": 10, "contrast_db": 21}], source="test", run_compare=False)

    # 3D-model reference comparisons for the Kara Sea target: the two sister ships are identical in size,
    # so only the model comparison can separate them
    vid = {v.name: v.id for v in s.scalars(select(Vessel))}
    s.add_all([ModelComparison(sar_detection_id=det_b, vessel_id=vid["TEST KARA SPIRIT"], method="electronic",
                               colour="bw", score=0.9, reviewer="test"),
               ModelComparison(sar_detection_id=det_b, vessel_id=vid["TEST KARA SPIRIT"], method="cad",
                               colour="color", score=0.85, reviewer="test"),
               ModelComparison(sar_detection_id=det_b, vessel_id=vid["TEST ICE LANTERN"], method="electronic",
                               colour="bw", score=0.35, reviewer="test")])
    s.commit()

    for d in (det_a, det_b, det_c):
        c = compare_detection(s, d)
        print(f"detection {d}: {c.verdict:18s} conf={c.confidence:.2f}  {c.card['headline']}")
        for f in c.card["flags"]:
            print("     flag:", f)
    s.close()
