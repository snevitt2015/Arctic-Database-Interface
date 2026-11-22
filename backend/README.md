# Arctic Vessel Watch — backend v2

API server and database for the Arctic AIS-spoofing monitor. It replaces the v1 "daily script writes
CSVs, viewer reads static files" setup with a real service the interface can query live.

```
 OSINT feeds                      backend (this folder)                          interface tabs
 ───────────                      ─────────────────────                          ──────────────
 aisstream.io (live)  ─┐
 Kystverket AIS (live) ┼─► ingest ─► positions ─► spoofing detectors ─► anomalies ─► Spoofing
 GFW gaps/encounters  ─┤                │                                         ─► Overview map
 MarineTraffic/TankerMap CSV ┘          ▼                                         ─► Live OSINT feed
                                    vessels + identities
 Sentinel-1 chip / GFW SAR ─► CFAR ─► SAR detections ─┐
 optical measurement (+ shadow) ──────────────────────┴─► comparative ─► cards ─► SAR ⇄ optical
```

## Run it (Windows PowerShell, from the backend folder)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```
Open http://localhost:8000/docs. The server keeps running in that window; Ctrl+C stops it.

**Data policy: only real data.** The database (`arctic.db`) holds only records from real sources.
Nothing is generated, simulated or filled in. The automated tests use test-only cases kept inside
`tests/`, which refuse to run outside pytest and never touch `arctic.db`.

Loading the team's real data from the v1 repo (watch list, 4,170-ship registry, GFW history since 2012):
```
.venv\Scripts\python scripts\import_v1.py --v1 "C:\path\to\Arctic-Database-Interface"
```
Live and ongoing sources:
```
.venv\Scripts\python -m app.workers.live --feed aisstream     # needs AISSTREAM_API_KEY in .env
.venv\Scripts\python -m app.workers.live --feed norway        # NORWAY_AIS_ENABLED=true in .env
.venv\Scripts\python -m app.workers.gfw_sync                  # needs GFW_API_TOKEN in .env
.venv\Scripts\python -m pytest -q                             # tests
```
Load the team registry (dimensions, air draft, flags, watch list) with
`POST /api/vessels/import` (CSV/XLSX; column names are matched flexibly, see `ingest/csv_import.py`).
**The comparative is only as good as the registry:** length, beam and air draft are what it compares against,
and the current registry has none of them yet.

## Endpoints by tab

| Tab | Endpoint | What it gives |
|---|---|---|
| Overview | `GET /api/overview` | headline counts, anomalies by kind/severity, SAR verdicts, top-10 risk ships |
| | `GET /api/overview/map?layers=vessels,anomalies,sar` | one GeoJSON for the world map; `properties.layer` picks the style |
| Live OSINT | `GET /api/live/positions` | latest position per ship (GeoJSON) |
| | `GET /api/live/feed?after_id=` | incoming pings, newest first (or poll with after_id) |
| | `WS /ws/live` | pushes new pings every 2 s |
| | `GET /api/live/sources` | each feed: on/off, positions, last delivery |
| | `POST /api/live/import` | upload a MarineTraffic / TankerMap export |
| Spoofing | `GET /api/spoofing/anomalies?kind=&severity=&status=` | the findings list |
| | `GET /api/spoofing/anomalies/{id}` | one finding + the pings around it (evidence) |
| | `PATCH /api/spoofing/anomalies/{id}` | analyst status: reviewed / confirmed / dismissed |
| | `GET /api/spoofing/anomalies/{id}/imagery` | Sentinel-1/2 scenes covering it (cross-cue) |
| | `GET /api/spoofing/summary` | counts + per-day timeline for charts |
| SAR ⇄ optical | `POST /api/sar/process-chip` | radar chip in → detections → comparison cards out |
| | `POST /api/sar/detections` | detections from GFW SAR / analysts |
| | `POST /api/sar/optical` | optical measurement incl. shadow → comparison re-runs |
| | `GET /api/sar/detections/{id}` | the comparison card |
| | `GET /api/sar/sun?lat=&lon=&when=` | is an optical shadow even possible? |
| Shared | `GET /api/vessels/{id}`, `/track` | profile, every identity used, OSINT deep links, track |

## Spoofing detectors (`app/analytics/spoofing.py`)

| Kind | Fires when |
|---|---|
| `ais_gap` | silent > 6 h; notes distance moved, impossible reappearance, or *stopped while dark* (possible STS) |
| `on_off_cycling` | ≥ 3 silences of ≥ 45 min inside 24 h |
| `teleport` | next position needs > 30 kn; a single outlier-and-back is called a fake position |
| `mmsi_collision` | track flips between two places repeatedly: two transmitters, one MMSI |
| `speed_mismatch` | reported SOG ≠ distance actually covered (fabricated track) |
| `identity_change` | name / IMO / callsign changes under one MMSI (spacing/case ignored) |
| `imo_clone` | one IMO broadcast by different MMSIs far apart at the same time |
| `invalid_id` | placeholder or non-ship MMSI, MMSI country ≠ reported flag, bad IMO check digit |
| `encounter`, `loitering` | from GFW |

GFW records are events days apart, not a continuous AIS stream, so gap / on-off / speed detectors run
only on live AIS feeds; GFW's own AIS-off events are stored as reported. Thresholds are in `.env`. After changing them: `POST /api/spoofing/rerun?full=true` (analyst statuses are kept).
**Coverage caveat:** terrestrial feeds lose ships offshore, which looks like a gap. Each anomaly lists its
`sources`; GFW (satellite AIS) gaps are the stronger evidence.

## SAR ⇄ optical comparative (`app/analytics/comparative.py`)

1. Fuse SAR + optical length/beam (weighted by each sensor's precision).
2. Shadow: sun elevation/azimuth at the optical time → **height = shadow × tan(elevation)**; shadow direction
   must match sun azimuth + 180° (± 20°) or the measurement is rejected. Each candidate gets the shadow its
   registry air draft *should* cast.
3. Check that the optical target is where the SAR target could have sailed in between.
4. Score AIS candidates on position, length, beam, height, heading (stale AIS counts as weak evidence).
5. Verdict: `match` · `probable` · `identity_mismatch` (an AIS ship is there but its hull doesn't fit) ·
   `dark` (nothing broadcasting; look-alikes from the registry are listed, and location spoofing is flagged
   only when hull *and* shadow height point to one ship whose AIS is elsewhere) · `ambiguous`.

Polar night: north of ~70°N there is no usable sun from late November to mid-January; cards say so and
fall back to SAR + AIS.

## Layout

```
app/
  main.py            API server
  config.py          settings from .env
  db.py, models.py   database (SQLite dev / Postgres prod)
  analytics/         pure maths, no database: spoofing, comparative, identity, shadow, sun,
                     sar_detect + clustering (CFAR, L-shape fit), interpolate, geo
  ingest/            aisstream, norway_ais, gfw, csv_import, copernicus
  services/          ingest.py (store + detect), sar.py (chip → detections → comparison)
  api/               one router per tab
  workers/           long-running feed workers
scripts/import_v1.py  loads the real v1 data (watch list, registry, GFW history)
tests/               32 tests (test-only cases, isolated from arctic.db)
```

v1 code (`tracker/`, `web/`, `run_update.py`) is untouched. Its sun, shadow, SAR-detection and interpolation
code was carried over as-is.
