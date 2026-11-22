"""
norway_ais.py  -  Open live AIS from the Norwegian Coastal Administration (Kystverket).

Free, no key. Raw NMEA over TCP covering the Norwegian coast, the Barents Sea approaches and
Svalbard - exactly where Russian Arctic traffic passes Murmansk <-> Europe.
Licence: NLOD (credit "Contains data from the Norwegian Coastal Administration").

    NORWAY_AIS_ENABLED=true  python -m app.workers.live --feed norway

Messages are decoded with pyais. Kystverket prefixes each sentence with a tag block whose
`c:` field is the receive time (unix seconds); we use it as the ping time.
"""
import logging
import time
from datetime import datetime, timezone

from app import config

log = logging.getLogger("norway_ais")
POSITION_TYPES = {1, 2, 3, 18, 19, 27}
STATIC_TYPES = {5, 24}


def _ts(msg) -> datetime:
    tb = getattr(msg, "tag_block", None)
    if tb is not None:
        try:
            tb.init()
            if tb.receiver_timestamp:
                return datetime.fromtimestamp(int(tb.receiver_timestamp), tz=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def parse_decoded(d: dict, ts: datetime) -> dict | None:
    """pyais decoded dict -> standard ping dict."""
    mt = d.get("msg_type")
    mmsi = str(d.get("mmsi") or "").zfill(9)
    p = {"mmsi": mmsi, "ts": ts, "source": "norway_ais"}
    if mt in POSITION_TYPES:
        lat, lon = d.get("lat"), d.get("lon")
        if lat is None or lon is None:
            return None
        p.update(lat=lat, lon=lon, sog=d.get("speed"), cog=d.get("course"),
                 heading=None if d.get("heading") in (None, 511) else d.get("heading"),
                 nav_status=str(d.get("status")).split(".")[-1].replace("_", " ").lower() if d.get("status") is not None else None)
        if p["sog"] is not None and p["sog"] >= 102.2:
            p["sog"] = None
        if p["cog"] is not None and p["cog"] >= 360:
            p["cog"] = None
        return p
    if mt in STATIC_TYPES:
        name = (d.get("shipname") or "").strip(" @") or None
        L = (d.get("to_bow") or 0) + (d.get("to_stern") or 0)
        B = (d.get("to_port") or 0) + (d.get("to_starboard") or 0)
        p.update(name=name, imo=str(d["imo"]) if d.get("imo") else None,
                 callsign=(d.get("callsign") or "").strip(" @") or None,
                 length_m=L or None, beam_m=B or None, draught_m=d.get("draught") or None)
        return p
    return None


def run(store, batch_size=200, flush_seconds=5.0, status=None):
    from pyais.stream import TCPConnection
    backoff = 2
    while True:
        try:
            batch, last = [], time.time()
            for msg in TCPConnection(config.NORWAY_AIS_HOST, port=config.NORWAY_AIS_PORT):
                try:
                    p = parse_decoded(msg.decode().asdict(), _ts(msg))
                except Exception:
                    continue                     # one bad sentence never stops the feed
                if p:
                    batch.append(p)
                if len(batch) >= batch_size or (batch and time.time() - last >= flush_seconds):
                    store(batch)
                    if status is not None:
                        status.update(last_message=batch[-1]["ts"], received=status.get("received", 0) + len(batch))
                    batch, last = [], time.time()
                    backoff = 2
        except Exception as e:
            log.warning("norway_ais: %s - reconnecting in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
