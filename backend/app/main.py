"""
main.py  -  The API server.

    uvicorn app.main:app --reload          (from the backend/ folder)

Interactive docs at http://localhost:8000/docs - every endpoint can be tried from there.
Tabs -> routers:
    Overview         /api/overview, /api/overview/map
    Live OSINT feed  /api/live/*, /ws/live
    Spoofing         /api/spoofing/*
    SAR comparative  /api/sar/*
    (shared)         /api/vessels/*
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import data, live, overview, sar, spoofing, vessels
from app.db import init_db


@asynccontextmanager
async def lifespan(_):
    init_db()
    yield


app = FastAPI(title="Arctic Vessel Watch API", version="2.0.0", lifespan=lifespan,
              description="Backend for the Arctic AIS-spoofing monitor: overview map, live OSINT feeds, "
                          "spoofing detection and SAR ⇄ optical comparative.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for r in (overview.router, live.router, spoofing.router, sar.router, vessels.router, data.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True}


# The interface: http://localhost:8000/ serves app/static/index.html, which calls the API above.
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402

STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def interface():
    return FileResponse(STATIC / "index.html")
