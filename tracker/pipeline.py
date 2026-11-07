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


def gfw_since(overlap_days=None) -> dict:
    """For each ship we already have GFW data for: the date to start the next download from."""
    import config
    overlap = config.GFW_OVERLAP_DAYS if overlap_days is None else overlap_days
    have = storage.load_positions()
    have = have[have["source"] == "GFW"]
    if have.empty:
        return {}
    have = have.assign(key=have["imo"].where(have["imo"] != "", have["mmsi"]))
    last = have.groupby("key")["timestamp"].max()
    return {k: (t - pd.Timedelta(days=overlap)).strftime("%Y-%m-%d") for k, t in last.items() if k}


def update_data(source_names=None, log=print, full=False) -> int:
    """Download from every source. Normally GFW only fetches new events (fast);
    full=True re-downloads every ship's whole history since 2012."""
    vessels = excel_loader.load_vessels()
    since = {} if full else gfw_since()
    total_new = 0
    for name, fetch in ALL_SOURCES.items():
        if source_names and name not in source_names:
            continue
        extra = {"since": since} if name == "Global Fishing Watch" else {}
        rows = fetch(vessels, log=log, **extra)
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
    from tracker.kalman import add_kalman_flags
    positions = add_kalman_flags(positions)          # kf_* columns: filtered track + "unexpected" reports
    return positions, vessels, ports
