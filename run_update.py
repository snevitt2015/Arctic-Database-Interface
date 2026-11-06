"""
run_update.py  -  Command-line version (no user interface).

    python run_update.py              download from all sources, then print a summary
    python run_update.py --no-fetch   just analyse the data you already have
    python run_update.py --demo       include the SYNTHETIC demo data

GitHub Actions runs this every day (see .github/workflows/daily_update.yml).
"""
import argparse

import pandas as pd

from tracker.pipeline import update_data, build_dataset
from tracker.features import dark_periods
from tracker.matching import identity_changes
from tracker.prediction import predict
from config import DATA_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true", help="skip downloading")
    parser.add_argument("--demo", action="store_true", help="include synthetic demo data")
    parser.add_argument("--full", action="store_true", help="re-download every ship's full history (slow)")
    parser.add_argument("--imagery", action="store_true", help="search satellite imagery even with --no-fetch")
    parser.add_argument("--no-imagery", action="store_true", help="skip the satellite imagery step")
    args = parser.parse_args()

    # Data checks first, so problems are visible even if nothing else runs
    from tracker.validation import run_all_checks
    checks = run_all_checks()
    checks.to_csv(DATA_DIR / "output_data_checks.csv", index=False)
    counts = checks["severity"].value_counts()
    print(f"Data checks: {counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings "
          f"(see data/output_data_checks.csv)")
    for _, c in checks[checks["severity"] == "error"].iterrows():
        print(f"  ERROR {c['table']} / {c['record']} / {c['field']}: {c['problem']}")
    from tracker.capstone_loader import _source_note
    print("Capstone Database read from:", ", ".join(sorted(set(_source_note.values()))) or "not found")
    print()

    if not args.no_fetch:
        print("Downloading from OSINT sources...")
        new = update_data(full=args.full)
        print(f"{new} new rows saved.\n")

    df, vessels, ports = build_dataset(include_demo=args.demo)
    if df.empty:
        print("No position data yet. Set GFW_API_TOKEN, add CSVs to data/imports/, or use --demo.")
        return

    print(f"{len(df)} positions, {df['vessel'].ne('').sum()} matched to the Excel watch list.\n")

    # Latest position + prediction for each vessel
    print("LATEST POSITION AND 24-HOUR PREDICTION")
    predictions = []
    for vessel, track in df[df["vessel"] != ""].groupby("vessel"):
        last = track.sort_values("timestamp").iloc[-1]
        p = predict(track)
        nxt = "-> no prediction (no recent speed/direction)"
        if not p.empty:
            p.insert(0, "vessel", vessel)
            predictions.append(p)
            p24 = p[(p["hours_ahead"] == 24) & (p["method"] == "dead_reckoning")]
            if not p24.empty:
                nxt = f"-> 24h: {p24.iloc[0]['lat']:.2f}, {p24.iloc[0]['lon']:.2f}"
        spd = "  ?  kn" if pd.isna(last["speed_used"]) else f"{last['speed_used']:4.1f} kn"
        print(f"  {vessel:16} {last['timestamp']:%Y-%m-%d %H:%M}  {last['lat']:6.2f}, {last['lon']:7.2f}  {spd}  {nxt}")

    dark = dark_periods(df)
    print(f"\n{len(dark)} suspected dark periods.")

    # Save results so they can be opened in Excel or committed to GitHub
    df.to_csv(DATA_DIR / "output_features.csv", index=False)
    dark.to_csv(DATA_DIR / "output_dark_periods.csv", index=False)
    pred_cols = ["vessel", "method", "hours_ahead", "timestamp", "lat", "lon", "uncertainty_km"]
    (pd.concat(predictions) if predictions else pd.DataFrame(columns=pred_cols)).to_csv(
        DATA_DIR / "output_predictions.csv", index=False)
    # Small extra tables for the web map (index.html)
    port_cols = ["name", "lat", "lon", "coords_source", "berths", "depth_capacity", "managing_company",
                 "primary_use", "infrastructure", "notes"]
    ports.reindex(columns=port_cols).to_csv(DATA_DIR / "output_ports.csv", index=False)
    vessels.reindex(columns=["name", "type", "flag", "imo", "mmsi", "call_sign", "sanctioned", "notes",
                             "list_source", "gross_tonnage", "year_built"]).to_csv(DATA_DIR / "output_vessels.csv", index=False)
    # Capstone Database tables for the web map
    from tracker import capstone_loader as cap
    cap.load_registry().to_csv(DATA_DIR / "output_fleet.csv", index=False)
    cap.load_ais_checks().to_csv(DATA_DIR / "output_ais_checks.csv", index=False)
    cap.load_imagery().to_csv(DATA_DIR / "output_imagery.csv", index=False)
    cap.load_models().to_csv(DATA_DIR / "output_models.csv", index=False)
    cap.load_type_stats().to_csv(DATA_DIR / "output_type_stats.csv", index=False)
    identity_changes(df).to_csv(DATA_DIR / "output_identities.csv", index=False)
    print("Saved data/output_*.csv (features, dark periods, predictions, ports, vessels, identities)")

    # ---------------- cues ("pings") -> satellite imagery -> detections
    run_imagery = (not args.no_fetch or args.imagery) and not args.no_imagery
    imagery_step(df, vessels, run_imagery)


def imagery_step(df, vessels, search=True):
    from tracker.cueing import build_cues
    cues = build_cues(df, vessels)
    old_cues = pd.read_csv(DATA_DIR / "output_cues.csv") if (DATA_DIR / "output_cues.csv").exists() else pd.DataFrame()
    first_seen = dict(zip(old_cues.get("cue_id", []), old_cues.get("first_seen", []))) if not old_cues.empty else {}
    today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    if not cues.empty:
        cues["first_seen"] = cues["cue_id"].map(first_seen).fillna(today)
    print(f"\n{len(cues)} cues (pings): " + (", ".join(f"{n} {k}" for k, n in cues["kind"].value_counts().items()) if not cues.empty else "none")
          + f"; {int((cues.get('first_seen', pd.Series(dtype=str)) == today).sum())} new today")
    cues.to_csv(DATA_DIR / "output_cues.csv", index=False)
    if not search or cues.empty:
        return
    from tracker.sources.copernicus import search_all
    from tracker.sar_chips import process_scenes
    from tracker.capstone_loader import load_registry
    scenes_file, det_file = DATA_DIR / "output_scenes.csv", DATA_DIR / "output_detections.csv"
    old = pd.read_csv(scenes_file) if scenes_file.exists() else pd.DataFrame()
    scenes = search_all(cues, previous=old)
    scenes.to_csv(scenes_file, index=False)
    print(f"Imagery: {len(scenes)} Sentinel scenes over the cues "
          f"({(scenes['kind'] == 'SAR').sum() if not scenes.empty else 0} SAR, "
          f"{(scenes['kind'] == 'Optical').sum() if not scenes.empty else 0} optical)")
    old_det = pd.read_csv(det_file) if det_file.exists() else pd.DataFrame()
    dets = process_scenes(scenes, df, cues, load_registry(), previous=old_det)
    if dets is not None and not dets.empty:
        dets.to_csv(det_file, index=False)
        print(f"Detections: {len(dets)} rows saved")


if __name__ == "__main__":
    main()
