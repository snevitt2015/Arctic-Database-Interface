"""
start_app.py  -  Start the map app from IDLE (or by double-clicking).

In IDLE: File -> Open -> this file, then press F5.
The first time it installs the needed packages (takes a few minutes).
Then the map opens in your web browser.
To stop the app: close this IDLE window, or press Ctrl+C in the shell.
"""
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent     # the project folder
URL = "http://localhost:8501"

print("Step 1/2: installing packages (first time can take a few minutes)...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")],
               check=True)

print("Step 2/2: starting the map app...")
print(f"If your browser doesn't open by itself, go to {URL}")
print("Leave this window open while you use the app.")

# Open the browser a few seconds after the app starts
threading.Thread(target=lambda: (time.sleep(6), webbrowser.open(URL)), daemon=True).start()

subprocess.run([sys.executable, "-m", "streamlit", "run", str(HERE / "app.py"),
                "--server.headless", "true",                 # skip Streamlit's first-run email question
                "--browser.gatherUsageStats", "false"],
               cwd=HERE)
