"""
make_sea_routes.py  -  Build data/sea_routes.geojson (Arctic shipping routes for the web map).

SCHEMATIC ONLY: hand-placed waypoints through the well-known straits, capes and
seas each route uses. Good for orientation on the map - NOT for navigation and
not an exact copy of any published chart. Edit the waypoints below and rerun:

    python tools/make_sea_routes.py

Coordinates are (longitude, latitude). East of 180° is written as >180
(e.g. Bering Strait = 190.3) so lines cross the date line cleanly.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "sea_routes.geojson"

ROUTES = [
    {
        "id": "nsr", "name": "Northern Sea Route", "color": "#ec4899",
        "about": "Russia's legally defined Arctic route, Kara Gate / Novaya Zemlya to the Bering Strait. "
                 "Administered by Rosatom; ships need permission and often icebreaker escort.",
        "points": [(33.1, 69.2), (38.0, 70.0), (45.0, 70.2), (52.0, 70.3), (57.7, 70.45),   # Murmansk -> Kara Gate
                   (63.0, 71.3), (70.0, 72.8), (76.0, 74.5), (86.0, 76.0), (96.0, 77.3),
                   (104.5, 77.85),                                                         # Vilkitsky Strait
                   (113.0, 76.8), (122.0, 75.8), (130.0, 75.0), (138.5, 74.75),            # Laptev Sea
                   (142.0, 74.6),                                                          # Sannikov Strait area
                   (152.0, 73.2), (162.0, 70.9), (170.3, 70.1),                            # East Siberian Sea / Pevek
                   (178.5, 69.95), (183.5, 68.8), (187.5, 67.0), (190.3, 66.0)],           # Long Strait -> Bering Strait
    },
    {
        "id": "nep", "name": "North-East Passage", "color": "#ef4444",
        "about": "The full passage from the North Atlantic along the Russian Arctic to the Pacific, "
                 "here shown on its northern variant around Novaya Zemlya.",
        "points": [(4.0, 61.0), (8.0, 64.5), (13.0, 68.5), (19.0, 70.6), (26.0, 71.6), (35.0, 72.5),
                   (45.0, 74.5), (55.0, 76.5), (68.6, 77.2),                               # Cape Zhelaniya
                   (78.0, 77.8), (90.0, 78.5), (104.5, 78.2), (115.0, 77.6),
                   (128.0, 77.4), (140.0, 77.0), (150.0, 76.0),                            # north of New Siberian Islands
                   (160.0, 73.8), (170.0, 71.8), (180.0, 70.0), (186.0, 68.0), (190.3, 66.0),
                   (192.5, 64.5), (195.0, 62.0)],
    },
    {
        "id": "nwp", "name": "North-West Passage", "color": "#16a34a",
        "about": "Through the Canadian Arctic Archipelago between the Beaufort Sea and Baffin Bay.",
        "points": [(191.5, 65.8), (193.0, 68.9), (203.5, 71.6),                            # Bering Strait -> Point Barrow
                   (215.0, 70.6), (225.0, 70.0), (236.0, 70.9),                            # Beaufort Sea, Amundsen Gulf
                   (245.0, 69.0), (254.0, 68.6), (259.5, 69.2),                            # Dolphin & Union, Victoria Strait
                   (263.5, 71.5), (266.5, 73.9), (270.0, 74.3),                            # Franklin/Peel -> Barrow Strait
                   (278.0, 74.2), (285.0, 73.8),                                           # Lancaster Sound
                   (293.0, 72.0), (300.0, 68.0), (303.0, 64.0), (305.0, 60.0)],            # Baffin Bay -> Davis Strait
    },
    {
        "id": "tsr", "name": "Transpolar route (future)", "color": "#0ea5e9",
        "about": "A possible future route straight across the central Arctic Ocean if summer ice keeps shrinking.",
        "points": [(190.3, 66.0), (190.0, 70.0), (189.5, 75.0), (189.0, 80.0), (189.0, 84.0),
                   (189.0, 87.5), (370.0, 87.5),                                           # over the pole (drawn near 87.5°N)
                   # keep adding 360 after the pole so the line doesn't jump back across the map
                   (370.0, 84.0), (365.0, 81.0), (360.0, 78.8),                            # Fram Strait (0°E)
                   (355.0, 75.0), (352.0, 71.0), (350.0, 67.0)],
    },
    {
        "id": "rivers", "name": "Russian river & gulf feeder lines", "color": "#9ca3af",
        "about": "Short links from Arctic coast routes to river ports - the Gulf of Ob (Sabetta), Yenisei (Dudinka), Lena (Tiksi).",
        "multi": [
            [(72.07, 71.27), (73.0, 72.5), (73.5, 73.5)],                                   # Sabetta -> Kara Sea
            [(86.18, 69.41), (84.5, 70.8), (82.5, 72.0), (80.7, 73.4)],                     # Dudinka -> Yenisei Gulf
            [(128.87, 71.64), (129.5, 72.6), (130.0, 74.5)],                                # Tiksi -> Laptev Sea
            [(40.52, 64.54), (39.5, 65.5), (41.0, 66.6), (42.5, 68.0)],                     # Arkhangelsk -> White Sea mouth
        ],
    },
]


def arctic_circle():
    lat = 66.5634                      # latitude of the Arctic Circle (2025-2030 average)
    return [(lon, lat) for lon in range(-180, 181, 2)]


def main():
    features = []
    for r in ROUTES:
        props = {k: r[k] for k in ("id", "name", "color", "about")}
        if "multi" in r:
            geom = {"type": "MultiLineString", "coordinates": [[list(p) for p in line] for line in r["multi"]]}
        else:
            geom = {"type": "LineString", "coordinates": [list(p) for p in r["points"]]}
        features.append({"type": "Feature", "properties": props, "geometry": geom})
    features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(p) for p in arctic_circle()]},
                     "properties": {"id": "circle", "name": "Arctic Circle (66.56°N)", "color": "#93c5fd",
                                    "about": "Above this line the sun stays up for at least one full day in summer."}})
    OUT.write_text(json.dumps({"type": "FeatureCollection",
                               "note": "Schematic routes for orientation only - not for navigation.",
                               "features": features}, indent=1))
    print(f"Wrote {len(features)} features to {OUT}")


if __name__ == "__main__":
    main()
