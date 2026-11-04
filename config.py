"""
config.py  -  All settings in one place.

Change numbers here instead of digging through the code.
API keys are read from environment variables (never type them into code
that goes to GitHub). Locally you can put them in a file called .env
(see .env.example); on GitHub use Settings -> Secrets -> Actions.
"""
import os
from pathlib import Path

# Load a local .env file if python-dotenv is installed (optional).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------- File locations ----------
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
EXCEL_FILE = DATA_DIR / "Russian_Tankers_and_Port_Data.xlsx"
CAPSTONE_FILE = DATA_DIR / "Arctic_Capstone_Database.xlsx"   # team Airtable export (registry, AIS checks, imagery)
POSITIONS_FILE = DATA_DIR / "positions.csv"        # every position ever collected (all dates) - committed to GitHub
PRIVATE_POSITIONS_FILE = DATA_DIR / "imports" / "private_positions.csv"  # MarineTraffic/TankerMap rows - stays on your computer
DEMO_FILE = DATA_DIR / "demo_positions.csv"         # SYNTHETIC test data only
IMPORTS_DIR = DATA_DIR / "imports"                  # drop MarineTraffic / TankerMap CSV exports here

# ---------- API keys (from environment) ----------
GFW_API_TOKEN = os.getenv("GFW_API_TOKEN", "")
MARINETRAFFIC_API_KEY = os.getenv("MARINETRAFFIC_API_KEY", "")

# ---------- History ----------
# "All dates": Global Fishing Watch AIS data starts in 2012.
HISTORY_START_DATE = "2012-01-01"

# ---------- Algorithm settings ----------
DARK_GAP_HOURS = 6.0          # AIS silent longer than this -> flagged as a possible "dark" period
SPEED_MISMATCH_KNOTS = 5.0    # reported vs. calculated speed differ by more -> possible spoofing
MAX_REALISTIC_SPEED_KN = 30.0 # a tanker jumping faster than this -> position likely spoofed
IN_PORT_KM = 10.0             # within this distance of a port -> "in port"
NEAR_PORT_MAX_KM = 300.0      # further than this from every port -> nearest_port left blank

# Positions further apart than this are NOT used to calculate speed/direction and are
# NOT joined by a line on the map (e.g. two port visits a month apart).
MAX_HOURS_BETWEEN_POINTS = 12.0

# Prediction
PREDICT_HOURS = 72            # how far ahead to predict
PREDICT_STEP_HOURS = 6        # one predicted point every N hours
RECENT_POINTS_FOR_AVERAGE = 5 # how many recent positions the "average" method uses
