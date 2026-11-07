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

## 0. Arctic Vessel Watch - interactive map (no install)

**https://snevitt2015.github.io/Arctic-Database-Interface/**

A full-screen viewer for the Russian Arctic, refreshed every morning by the daily GitHub Action:
* **Imagery**: high-resolution satellite (zoom right into port terminals), ocean-depth, dark and light maps
* **NASA daily layers**: true-colour satellite image of any day, and sea-ice concentration
* **Sea marks** (OpenSeaMap): buoys, lights, harbour features when zoomed in near a coast
* **3D globe** (no Arctic stretching) or flat map
* **Ships by type**: LNG carriers, oil/chemical/bunkering tankers, cargo, container ships, icebreakers.
  Click one for its card: identity, activity, every name and MMSI it has used, most-visited ports, AIS-off periods
* **Ports**: click for a profile (from the Excel sheet) plus which watch-list ships were recorded there; "View port up close" flies in on satellite imagery
* **Timeline**: activity histogram since 2012 - pick 7 d / 30 d / 90 d / 1 yr / all, drag along it, or press ▶ to replay
* **Alerts**: AIS switched off, impossible moves, encounters and loitering, identity changes - click a row to jump there
* **Search**: ship name, IMO, MMSI, a former name, or a port

### Team Capstone Database (`data/Arctic_Capstone_Database.xlsx`)
Export from Airtable and replace this file; the next run picks it up (`tracker/capstone_loader.py`).
* **Ships** (registry of 4,000+ Russian-flagged ships) → the viewer's **Fleet** tab: counts by type, search by name/IMO/type
* Ships with a linked **AIS update, 3D model or image** count as *tracked* and are **added to the watch list automatically**,
  so the daily run downloads their Global Fishing Watch history too
* **AIS Updates** → "Manual AIS checks" on the ship card. If you fill in **Latitude/Longitude**, the check is also plotted
  on the map and gives the prediction a real speed and course
* **SAR / Satellite Optical Imagery** → image markers on the map (position read from the Geolocation / Location text, e.g. `77.5N 68.2E`)
* **3D Models** → links on the ship card

### Sea routes
`data/sea_routes.geojson` (made by `python tools/make_sea_routes.py`) draws the Northern Sea Route, North-East and North-West
Passages, a future transpolar route, Russian river/gulf feeder lines and the Arctic Circle. They are **schematic** - hand-placed
waypoints through the straits and seas each route uses - for orientation only, not navigation. Edit the waypoints in the script.

### Adding to the viewer (code in `web/`)
| To add... | Edit |
|---|---|
| a basemap or overlay (any {z}/{x}/{y} tile service or NASA layer) | `BASEMAPS` / `OVERLAYS` in `web/js/config.js` |
| a ship type (e.g. icebreakers, container ships) and its colour | `SHIP_CATEGORIES` in `web/js/config.js` |
| new information on a ship or port card | one function in `VESSEL_SECTIONS` / `PORT_SECTIONS` in `web/js/cards.js` |
| a new alert table | `ALERT_TABS` in `web/js/ui.js` |
| a new data file | `loadAll()` in `web/js/data.js` (and write it from `run_update.py`) |
| more ports | `data/reference_ports.csv` (coordinates there are approximate - verify) or the Excel Ports tab |

To try changes locally: `python -m http.server` in the repo folder, then open http://localhost:8000

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

## How the tracking algorithm stays reliable

| Piece | File | What it does |
|---|---|---|
| **Position at any moment** | `tracker/interpolate.py` | `position_at(track, time)` gives an exact, interpolated or projected position, or the last known one, each with an uncertainty in km. `candidates_near(df, lat, lon, time)` lists which watch-list ships could be at a spot at that moment. This is what an image detection gets matched against. |
| **Sun position** | `tracker/sun.py` | Sun elevation and azimuth at a time and place, for comparing satellite images to the 3D-print photo sessions |
| **Only new data** | `pipeline.update_data()` | Daily runs download each ship's events from its last saved date minus 30 days. New ships get their full history. Run `python run_update.py --full` to re-download everything. Re-downloaded GFW events replace their old copy (matched by event id). |
| **Data checks** | `tracker/validation.py` | Every run checks IMO check digits, MMSIs, coordinates against named places (catches E/W mix-ups), date-only times, image-name vs. date mismatches, missing coordinates and duplicates. Results go to `data/output_data_checks.csv` and the viewer (Alerts → Data checks). |
| **Area-only AIS checks** | `data/region_gazetteer.csv` | Checks with an area name but no lat/lon (e.g. *Barents Sea (Murmansk)*) are placed at the area's centre with its radius as the error (Murmansk ±20 km, Barents Sea ±700 km). They show on the map as hollow amber rings, but are kept out of speed, "impossible move" and prediction maths. Add new area names to the CSV; the data checks list any that aren't recognised. |
| **Live Airtable** | `tracker/airtable.py` | With an `AIRTABLE_TOKEN` secret, every run reads the Capstone base directly instead of the Excel export, and `upsert()` can write results back. |

**Fields to add in Airtable** (ship dimensions, exact UTC times, SAR scenes, detections and matches, 3D-print capture
sessions): see [`docs/airtable_fields.md`](docs/airtable_fields.md).

## Satellite imagery: pings → scenes → detections

Every daily run turns suspicious tracking events into **pings** (`tracker/cueing.py`). Each ping is a place, a time window
and a reason: AIS switched off, an impossible move (possible spoofing), a change of identity, or a long gap in records.
The run then:

1. **Finds free satellite images** over each ping (`tracker/sources/copernicus.py`): Sentinel-1 radar (works through
   cloud and polar night) and Sentinel-2 optical (with the sun's elevation and azimuth worked out for shadow analysis).
   No account needed.
2. **Pulls a small radar image crop** when the ship's position at the image time is known within about 15 km
   (`tracker/sar_chips.py`). This needs a free Copernicus account; add `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET` as GitHub secrets.
3. **Detects and measures ships** in the crop (`tracker/sar_detect.py`): length, width and heading, each with an error.
4. **Scores which watch-list ship each detection could be** (`tracker/match.py`), using position, plus length and beam
   once they're in the Airtable Ships table. A detection with no plausible AIS ship is flagged as a **possible dark vessel**.

Results: `data/output_cues.csv`, `output_scenes.csv`, `output_detections.csv`, crops in `data/chips/`. In the viewer, click
**Pings** in the header. Shadow geometry for optical and 3D-print photos is in `tracker/shadow.py`.

**Licensing.** Sentinel data is free and open: credit "Contains modified Copernicus Sentinel data". **GEGD / Maxar imagery is
licensed for US Government purposes (USG-Plus)** and must not be added to this public repository or website.

## Lab reference: 3D-print photos → SAR ↔ optical signatures

`lab/` turns the turntable photos of the 3D-printed ships into the reference that satellite detections are compared against.
It runs on your own computer; photos and outputs stay there (`lab_out/` isn't uploaded).

    python -m lab.ingest --images "D:/Lab Photos"     # sun / camera / rotation from folder names
    python -m lab.annotate --preview 5 --debug-save-dir lab_out/debug
    python -m lab.annotate && python -m lab.validate && python -m lab.reference build

Step by step guide, settings to measure on the print, and what every number means: **[lab/README.md](lab/README.md)**.

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
