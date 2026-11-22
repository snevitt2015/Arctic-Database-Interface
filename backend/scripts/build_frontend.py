"""
build_frontend.py  -  Build the interface page.

    python scripts/build_frontend.py                      -> app/static/index.html (served at http://localhost:8000/)
    python scripts/build_frontend.py --preview out.html   -> also a stand-alone preview with a snapshot of the
                                                             REAL data in arctic.db (for sharing without a server)

The page itself is frontend/index.template.html. The coastline is Natural Earth 1:50m (world-atlas, public domain),
packed into the page so the map works with no tile server.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FRONT = ROOT / "frontend"


def page(snapshot=None) -> str:
    html = (FRONT / "index.template.html").read_text(encoding="utf-8")
    topo = (FRONT / "vendor" / "topojson-client.min.js").read_text(encoding="utf-8")
    land = (FRONT / "vendor" / "land-50m.json").read_text(encoding="utf-8")
    html = html.replace("/*__TOPOJSON__*/", topo).replace("/*__LAND__*/null", land)
    snap = "null" if snapshot is None else json.dumps(snapshot, separators=(",", ":")).replace("</", "<\\/")
    return html.replace("/*__SNAPSHOT__*/null", snap)


def snapshot() -> dict:
    """Every API answer the page asks for, taken from the real database."""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    out = {}

    def get(p):
        r = c.get(p)
        r.raise_for_status()
        out[p] = r.json()
        return out[p]

    for p in ["/api/overview", "/api/overview/fleet", "/api/spoofing/anomalies?limit=500", "/api/spoofing/kinds",
              "/api/sar/detections", "/api/vessels?watchlist=true", "/api/data/references", "/api/data/sources",
              "/api/data/checks"]:
        get(p)
    ids = {f["properties"]["vessel_id"] for f in out["/api/overview/fleet"]["features"]}
    ids |= {v["id"] for v in out["/api/vessels?watchlist=true"]}
    ids |= {a["vessel_id"] for a in out["/api/spoofing/anomalies?limit=500"] if a["vessel_id"]}
    ids |= {t["vessel_id"] for t in out["/api/overview"]["top_risk"]}
    for i in sorted(ids):
        get(f"/api/vessels/{i}")
        get(f"/api/vessels/{i}/track?days=180")
    for a in out["/api/spoofing/anomalies?limit=500"]:
        get(f"/api/spoofing/anomalies/{a['id']}")
    for d in out["/api/sar/detections"]:
        get(f"/api/sar/detections/{d['detection_id']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="also write a stand-alone preview with a real-data snapshot")
    ap.add_argument("--site", help="write ONLY the stand-alone snapshot page here (for GitHub Pages), nothing else")
    args = ap.parse_args()
    if args.site:
        snap = snapshot()
        Path(args.site).write_text(page(snap), encoding="utf-8")
        print(f"wrote {args.site} ({len(snap)} API answers from arctic.db)")
        return
    (ROOT / "app" / "static").mkdir(exist_ok=True)
    (ROOT / "app" / "static" / "index.html").write_text(page(), encoding="utf-8")
    print("wrote app/static/index.html")
    if args.preview:
        snap = snapshot()
        Path(args.preview).write_text(page(snap), encoding="utf-8")
        print(f"wrote {args.preview} ({len(snap)} API answers from arctic.db)")


if __name__ == "__main__":
    main()
