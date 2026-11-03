"""
run_update.py  -  Command-line version (no user interface).

    python run_update.py              download from all sources, then print a summary
    python run_update.py --no-fetch   just analyse the data you already have
    python run_update.py --demo       include the SYNTHETIC demo data

GitHub Actions runs this every day (see .github/workflows/daily_update.yml).
"""
import argparse

from tracker.pipeline import update_data, build_dataset
from tracker.features import dark_periods
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
        p.insert(0, "vessel", vessel)
        predictions.append(p)
        p24 = p[(p["hours_ahead"] == 24) & (p["method"] == "dead_reckoning")]
        nxt = f"-> 24h: {p24.iloc[0]['lat']:.2f}, {p24.iloc[0]['lon']:.2f}" if not p24.empty else "-> no prediction"
        print(f"  {vessel:16} {last['timestamp']:%Y-%m-%d %H:%M}  {last['lat']:.2f}, {last['lon']:.2f}  "
              f"{last['speed_used']:.1f} kn  {last['course_used']:.0f}°  {nxt}")

    dark = dark_periods(df)
    print(f"\n{len(dark)} suspected dark periods.")

    # Save results so they can be opened in Excel or committed to GitHub
    df.to_csv(DATA_DIR / "output_features.csv", index=False)
    dark.to_csv(DATA_DIR / "output_dark_periods.csv", index=False)
    if predictions:
        import pandas as pd
        pd.concat(predictions).to_csv(DATA_DIR / "output_predictions.csv", index=False)
    print("Saved data/output_features.csv, output_dark_periods.csv, output_predictions.csv")


if __name__ == "__main__":
    main()
