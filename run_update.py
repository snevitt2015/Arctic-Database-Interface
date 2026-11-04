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
    args = parser.parse_args()

    if not args.no_fetch:
        print("Downloading from OSINT sources...")
        new = update_data()
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


if __name__ == "__main__":
    main()
