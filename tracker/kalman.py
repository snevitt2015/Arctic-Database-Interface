"""
kalman.py  -  Kalman filter: one best estimate of where a ship is, how fast it's going, and
how sure we are - updated every time ANY source reports it (AIS, GFW events, Airtable checks,
later SAR and optical detections).

Why it helps
  * Every report has its own accuracy (an AIS fix: ~0.5 km; an area name like "Barents Sea":
    hundreds of km; a Sentinel-1 detection: ~50 m). The filter weights each one by that.
  * Between reports the uncertainty GROWS in a realistic way (faster when we don't know the
    ship's speed), which gives the search area for satellite imagery when AIS is off.
  * Each new report is checked against where the filter expected the ship. A report far
    outside the expected area ("innovation" test) is a possible spoofed position.

The model ("constant velocity")
  state  = position east/north (km) + velocity east/north (km/h)
  predict: position moves with the velocity; the velocity slowly "forgets" itself (a ship
           doesn't hold course for days without us knowing), and uncertainty grows with time
  update : blend in a reported position (and speed/course when a source gives one).
           Port visits and loitering also tell us the ship is (nearly) stopped.
To work anywhere - including near the pole - the filter keeps its maths in a small flat
east/north frame centred on its current estimate and re-centres after every step.

Use
    kf = kalman_track(track)          # one row per report: filtered position, speed, sigma, flags
    kalman_predict(track)             # future positions, same format as prediction.py
    kalman_position_at(track, when)   # estimate + uncertainty at a satellite pass time
"""
import math

import numpy as np
import pandas as pd

import config
from tracker.geo import bearing_deg, distance_km, move_point, kmh_to_knots

SPEED_SIGMA_KMH = 17.0           # typical speed per east/north axis (~ 13 kn cruising overall)
TAU_H = 48.0                     # how long a ship keeps its speed and course before we "forget" it
                                 # (both calibrated on our GFW event data - port visits, loitering
                                 #  days apart, no speed given: see calibrate() at the bottom)
TAU_AIS_H = 3000.0               # when reports carry real AIS speed + course (MarineTraffic /
                                 # TankerMap exports), ships are seen holding course: ~ +-20 km
                                 # drift over 24 h. Starting value - re-run calibrate() on such data.
MEAS_ERROR_KM = 0.5              # default position error when a source doesn't say
SPEED_ERROR_KMH = 1.0            # error of a reported speed (AIS SOG)
STOPPED_ERROR_KMH = 2.0          # port visit / loitering: speed ~0 within this
MAX_SPEED_KMH = 25 * 1.852       # never let the filter believe a ship does more than 25 kn
GATE_NIS = 13.8                  # chi-square, 2 d.o.f., 99.9 %: beyond this a report is "unexpected"
MAX_GAP_H = 14 * 24             # silent longer than this: start the track afresh (no test)
MIN_ANOMALY_KM = 25.0            # never call a report unexpected if it's closer than this
                                 # (port / event positions wander a few km)
RESET_AFTER_REJECTS = 2          # this many unexpected reports in a row -> restart the track there
RADIUS_95 = math.sqrt(5.991)     # 2-D 95 % radius in standard deviations
STOPPED_EVENTS = ("port_visit", "loitering")


def _offset_km(lat0, lon0, lat, lon):
    """Point (lat, lon) as east/north km from (lat0, lon0)."""
    d = distance_km(lat0, lon0, lat, lon)
    if d == 0:
        return np.zeros(2)
    b = math.radians(bearing_deg(lat0, lon0, lat, lon))
    return np.array([d * math.sin(b), d * math.cos(b)])


def _move(lat, lon, east, north):
    d = math.hypot(east, north)
    if d == 0:
        return lat, lon
    return move_point(lat, lon, (math.degrees(math.atan2(east, north)) + 360) % 360, d)


class ShipKalman:
    def __init__(self, lat, lon, time, pos_sigma_km=MEAS_ERROR_KM, speed_sigma=None, tau_h=None):
        self.lat, self.lon, self.time = float(lat), float(lon), pd.Timestamp(time)
        self.v = np.zeros(2)                                   # east, north km/h
        self.s2 = (speed_sigma or SPEED_SIGMA_KMH) ** 2
        self.tau_events = tau_h or TAU_H
        self.tau = self.tau_events
        self.has_ais_speed = False          # last report gave a real speed + course
        self.P = np.diag([pos_sigma_km ** 2, pos_sigma_km ** 2, self.s2, self.s2])

    # ---- predict
    def predicted(self, time):
        """(lat, lon, v, P) at `time` without changing the filter.
        Velocity model (integrated Ornstein-Uhlenbeck, per axis): a ship's velocity drifts
        toward 0 with time constant TAU_H and stays within about +-SPEED_SIGMA_KMH. Short gaps
        behave like dead reckoning; for long gaps the estimate stops "flying off" in the last
        direction and the uncertainty grows like a random walk - which is what sparse GFW
        events (port visits days apart) need."""
        dt = max(0.0, (pd.Timestamp(time) - self.time).total_seconds() / 3600)
        tau = TAU_AIS_H if self.has_ais_speed else self.tau_events
        s2 = self.s2
        e = math.exp(-dt / tau)
        F1 = np.array([[1.0, tau * (1 - e)], [0.0, e]])
        Q1 = s2 * np.array([[tau ** 2 * (2 * dt / tau - 3 + 4 * e - e * e), tau * (1 - e) ** 2],
                            [tau * (1 - e) ** 2, 1 - e * e]])
        F, Q = np.zeros((4, 4)), np.zeros((4, 4))
        for a, b in ((0, 2), (1, 3)):
            F[np.ix_([a, b], [a, b])] = F1
            Q[np.ix_([a, b], [a, b])] = Q1
        v = self.v * e
        lat, lon = _move(self.lat, self.lon, *(self.v * tau * (1 - e)))
        return lat, lon, v, F @ self.P @ F.T + Q

    def predict(self, time):
        self.lat, self.lon, self.v, self.P = self.predicted(time)
        self.time = pd.Timestamp(time)

    # ---- update
    def check(self, lat, lon, pos_sigma_km):
        """Innovation test without updating: (nis, distance_km) of a report vs the prediction."""
        z = _offset_km(self.lat, self.lon, lat, lon)
        S = self.P[:2, :2] + np.eye(2) * pos_sigma_km ** 2
        return float(z @ np.linalg.solve(S, z)), float(np.hypot(*z))

    def update(self, lat=None, lon=None, pos_sigma_km=MEAS_ERROR_KM, vel=None, vel_sigma=None):
        """Blend in a position (and/or velocity east/north km/h). Call predict(time) first."""
        H, z, R = [], [], []
        if lat is not None:
            off = _offset_km(self.lat, self.lon, lat, lon)
            H += [[1, 0, 0, 0], [0, 1, 0, 0]]; z += list(off); R += [pos_sigma_km ** 2] * 2
        if vel is not None:
            H += [[0, 0, 1, 0], [0, 0, 0, 1]]; z += list(np.asarray(vel) - self.v); R += [vel_sigma ** 2] * 2
        if not H:
            return
        H, z, R = np.array(H, float), np.array(z, float), np.diag(R)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        dx = K @ z                                             # state is relative to current estimate
        I_KH = np.eye(4) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T          # Joseph form: stays symmetric
        self.lat, self.lon = _move(self.lat, self.lon, dx[0], dx[1])
        self.v = self.v + dx[2:]
        sp = float(np.hypot(*self.v))
        if sp > MAX_SPEED_KMH:
            self.v *= MAX_SPEED_KMH / sp

    # ---- read out
    def speed_kn(self):
        return kmh_to_knots(float(np.hypot(*self.v)))

    def course(self):
        return (math.degrees(math.atan2(self.v[0], self.v[1])) + 360) % 360

    @staticmethod
    def radius95(P):
        return RADIUS_95 * math.sqrt(max(np.linalg.eigvalsh(P[:2, :2]).max(), 0.0))


def _report_sigma(r):
    v = r.get("pos_error_km")
    return max(MEAS_ERROR_KM, float(v)) if v is not None and pd.notna(v) else MEAS_ERROR_KM


def _velocity_measurement(r):
    """(vel_east_north_kmh, sigma) from a report, or (None, None). Real AIS speed + course
    switches the filter to its 'AIS' setting (see TAU_AIS_H) until a report without them."""
    sp, co = r.get("speed_kn"), r.get("course")
    if sp is not None and pd.notna(sp) and co is not None and pd.notna(co):
        kmh, c = float(sp) * 1.852, math.radians(float(co))
        return np.array([kmh * math.sin(c), kmh * math.cos(c)]), SPEED_ERROR_KMH
    if str(r.get("event_type", "")) in STOPPED_EVENTS:
        return np.zeros(2), STOPPED_ERROR_KMH
    return None, None


def kalman_track(track: pd.DataFrame, speed_sigma=None, tau_h=None, return_filter=False):
    """Run the filter over one ship's reports (oldest first). One output row per report:
        kf_lat, kf_lon, kf_speed_kn, kf_course, kf_sigma_km (95 % radius after the update),
        kf_expected_km (95 % radius BEFORE it - how far off a report could reasonably be),
        kf_miss_km (how far the report was from the prediction), kf_nis,
        kf_unexpected (report outside the 99.9 % area -> not used; possible spoof),
        kf_reset (track restarted here: the ship really was somewhere else)."""
    t = track.dropna(subset=["lat", "lon", "timestamp"]).sort_values("timestamp")
    rows, kf, rejects = [], None, 0
    for idx, r in t.iterrows():
        sig = _report_sigma(r)
        vel, vsig = _velocity_measurement(r)
        ais_speed = vel is not None and vsig == SPEED_ERROR_KMH
        out = {"index": idx}
        if kf is None:
            kf = ShipKalman(r["lat"], r["lon"], r["timestamp"], sig, speed_sigma, tau_h)
            if vel is not None:
                kf.update(vel=vel, vel_sigma=vsig)
            out.update(kf_nis=0.0, kf_miss_km=0.0, kf_expected_km=None, kf_unexpected=False, kf_reset=True)
        elif (r["timestamp"] - kf.time).total_seconds() / 3600 > MAX_GAP_H:
            kf = ShipKalman(r["lat"], r["lon"], r["timestamp"], sig, speed_sigma, tau_h)
            if vel is not None:
                kf.update(vel=vel, vel_sigma=vsig)
            rejects = 0
            out.update(kf_nis=None, kf_miss_km=None, kf_expected_km=None, kf_unexpected=False, kf_reset=True)
        else:
            kf.predict(r["timestamp"])
            nis, miss = kf.check(r["lat"], r["lon"], sig)
            expected = ShipKalman.radius95(kf.P + np.diag([sig ** 2] * 2 + [0, 0]))
            unexpected = nis > GATE_NIS and miss > MIN_ANOMALY_KM and sig <= config.COARSE_POSITION_KM
            reset = False
            if unexpected:
                rejects += 1
                if rejects >= RESET_AFTER_REJECTS:      # two odd reports in a row agree: ship moved
                    kf = ShipKalman(r["lat"], r["lon"], r["timestamp"], sig, speed_sigma, tau_h)
                    reset, unexpected, rejects = True, False, 0
            else:
                rejects = 0
                kf.update(r["lat"], r["lon"], sig, vel, vsig)
            out.update(kf_nis=round(nis, 2), kf_miss_km=round(miss, 1), kf_expected_km=round(expected, 1),
                       kf_unexpected=unexpected, kf_reset=reset)
        kf.has_ais_speed = ais_speed
        out.update(kf_lat=round(kf.lat, 5), kf_lon=round(kf.lon, 5), kf_speed_kn=round(kf.speed_kn(), 2),
                   kf_course=round(kf.course(), 1), kf_sigma_km=round(ShipKalman.radius95(kf.P), 2))
        rows.append(out)
    res = pd.DataFrame(rows).set_index("index") if rows else pd.DataFrame()
    if len(res):
        res["kf_unexpected"] = res["kf_unexpected"].astype(bool)
    return (res, kf) if return_filter else res


def kalman_predict(track: pd.DataFrame, hours=None, step=None) -> pd.DataFrame:
    """Future positions with a 95 % uncertainty radius (same columns as prediction.py)."""
    hours = hours or config.PREDICT_HOURS
    step = step or config.PREDICT_STEP_HOURS
    t = track.sort_values("timestamp")
    if "pos_error_km" in t:
        t = t[t["pos_error_km"].fillna(0.5) <= config.COARSE_POSITION_KM]
    if len(t) < 2:
        return pd.DataFrame()
    res, kf = kalman_track(t, return_filter=True)
    if kf is None:
        return pd.DataFrame()
    last_event = str(t.iloc[-1].get("event_type", ""))
    rows = []
    for h in range(step, hours + 1, step):
        when = kf.time + pd.Timedelta(hours=h)
        lat, lon, v, P = kf.predicted(when)
        if last_event == "port_visit" and float(np.hypot(*kf.v)) < 1.0:
            lat, lon = kf.lat, kf.lon               # in port: stays put (uncertainty still grows)
        reach = MAX_SPEED_KMH * h + 2               # can't be further than a fast ship could sail
        rows.append({"method": "kalman", "hours_ahead": h, "timestamp": when, "lat": lat, "lon": lon,
                     "uncertainty_km": round(min(ShipKalman.radius95(P), reach), 1)})
    return pd.DataFrame(rows)


def kalman_position_at(track: pd.DataFrame, when) -> dict:
    """Estimate at an exact moment (e.g. a satellite pass) using every report BEFORE it."""
    when = pd.Timestamp(when)
    when = when.tz_localize("UTC") if when.tzinfo is None else when.tz_convert("UTC")
    before = track[track["timestamp"] <= when]
    if before.empty:
        return {"time": when, "lat": None, "lon": None, "method": "none", "uncertainty_km": None}
    _, kf = kalman_track(before, return_filter=True)
    lat, lon, v, P = kf.predicted(when)
    hours = (when - kf.time).total_seconds() / 3600
    return {"time": when, "lat": lat, "lon": lon, "method": "kalman",
            "uncertainty_km": round(min(ShipKalman.radius95(P), MAX_SPEED_KMH * hours + 2), 1),
            "speed_kn": round(kmh_to_knots(float(np.hypot(*v))), 1), "hours_since_report": round(hours, 1)}


def add_kalman_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Run the filter per watch-list ship and add its columns to the positions table."""
    cols = ["kf_lat", "kf_lon", "kf_speed_kn", "kf_course", "kf_sigma_km", "kf_expected_km",
            "kf_miss_km", "kf_nis", "kf_unexpected", "kf_reset"]
    df = df.copy()
    for c in cols:
        df[c] = np.nan
    df["kf_unexpected"] = False
    df["kf_reset"] = False
    for vessel, g in df[df["vessel"] != ""].groupby("vessel"):
        res = kalman_track(g)
        if len(res):
            df.loc[res.index, cols] = res[cols].values
    df["kf_unexpected"] = df["kf_unexpected"].fillna(False).astype(bool)
    df["kf_reset"] = df["kf_reset"].fillna(False).astype(bool)
    return df


def calibrate(df: pd.DataFrame, speed_sigma=None, tau_h=None) -> dict:
    """How honest is the filter on real data? For every fine-resolution report: was it inside
    the 95 % area the filter predicted? Aim: ~95 % inside, and few 'unexpected' reports."""
    tot = inside = unexpected = 0
    for _, g in df[df["vessel"] != ""].groupby("vessel"):
        res = kalman_track(g, speed_sigma, tau_h)
        r = res[res["kf_expected_km"].notna() & ~res["kf_reset"]]
        tot += len(r); inside += int((r["kf_miss_km"] <= r["kf_expected_km"]).sum())
        unexpected += int(res["kf_unexpected"].sum())
    return {"reports": tot, "inside_95_pct": round(100 * inside / tot, 1) if tot else None,
            "unexpected": unexpected}
