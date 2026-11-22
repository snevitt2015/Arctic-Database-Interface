import os
import sys
import tempfile
from pathlib import Path

# The app reads DATABASE_URL at import time: point it at a throwaway file before anything imports app.*
_tmp = tempfile.mkdtemp()
os.environ["ARCTIC_TESTING"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
