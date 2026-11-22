"""
config.py  -  Every setting in one place, read from environment variables (or a .env file).

Nothing secret is ever written in code. Copy .env.example to .env and fill in the keys you have;
any feed without a key is simply switched off and shown as "not configured" in the Live tab.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent

# ---------- Database ----------
# SQLite for a laptop / class demo; set a postgresql+psycopg:// URL for the shared server.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'arctic.db'}")

# ---------- Area of interest ----------
# Live feeds subscribe to this box. Default: everything north of 60°N (whole Arctic + approaches).
AOI_SOUTH = float(os.getenv("AOI_SOUTH", "60"))
AOI_NORTH = float(os.getenv("AOI_NORTH", "90"))
AOI_WEST = float(os.getenv("AOI_WEST", "-180"))
AOI_EAST = float(os.getenv("AOI_EAST", "180"))

# ---------- OSINT feeds (blank = feed off) ----------
AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "")          # https://aisstream.io (free key)
NORWAY_AIS_HOST = os.getenv("NORWAY_AIS_HOST", "153.44.253.27")  # Kystverket open AIS (Norwegian waters, Svalbard)
NORWAY_AIS_PORT = int(os.getenv("NORWAY_AIS_PORT", "5631"))
NORWAY_AIS_ENABLED = os.getenv("NORWAY_AIS_ENABLED", "false").lower() == "true"
GFW_API_TOKEN = os.getenv("GFW_API_TOKEN", "")                  # Global Fishing Watch (non-commercial)
MARINETRAFFIC_API_KEY = os.getenv("MARINETRAFFIC_API_KEY", "")  # paid
COPERNICUS_USER = os.getenv("COPERNICUS_USER", "")              # only needed to DOWNLOAD Sentinel scenes
COPERNICUS_PASSWORD = os.getenv("COPERNICUS_PASSWORD", "")

# ---------- Spoofing / dark-activity thresholds ----------
GAP_HOURS = float(os.getenv("GAP_HOURS", "6"))            # silence longer than this = AIS gap
CYCLE_MIN_GAP_MIN = float(os.getenv("CYCLE_MIN_GAP_MIN", "45"))  # a silence this long counts as one "off"
CYCLE_WINDOW_HOURS = float(os.getenv("CYCLE_WINDOW_HOURS", "24"))
CYCLE_MIN_COUNT = int(os.getenv("CYCLE_MIN_COUNT", "3"))  # this many offs in the window = on/off cycling
MAX_SPEED_KN = float(os.getenv("MAX_SPEED_KN", "30"))     # faster than any tanker / LNG carrier
SPEED_MISMATCH_KN = float(os.getenv("SPEED_MISMATCH_KN", "5"))
DETECT_LOOKBACK_HOURS = float(os.getenv("DETECT_LOOKBACK_HOURS", "72"))  # re-check this much history per new ping

# ---------- Live feed ----------
LIVE_WINDOW_HOURS = float(os.getenv("LIVE_WINDOW_HOURS", "6"))   # "live" = reported within this
