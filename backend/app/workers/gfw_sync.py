"""
Global Fishing Watch sync for the watch list (run daily, e.g. from the GitHub Action):

    python -m app.workers.gfw_sync [--since 2024-01-01]
"""
import argparse
import logging

from sqlalchemy import select

from app import config
from app.db import SessionLocal, init_db
from app.ingest import gfw
from app.models import Vessel
from app.services.ingest import store_pings, upsert_anomalies

log = logging.getLogger("gfw_sync")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2012-01-01")
    args = ap.parse_args()
    if not config.GFW_API_TOKEN:
        raise SystemExit("GFW_API_TOKEN is not set")
    init_db()
    with SessionLocal() as s:
        for v in s.scalars(select(Vessel).where(Vessel.watchlist)).all():
            try:
                ids = gfw.find_ids(v.imo, v.mmsi, v.name)
                if not ids:
                    log.info("%s: not found in GFW", v.name)
                    continue
                pings, anomalies = gfw.to_records(gfw.fetch_events(ids, args.since), imo=v.imo, fallback_mmsi=v.mmsi)
                res = store_pings(s, pings, detect=False)
                n = upsert_anomalies(s, anomalies, {a["mmsi"]: v.id for a in anomalies})
                s.commit()
                log.info("%s: %s, %d GFW events", v.name, res, n)
            except Exception as e:
                s.rollback()
                log.warning("%s: %s", v.name, e)


if __name__ == "__main__":
    main()
