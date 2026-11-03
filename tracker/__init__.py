"""Arctic vessel tracker package.

Flow of data (each step is one small file):

  excel_loader.py  -> list of our vessels + ports from the Excel sheet
  sources/*.py     -> raw positions from OSINT (GFW, MarineTraffic, TankerMap, CSV)
  matching.py      -> pair each OSINT record with a vessel from the Excel sheet
  storage.py       -> save everything to data/positions.csv (all dates)
  features.py      -> add speed, direction, gaps, dark flags, nearest port
  prediction.py    -> predict future positions
"""
