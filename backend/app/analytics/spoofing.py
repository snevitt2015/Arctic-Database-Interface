"""
spoofing.py  -  Find spoofing and dark activity in raw AIS pings.

Every detector takes ONE ship's pings (dicts, sorted by time) and returns anomaly dicts.
They are pure functions (no database), so they are easy to test and to tune.

  position_invalid  lat 91 / lon 181 ("not available"), 0°N 0°E, or out of range
  ais_gap           AIS silent longer than GAP_HOURS. Also says how far the ship moved while
                    dark, and whether it seems to have STOPPED while dark (a possible STS transfer)
  on_off_cycling    transmitter switched off and on again several times in a short window
  teleport          the next position is further than any ship could sail in the time
  mmsi_collision    A→B→A jumps: two transmitters are using the same MMSI at once
  speed_mismatch    reported speed doesn't fit the distance actually covered (fabricated track)
  identity_change   name / IMO / callsign changes under the same MMSI
  invalid_id        impossible or placeholder MMSI, MMSI country ≠ reported flag, bad IMO check digit

And one detector across ships:
  imo_clone         the same IMO broadcast by different MMSIs at places far apart at the same time

Every anomaly has a `dedupe_key`, so running detection again over the same pings updates
findings instead of duplicating them.

Coverage caveat: a terrestrial-only feed (Norway AIS, aisstream near coasts) loses ships
offshore, which looks like a gap or on/off cycling. The `sources` in each anomaly's details
says which feeds the evidence came from; gaps seen by satellite AIS (GFW) are stronger evidence.
"""
import hashlib
from collections import defaultdict
from datetime import timedelta

from app import config
from app.analytics.geo import distance_km, kmh_to_knots
from app.analytics.identity import mmsi_problems, valid_imo

# "info" = worth knowing, not evidence of wrongdoing: never adds to a ship's risk score
SEVERITY_SCORE = {"info": 0.0, "low": 1.0, "medium": 3.0, "high": 6.0}
JITTER_KM = 2.0          # position noise below this is never called a jump


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _anomaly(kind, severity, mmsi, start, end, lat, lon, summary, lat2=None, lon2=None, key_extra="", **details):
    return {
        "dedupe_key": _key(kind, mmsi, start.replace(second=0, microsecond=0).isoformat(), key_extra),
        "kind": kind, "severity": severity, "score": SEVERITY_SCORE[severity], "mmsi": mmsi,
        "start_ts": start, "end_ts": end, "lat": lat, "lon": lon, "lat2": lat2, "lon2": lon2,
        "summary": summary, "details": details,
    }


def _bad_position(p) -> bool:
    return (p["lat"] is None or p["lon"] is None or abs(p["lat"]) > 90 or abs(p["lon"]) > 180
            or (abs(p["lat"]) < 1e-6 and abs(p["lon"]) < 1e-6))


def _hours(a, b) -> float:
    return (b["ts"] - a["ts"]).total_seconds() / 3600


def _norm(name) -> str:
    return "".join(str(name or "").upper().split())


# ---------------------------------------------------------------- per-ship detectors
def detect_position_invalid(pings, mmsi):
    bad = [p for p in pings if _bad_position(p)]
    if not bad:
        return []
    days = defaultdict(list)
    for p in bad:
        days[p["ts"].date()].append(p)
    return [_anomaly("position_invalid", "low", mmsi, ps[0]["ts"], ps[-1]["ts"], None, None,
                     f"{len(ps)} ping(s) with an invalid or 'not available' position",
                     key_extra=str(day), count=len(ps),
                     examples=[[p["lat"], p["lon"]] for p in ps[:5]])
            for day, ps in days.items()]


def detect_gaps(pings, mmsi, gap_hours=None):
    gap_hours = gap_hours or config.GAP_HOURS
    out = []
    for a, b in zip(pings, pings[1:]):
        h = _hours(a, b)
        if h <= gap_hours:
            continue
        km = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
        kn = kmh_to_knots(km / h)
        last_sog = a.get("sog")
        stopped = kn < 2 and (last_sog or 0) > 5 and h >= 12      # was sailing, went dark, barely moved
        impossible = kn > config.MAX_SPEED_KN
        severity = "high" if (h >= 24 or impossible or stopped) else "medium" if h >= 12 else "low"
        notes = []
        if impossible:
            notes.append(f"reappeared {km:,.0f} km away - faster than possible ({kn:.0f} kn): one position is fake")
        if stopped:
            notes.append("was under way, then barely moved while dark - possible ship-to-ship transfer")
        out.append(_anomaly(
            "ais_gap", severity, mmsi, a["ts"], b["ts"], a["lat"], a["lon"],
            f"AIS off for {h:.1f} h; reappeared {km:,.0f} km away" + (f" ({'; '.join(notes)})" if notes else ""),
            lat2=b["lat"], lon2=b["lon"], hours=round(h, 2), km_moved=round(km, 1),
            implied_speed_kn=round(kn, 1), last_sog_kn=last_sog, possible_sts=stopped,
            sources=sorted({a.get("source", ""), b.get("source", "")})))
    return out


def detect_on_off_cycling(pings, mmsi, min_gap_min=None, window_h=None, min_count=None):
    """Several short silences close together = transmitter being switched on and off."""
    min_gap_h = (min_gap_min or config.CYCLE_MIN_GAP_MIN) / 60
    window = timedelta(hours=window_h or config.CYCLE_WINDOW_HOURS)
    min_count = min_count or config.CYCLE_MIN_COUNT
    offs = [(a, b) for a, b in zip(pings, pings[1:]) if _hours(a, b) >= min_gap_h]
    out, i = [], 0
    while i < len(offs):
        j = i
        while j + 1 < len(offs) and offs[j + 1][0]["ts"] - offs[i][0]["ts"] <= window:
            j += 1
        group = offs[i:j + 1]
        if len(group) >= min_count:
            first, last = group[0][0], group[-1][1]
            dark_h = sum(_hours(a, b) for a, b in group)
            out.append(_anomaly(
                "on_off_cycling", "high" if len(group) >= 5 else "medium", mmsi, first["ts"], last["ts"],
                first["lat"], first["lon"],
                f"Transmitter went off and on {len(group)} times in "
                f"{(last['ts'] - first['ts']).total_seconds() / 3600:.0f} h ({dark_h:.1f} h dark in total)",
                lat2=last["lat"], lon2=last["lon"], count=len(group), total_dark_hours=round(dark_h, 2),
                cycles=[{"off": a["ts"].isoformat(), "on": b["ts"].isoformat(),
                         "minutes": round(_hours(a, b) * 60)} for a, b in group],
                sources=sorted({p.get("source", "") for g in group for p in g})))
            i = j + 1
        else:
            i += 1
    return out


def detect_jumps(pings, mmsi, max_kn=None):
    """Teleports, and A→B→A patterns (two transmitters, one MMSI)."""
    max_kn = max_kn or config.MAX_SPEED_KN
    out, collisions = [], defaultdict(list)
    n = len(pings)
    skip_next = False
    for i in range(1, n):
        if skip_next:
            skip_next = False
            continue
        a, b = pings[i - 1], pings[i]
        km = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
        h = max(_hours(a, b), 1 / 60)
        kn = kmh_to_knots(km / h)
        if km < JITTER_KM or kn <= max_kn:
            continue
        # Two EVENT records (GFW port visits etc.) that no ship could connect almost always mean the MMSI
        # was used by another vessel at the time (MMSIs are reassigned after re-flagging) and the record was
        # attributed to the wrong hull. Audit of the real data, 22 Nov 2026: all 14 such cases were this.
        # Reported as an identity question, worth 0 risk - not as spoofing.
        if a.get("source") in EVENT_SOURCES and b.get("source") in EVENT_SOURCES:
            out.append(_anomaly(
                "record_conflict", "info", mmsi, a["ts"], b["ts"], a["lat"], a["lon"],
                f"Two GFW records under MMSI {mmsi} can't both be this ship: {km:,.0f} km apart, "
                f"{h:.0f} h between them. The MMSI was probably also used by another vessel - "
                "an identity question, not evidence of spoofing",
                lat2=b["lat"], lon2=b["lon"], km=round(km, 1), hours=round(h, 1), implied_speed_kn=round(kn, 1),
                events=[a.get("nav_status"), b.get("nav_status")], sources=["gfw"]))
            continue
        # A -> B -> back near A soon after: a second transmitter, not one ship jumping
        if i + 1 < n:
            c = pings[i + 1]
            back_km = distance_km(a["lat"], a["lon"], c["lat"], c["lon"])
            back_h = max(_hours(a, c), 1 / 60)
            if back_km < km / 4 and kmh_to_knots(back_km / back_h) <= max_kn:
                collisions[b["ts"].date()].append((a, b, km))
                skip_next = True
                continue
        out.append(_anomaly(
            "teleport", "high", mmsi, a["ts"], b["ts"], a["lat"], a["lon"],
            f"Position jumped {km:,.0f} km in {h * 60:.0f} min ({kn:,.0f} kn) - one of these positions is fake",
            lat2=b["lat"], lon2=b["lon"], km=round(km, 1), minutes=round(h * 60, 1), implied_speed_kn=round(kn, 1),
            names=[a.get("name"), b.get("name")], sources=sorted({a.get("source", ""), b.get("source", "")})))
    for day, hits in collisions.items():
        if len(hits) < 2:                    # one outlier and back: a single fake position, not two transmitters
            a, b, km = hits[0]
            h = max(_hours(a, b), 1 / 60)
            out.append(_anomaly(
                "teleport", "high", mmsi, a["ts"], b["ts"], a["lat"], a["lon"],
                f"One position {km:,.0f} km off the track, then straight back - a spoofed / fake position",
                lat2=b["lat"], lon2=b["lon"], km=round(km, 1), minutes=round(h * 60, 1),
                implied_speed_kn=round(kmh_to_knots(km / h), 1), outlier=True))
            continue
        a, b, km = hits[0]
        out.append(_anomaly(
            "mmsi_collision", "high", mmsi, hits[0][0]["ts"], hits[-1][1]["ts"], a["lat"], a["lon"],
            f"Two transmitters are using MMSI {mmsi}: track flipped between places ~{km:,.0f} km apart "
            f"{len(hits)} time(s)", lat2=b["lat"], lon2=b["lon"], key_extra=str(day), count=len(hits),
            names=sorted({x.get("name") or "" for h_ in hits for x in h_[:2]})))
    return out


def detect_speed_mismatch(pings, mmsi, tol_kn=None):
    tol_kn = tol_kn or config.SPEED_MISMATCH_KN
    days = defaultdict(list)
    for a, b in zip(pings, pings[1:]):
        h = _hours(a, b)
        if not (10 / 60 <= h <= 3) or a.get("sog") is None or b.get("sog") is None:
            continue
        if a["sog"] >= 102 or b["sog"] >= 102:          # 102.3 = "not available"
            continue
        km = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
        calc = kmh_to_knots(km / h)
        if calc > config.MAX_SPEED_KN:       # already reported as a teleport / collision
            continue
        reported = (a["sog"] + b["sog"]) / 2
        if abs(calc - reported) > tol_kn and (km > JITTER_KM or reported > tol_kn):
            days[a["ts"].date()].append((a, b, reported, calc))
    out = []
    for day, hits in days.items():
        worst = max(hits, key=lambda x: abs(x[3] - x[2]))
        a, b, rep, calc = worst
        out.append(_anomaly(
            "speed_mismatch", "medium" if len(hits) >= 3 else "low", mmsi, hits[0][0]["ts"], hits[-1][1]["ts"],
            a["lat"], a["lon"],
            f"Reported speed doesn't match movement {len(hits)} time(s); worst: reports {rep:.1f} kn, "
            f"actually moved at {calc:.1f} kn", key_extra=str(day), count=len(hits),
            worst_reported_kn=round(rep, 1), worst_calculated_kn=round(calc, 1)))
    return out


def detect_identity_changes(pings, mmsi):
    out, prev = [], None
    for p in pings:
        ident = {"name": _norm(p.get("name")), "imo": str(p.get("imo") or ""), "callsign": _norm(p.get("callsign"))}
        shown = {"name": (p.get("name") or "").strip(), "imo": ident["imo"], "callsign": (p.get("callsign") or "").strip()}
        if prev is not None:
            changed = [k for k in ident if ident[k] and prev[0][k] and ident[k] != prev[0][k]]
            if changed:
                sev = "high" if "imo" in changed else "medium" if "name" in changed else "low"
                desc = ", ".join(f"{k}: {prev[2][k]} → {shown[k]}" for k in changed)
                out.append(_anomaly("identity_change", sev, mmsi, prev[1]["ts"], p["ts"], p["lat"], p["lon"],
                                    f"Identity changed under MMSI {mmsi} ({desc})",
                                    key_extra="|".join(changed), changed=changed,
                                    before={k: prev[2][k] for k in changed}, after={k: shown[k] for k in changed}))
        # carry forward fields not present in this ping (position reports have no name/IMO)
        merged = {k: ident[k] or (prev[0][k] if prev else "") for k in ident}
        merged_shown = {k: shown[k] or (prev[2][k] if prev else "") for k in shown}
        prev = (merged, p, merged_shown)
    return out


def detect_invalid_ids(pings, mmsi, reported_flag=None):
    out = []
    first = pings[0] if pings else None
    if first is None:
        return out
    for problem in mmsi_problems(mmsi, reported_flag):
        out.append(_anomaly("invalid_id", "medium", mmsi, first["ts"], None, first["lat"], first["lon"],
                            problem, key_extra=problem))
    for imo in sorted({str(p.get("imo")) for p in pings if p.get("imo")}):
        if not valid_imo(imo):
            p = next(p for p in pings if str(p.get("imo")) == imo)
            out.append(_anomaly("invalid_id", "medium", mmsi, p["ts"], None, p["lat"], p["lon"],
                                f"IMO {imo} fails the IMO check digit - not a real IMO number",
                                key_extra=f"imo{imo}", imo=imo))
    return out


# Sources that deliver EVENTS (port visits, gap start/end), not a continuous AIS stream. Silence between
# their records means nothing, so gap / on-off / speed detectors never run on them.
EVENT_SOURCES = {"gfw"}


def detect_for_vessel(pings, mmsi, reported_flag=None) -> list[dict]:
    """Run every per-ship detector. `pings` = this MMSI's pings (any order)."""
    pings = sorted(pings, key=lambda p: p["ts"])
    good = [p for p in pings if not _bad_position(p)]
    stream = [p for p in good if p.get("source") not in EVENT_SOURCES]
    return (detect_position_invalid(pings, mmsi) + detect_gaps(stream, mmsi) +
            detect_on_off_cycling(stream, mmsi) + detect_jumps(good, mmsi) +
            detect_speed_mismatch(stream, mmsi) + detect_identity_changes(pings, mmsi) +
            detect_invalid_ids(pings, mmsi, reported_flag))


# ---------------------------------------------------------------- cross-ship detector
def detect_imo_clones(pings, window_h=1.0, min_km=50.0) -> list[dict]:
    """Same IMO, different MMSIs, far apart at (nearly) the same time = one identity on two hulls."""
    by_imo = defaultdict(list)
    for p in pings:
        if p.get("imo") and not _bad_position(p):
            by_imo[str(p["imo"])].append(p)
    out = []
    for imo, ps in by_imo.items():
        if len({p["mmsi"] for p in ps}) < 2:
            continue
        ps.sort(key=lambda p: p["ts"])
        seen = set()
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                if _hours(a, b) > window_h:
                    break
                if a["mmsi"] == b["mmsi"]:
                    continue
                km = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
                h = max(_hours(a, b), 1 / 60)
                pair = tuple(sorted([a["mmsi"], b["mmsi"]]))
                if km >= min_km and kmh_to_knots(km / h) > config.MAX_SPEED_KN and (pair, a["ts"].date()) not in seen:
                    seen.add((pair, a["ts"].date()))
                    out.append(_anomaly(
                        "imo_clone", "high", a["mmsi"], a["ts"], b["ts"], a["lat"], a["lon"],
                        f"IMO {imo} broadcast by MMSI {a['mmsi']} ({a.get('name')}) and MMSI {b['mmsi']} "
                        f"({b.get('name')}) {km:,.0f} km apart within {h * 60:.0f} min",
                        lat2=b["lat"], lon2=b["lon"], key_extra=f"{imo}|{pair}|{a['ts'].date()}",
                        imo=imo, mmsis=list(pair), km=round(km, 1)))
    return out


def risk_score(anomalies, now, recent_days=90) -> float:
    """Vessel risk = sum of anomaly scores; anything from the last 90 days counts double."""
    total = 0.0
    for a in anomalies:
        if a.get("status") == "dismissed":
            continue
        w = 2.0 if a["start_ts"] and (now - a["start_ts"]).days <= recent_days else 1.0
        total += a["score"] * w
    return round(total, 1)
