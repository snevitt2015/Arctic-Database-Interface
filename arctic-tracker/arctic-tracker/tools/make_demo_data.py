"""
make_demo_data.py  -  Create SYNTHETIC (fake) positions to test the code.

!!! THIS IS NOT REAL INTELLIGENCE. !!!
It writes to data/demo_positions.csv (a separate file), every row is
labelled source = "DEMO (synthetic)", and the app only shows it when you
tick "Show demo data". Use it to check the map, features and predictions
work before you have real OSINT data.

    python tools/make_demo_data.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DEMO_FILE
from tracker.excel_loader import load_vessels
from tracker.geo import move_point, bearing_deg, distance_km, knots_to_kmh

# Fake voyages between approximate Arctic waypoints
ROUTES = {
    "Murmansk -> Sabetta": [(69.10, 33.40), (70.30, 40.00), (70.70, 52.00), (72.00, 62.00), (73.00, 70.00), (71.27, 72.07)],
    "Sabetta -> Murmansk": [(71.27, 72.07), (73.00, 70.00), (72.00, 62.00), (70.70, 52.00), (70.30, 40.00), (69.10, 33.40)],
    "Arkhangelsk -> Barents Sea": [(64.60, 40.40), (65.50, 39.50), (66.80, 41.50), (68.50, 43.50), (70.00, 45.00)],
}


def sail(route, start, speed_kn, every_h, rng, dark_from=None, dark_hours=0):
    """Move along waypoints at roughly `speed_kn`, reporting every `every_h` hours."""
    rows, t = [], start
    lat, lon = route[0]
    for wlat, wlon in route[1:]:
        while distance_km(lat, lon, wlat, wlon) > 5:
            course = bearing_deg(lat, lon, wlat, wlon)
            spd = max(1, speed_kn + rng.normal(0, 0.7))
            step = min(knots_to_kmh(spd) * every_h, distance_km(lat, lon, wlat, wlon))
            lat, lon = move_point(lat, lon, course, step)
            t += pd.Timedelta(hours=every_h)
            silent = dark_from is not None and dark_from <= t < dark_from + pd.Timedelta(hours=dark_hours)
            if not silent:
                actual_kn = step / every_h / 1.852   # report the speed actually sailed
                rows.append({"timestamp": t, "lat": lat, "lon": lon, "speed_kn": round(actual_kn, 1),
                             "course": round(course), "heading": round((course + rng.normal(0, 2)) % 360)})
    return rows


def main():
    rng = np.random.default_rng(42)
    vessels = load_vessels()
    now = pd.Timestamp("2026-03-01", tz="UTC")
    out = []
    for i, (_, v) in enumerate(vessels.head(6).iterrows()):
        route = list(ROUTES.values())[i % len(ROUTES)]
        start = now - pd.Timedelta(days=int(rng.integers(3, 20)))
        dark = start + pd.Timedelta(hours=30) if i % 2 == 0 else None   # every other ship goes dark once
        for r in sail(route, start, speed_kn=rng.uniform(10, 14), every_h=3, rng=rng,
                      dark_from=dark, dark_hours=18):
            out.append({**r, "imo": v["imo"], "mmsi": v["mmsi"], "name": v["name"],
                        "source": "DEMO (synthetic)", "event_type": "position", "note": "SYNTHETIC TEST DATA"})
    pd.DataFrame(out).to_csv(DEMO_FILE, index=False)
    print(f"Wrote {len(out)} synthetic rows to {DEMO_FILE}")


if __name__ == "__main__":
    main()
