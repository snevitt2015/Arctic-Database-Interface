# Arctic-Database-Interface
Ongoing code for Eyes on the Arctic Fall 2026

Tracks Russian-linked tankers in the Arctic, flags **dark activity** (AIS switched off,
spoofed positions, identity changes, ship-to-ship meetings), and **predicts future positions**.

It combines open-source intelligence (OSINT) with our Excel watch list:

| Source | How it gets in | What it gives |
|---|---|---|
| **Excel sheet** (`data/Russian_Tankers_and_Port_Data.xlsx`) | read automatically | watch list (Vessels), ports, manual sightings (Operations tab) |
| **Global Fishing Watch** | free API (non-commercial) | AIS-off gaps, port visits, encounters, loitering, all dates since 2012 |
| **MarineTraffic** | paid API, or CSV export | full AIS tracks: position, speed, course, heading |
| **TankerMap / anything else** | CSV/Excel upload or drop in `data/imports/` | whatever columns the file has |

---

## 1. Install (once)

```bash
git clone https://github.com/<your-account>/arctic-tracker.git
cd arctic-tracker
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run the app

```bash
streamlit run app.py
```

Opens in your browser at http://localhost:8501. Tick **Show demo data** to see it work right
away. Demo data is **synthetic** (made up), kept in its own file, and labelled `DEMO (synthetic)`.

Command-line version (no interface):

```bash
python run_update.py               # download + analyse + save CSVs in data/
python run_update.py --no-fetch    # analyse only what you already have
```

## 3. Connect real data

1. **Global Fishing Watch**: sign up at https://globalfishingwatch.org/our-apis/tokens/signup,
   copy `.env.example` to `.env`, and paste your token after `GFW_API_TOKEN=`.
   Then click **Fetch now** in the app. The first run pulls every date since 2012 and takes a while.
   GFW data is for non-commercial use only, and you must credit GFW in anything you publish.
2. **MarineTraffic**: add `MARINETRAFFIC_API_KEY` to `.env` if you have a paid plan. Check the
   endpoint in `tracker/sources/marinetraffic.py` against your plan first, because every call costs credits.
3. **TankerMap / website exports**: upload the file in the app sidebar, or put it in `data/imports/`.
4. **Manual sightings**: type them into the Excel **Operations** tab (IMO, Date, Latitude, Longitude, Speed, Heading).

All data goes into one growing file, `data/positions.csv`, so history is never lost.

## 4. GitHub

* Create an empty repo on GitHub, then:
  ```bash
  git remote add origin https://github.com/<your-account>/arctic-tracker.git
  git push -u origin main
  ```
* **Automatic daily updates**: `.github/workflows/daily_update.yml` runs every day on GitHub,
  downloads new data, and commits it to the repo. To turn it on:
  repo **Settings → Secrets and variables → Actions** → add `GFW_API_TOKEN`; then
  **Settings → Actions → General → Workflow permissions → Read and write**.
  The **Actions** tab also has a "Run workflow" button for running it by hand.
* ⚠️ If the repo is **public**, the watch list and collected data are public too. Use a private
  repo if that matters.

---

## How the algorithm works

```
Excel watch list ─┐
GFW / MT / CSV ───┼─► standard format ─► match to Excel ─► features ─► dark-activity flags
Excel Operations ─┘   (schema.py)        (matching.py)    (features.py)     + predictions
                                                                           (prediction.py)
```

| File | What it does |
|---|---|
| `config.py` | **all settings** (dark-gap hours, speed limits, prediction horizon) |
| `tracker/schema.py` | the standard columns every source is converted to |
| `tracker/excel_loader.py` | reads the Excel tabs, parses port coordinates, flags data problems |
| `tracker/sources/*.py` | one file per OSINT source |
| `tracker/matching.py` | pairs OSINT rows with Excel vessels: IMO → MMSI → name |
| `tracker/features.py` | speed, direction, turns, gaps, nearest port, dark/spoofing flags, risk score |
| `tracker/prediction.py` | future positions + backtest (accuracy check on past data) |
| `tracker/geo.py` | distance / direction maths (great-circle, which matters in the Arctic) |
| `app.py` | the user interface |

**Features computed for every position:** time since last position, distance moved, calculated
speed and course, reported vs. calculated speed, direction change, nearest port and distance,
in-port flag, AIS gap (> 6 h by default), speed mismatch (possible spoofing), impossible jump
(> 30 kn), and a simple risk score.

**Prediction methods** (both give a growing uncertainty radius):
* `dead_reckoning`: last position + last speed + last direction.
* `recent_average`: average movement over the last 5 positions. This still works when a dark
  ship stops broadcasting speed and heading.
* A ship in port or under 0.5 kn is predicted to stay where it is.

The **Predictions** tab has a backtest that shows how many km off each method was on past data.
Use it to check whether a new method is actually better.

## Adding features (suggested order, simple → complex)

1. **New column/feature**: add a few lines in `add_features()` in `features.py`.
2. **New data source**: copy `sources/csv_import.py`, then register it in `sources/__init__.py`.
3. **New prediction method**: write a function with the same inputs/outputs as `recent_average`
   and add it to `METHODS` in `prediction.py`. It appears in the app automatically.
4. Then: avoid land (coastline file), predict destination from past port visits, learn common
   routes (Murmansk ↔ Sabetta), Northern Sea Route ice season, machine learning on the features
   table (scikit-learn is already installed), satellite (SAR) detections of ships with AIS off.

## Known data issues in the current Excel sheet

(Also shown on the app's **Data quality** tab.)
* ALEXEY KOSYGIN MMSI `273253` is only 6 digits. A real MMSI has 9, so check it.
* Pecha has no IMO, so it can only match on MMSI or name.
* Sabetta, Arkhangelsk, and Tieshan have no coordinates in the sheet. The code uses
  **approximate** ones (marked "approximate - verify"). Type the real ones into the Excel sheet.
* The Sanctioned column is empty. Filling it in will let the risk score use it.

## Tests

```bash
python -m pytest
```
