"""
pipeline.py  -  Glue: run the whole algorithm in two calls.

    update_data()   download from all OSINT sources -> save to positions.csv
    build_dataset() load everything -> match to Excel -> add features

Both the app (app.py) and the command line (run_update.py) use these,
so they always give the same answer.
"""
import pandas as pd

from tracker import excel_loader, storage, matching, features
from tracker.schema import standardize
from tracker.sources import ALL_SOURCES, PRIVATE_SOURCES


def update_data(source_names=None, log=print) -> int:
    vessels = excel_loader.load_vessels()
    total_new = 0
    for name, fetch in ALL_SOURCES.items():
        if source_names and name not in source_names:
            continue
        rows = fetch(vessels, log=log)
        if not rows.empty:
            new = storage.save_positions(rows, private=name in PRIVATE_SOURCES)
            log(f"{name}: {len(rows)} rows downloaded, {new} new")
            total_new += new
    return total_new


def build_dataset(include_demo=False):
    """Returns (positions_with_features, vessels, ports)."""
    vessels = excel_loader.load_vessels()
    ports = excel_loader.load_ports()
    from tracker import capstone_loader
    positions = pd.concat([storage.load_positions(include_demo),
                           excel_loader.load_operations(),
                           capstone_loader.ais_checks_as_positions(capstone_loader.load_ais_checks(),
                                                                   capstone_loader.load_registry())],
                          ignore_index=True)
    positions = standardize(positions)   # keep the time column as real dates after combining
    if positions.empty:
        return positions, vessels, ports
    positions = matching.match_to_watchlist(positions, vessels)
    positions = features.add_features(positions, ports)
    return positions, vessels, ports
