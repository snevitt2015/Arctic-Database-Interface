"""
OSINT data sources.

Each file here has ONE main function:

    fetch(vessels) -> pandas DataFrame in the standard format (see schema.py)

To add a new source (e.g. Sentinel-1 satellite detections, VesselFinder):
  1. copy csv_import.py or global_fishing_watch.py to a new file
  2. convert its data to the standard columns
  3. add it to ALL_SOURCES below - done. The app and run_update.py pick it up.
"""
from tracker.sources import global_fishing_watch, marinetraffic, csv_import

# Sources whose data must NOT be published (their terms forbid it) -> saved to the private file
PRIVATE_SOURCES = {"MarineTraffic", "CSV imports (TankerMap / MarineTraffic exports / other)"}

ALL_SOURCES = {
    "Global Fishing Watch": global_fishing_watch.fetch,
    "MarineTraffic": marinetraffic.fetch,
    "CSV imports (TankerMap / MarineTraffic exports / other)": csv_import.fetch,
}
