"""
Run the lab pipeline from IDLE: paste your photo folder below, then press F5.

The folder is the one that HOLDS your session folders, e.g.
    C:\\Users\\you\\Downloads\\Lab Photos
        10-23-26_Sun15Camera20\\IMG_6383.JPG
        11-3-26Sun25Camera25\\IMG_7002.JPG
Tip: in File Explorer, click the address bar, copy, and paste between the quotes.
Results go to the lab_out folder next to this file. Your photos are never changed.
"""
PHOTO_FOLDER = r"C:\Users\snevi\Downloads\Arctic Capstone Work\Lab Photos"
PREVIEW = 5      # 5 = quick check on 5 photos first (look at lab_out\debug); 0 = run everything

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from lab.run import run

run(PHOTO_FOLDER, preview=PREVIEW)
