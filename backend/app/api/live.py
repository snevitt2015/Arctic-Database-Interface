"""Live OSINT tab: latest positions, the incoming ping feed, feed health, CSV imports, websocket push."""
import asyncio
import io
from datetime import timedelta

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.api.common import feature, iso, now_utc, position_out, utc
from app.db import SessionLocal, get_session
from app.ingest.csv_import import load_positions
from app.models import Position
from app.services.ingest import store_pings

router = APIRouter(tags=["live"])

FEEDS = [
    {"id": "aisstream", "name": "aisstream.io (live AIS)", "configured": lambda: bool(config.AISSTREAM_API_KEY)},
    {"id": "norway_ais", "name": "Kystverket open AIS (Norway / Svalbard)", "configured": lambda: config.NORWAY_AIS_ENABLED},
    {"id": "gfw", "name": "Global Fishing Watch (gaps, encounters, loitering)", "configured": lambda: bool(config.GFW_API_TOKEN)},
    {"id": "csv:marinetraffic", "name": "MarineTraffic (CSV export / paid API)", "configured": lambda: True},
    {"id": "csv:tankermap", "name": "TankerMap (CSV export)", "configured": lambda: True},
]


def latest_positions(s: Session, since):
    """Most recent ping per MMSI since `since`."""
    sub = (select(Position.mmsi, func.max(Position.ts).label("ts")).where(Position.ts >= since)
           .group_by(Position.mmsi).subquery())
    rows = s.scalars(select(Position).join(sub, (Position.mmsi == sub.c.mmsi) & (Position.ts == sub.c.ts))).all()
    best = {}
    for p in rows:                               # two feeds can report the same second: keep one
        best.setdefault(p.mmsi, p)
    return list(best.values())


@router.get("/api/live/positions")
def live_positions(hours: float = None, s: Session = Depends(get_session)):
    newest = s.scalar(select(func.max(Position.ts)))
    ref = utc(newest) if newest else now_utc()
    since = ref - timedelta(hours=hours or config.LIVE_WINDOW_HOURS)
    ps = latest_positions(s, since)
    return {"type": "FeatureCollection", "as_of": ref.isoformat(),
            "features": [feature(p.lon, p.lat, position_out(p)) for p in ps]}


@router.get("/api/live/feed")
def live_feed(limit: int = 100, after_id: int = 0, source: str = None, s: Session = Depends(get_session)):
    """Newest pings first (or everything after `after_id`, for polling)."""
    q = select(Position).where(Position.id > after_id)
    if source:
        q = q.where(Position.source == source)
    rows = s.scalars(q.order_by(Position.id.desc()).limit(limit)).all()
    return [position_out(p) for p in rows]


@router.get("/api/live/sources")
def sources(s: Session = Depends(get_session)):
    """Health of each OSINT feed: switched on? when did it last deliver?"""
    stats = {src: (n, last) for src, n, last in
             s.execute(select(Position.source, func.count(Position.id), func.max(Position.ts)).group_by(Position.source))}
    out = []
    for f in FEEDS:
        n, last = stats.get(f["id"], (0, None))
        out.append({"id": f["id"], "name": f["name"], "configured": f["configured"](), "positions": n,
                    "last_position": iso(last)})
    for src, (n, last) in stats.items():
        if src not in {f["id"] for f in FEEDS}:
            out.append({"id": src, "name": src, "configured": True, "positions": n, "last_position": iso(last)})
    return out


@router.post("/api/live/import")
async def import_positions(file: UploadFile = File(...), source: str = Form("upload"), s: Session = Depends(get_session)):
    """Upload a MarineTraffic / TankerMap / any CSV or Excel export of positions."""
    raw = await file.read()
    df = pd.read_excel(io.BytesIO(raw)) if file.filename.lower().endswith((".xlsx", ".xls")) else pd.read_csv(io.BytesIO(raw))
    pings = load_positions(df, source.lower().replace(" ", ""))
    return store_pings(s, pings)


@router.websocket("/ws/live")
async def live_socket(ws: WebSocket):
    """Pushes new pings to the browser every 2 s: [{...position...}, ...]."""
    await ws.accept()
    with SessionLocal() as s:
        last_id = s.scalar(select(func.max(Position.id))) or 0
    try:
        while True:
            await asyncio.sleep(2)
            with SessionLocal() as s:
                rows = s.scalars(select(Position).where(Position.id > last_id).order_by(Position.id).limit(500)).all()
            if rows:
                last_id = rows[-1].id
                await ws.send_json([position_out(p) for p in rows])
    except WebSocketDisconnect:
        pass
