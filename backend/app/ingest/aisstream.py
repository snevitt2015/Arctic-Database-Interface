"""
aisstream.py  -  Live AIS from aisstream.io (free API key, websocket, terrestrial + some satellite).

    python -m app.workers.live --feed aisstream

Subscribes to the area of interest in config (default: north of 60°N) and stores pings in
batches. Message format: https://aisstream.io/documentation
"""
import asyncio
import json
import logging
import re
from datetime import datetime

from app import config

URL = "wss://stream.aisstream.io/v0/stream"
log = logging.getLogger("aisstream")

NAV_STATUS = {0: "under way using engine", 1: "at anchor", 2: "not under command", 3: "restricted manoeuvrability",
              4: "constrained by draught", 5: "moored", 6: "aground", 7: "engaged in fishing",
              8: "under way sailing", 15: "not defined"}


def subscription(api_key: str = None) -> dict:
    return {"APIKey": api_key or config.AISSTREAM_API_KEY,
            "BoundingBoxes": [[[config.AOI_SOUTH, config.AOI_WEST], [config.AOI_NORTH, config.AOI_EAST]]],
            "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"]}


def parse(msg: dict) -> dict | None:
    """aisstream message -> standard ping dict (or None)."""
    kind = msg.get("MessageType")
    meta = msg.get("MetaData", {})
    body = msg.get("Message", {}).get(kind, {})
    mmsi = str(meta.get("MMSI") or body.get("UserID") or "")
    m = re.match(r"(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:\.(\d+))?", meta.get("time_utc") or "")
    if not m or not mmsi:
        return None
    ts = datetime.fromisoformat(f"{m[1]}T{m[2]}.{(m[3] or '0')[:6].ljust(6, '0')}+00:00")  # always UTC
    p = {"mmsi": mmsi, "ts": ts, "name": (meta.get("ShipName") or "").strip() or None, "source": "aisstream"}
    if kind in ("PositionReport", "StandardClassBPositionReport"):
        p.update(lat=body.get("Latitude", meta.get("latitude")), lon=body.get("Longitude", meta.get("longitude")),
                 sog=body.get("Sog"), cog=body.get("Cog"),
                 heading=None if body.get("TrueHeading") in (None, 511) else body.get("TrueHeading"),
                 nav_status=NAV_STATUS.get(body.get("NavigationalStatus")))
        return p
    if kind == "ShipStaticData":
        dim = body.get("Dimension") or {}
        p.update(imo=str(body.get("ImoNumber") or "") or None, callsign=(body.get("CallSign") or "").strip() or None,
                 name=(body.get("Name") or "").strip() or p["name"],
                 length_m=(dim.get("A", 0) + dim.get("B", 0)) or None, beam_m=(dim.get("C", 0) + dim.get("D", 0)) or None,
                 draught_m=body.get("MaximumStaticDraught") or None)
        return p
    return None


async def run(store, batch_size=200, flush_seconds=5.0, api_key=None, status=None):
    """Stream forever; `store(list_of_pings)` is called with each batch."""
    import websockets
    key = api_key or config.AISSTREAM_API_KEY
    if not key:
        raise RuntimeError("AISSTREAM_API_KEY is not set")
    backoff = 2
    while True:
        try:
            async with websockets.connect(URL, ping_interval=30) as ws:
                await ws.send(json.dumps(subscription(key)))
                log.info("aisstream connected")
                backoff = 2
                batch, last = [], asyncio.get_event_loop().time()
                async for raw in ws:
                    p = parse(json.loads(raw))
                    if p:
                        batch.append(p)
                    now = asyncio.get_event_loop().time()
                    if len(batch) >= batch_size or (batch and now - last >= flush_seconds):
                        store(batch)
                        if status is not None:
                            status.update(last_message=batch[-1]["ts"], received=status.get("received", 0) + len(batch))
                        batch, last = [], now
        except Exception as e:                      # network drop: reconnect with backoff
            log.warning("aisstream: %s - reconnecting in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
