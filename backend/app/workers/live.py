"""
Live ingest worker - run beside the API server:

    python -m app.workers.live --feed aisstream     (needs AISSTREAM_API_KEY)
    python -m app.workers.live --feed norway        (needs NORWAY_AIS_ENABLED=true)

Pings are stored in batches; spoofing detection re-runs for each batch's ships.
"""
import argparse
import asyncio
import logging

from app.db import SessionLocal, init_db
from app.services.ingest import store_pings

log = logging.getLogger("live")


def store(batch):
    with SessionLocal() as s:
        res = store_pings(s, batch)
    log.info("stored %s", res)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", choices=["aisstream", "norway"], required=True)
    args = ap.parse_args()
    init_db()
    if args.feed == "aisstream":
        from app.ingest import aisstream
        asyncio.run(aisstream.run(store))
    else:
        from app.ingest import norway_ais
        norway_ais.run(store)


if __name__ == "__main__":
    main()
