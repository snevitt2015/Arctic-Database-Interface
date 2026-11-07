# Lab: 3D-print photos → SAR ↔ optical reference

This folder turns the turntable photos of the 3D-printed ships into measurements. Those measurements become the reference library that satellite detections get compared against.

```
photos ─► 0 ingest ─► 1 annotate ─► 2 validate ─► reference ─► (tracker match score)
                          │
                          └─► YOLO dataset ─► 3 train / predict
```

| Step | Command | What you get |
|---|---|---|
| 0 ingest | `python -m lab.ingest --images "D:/Lab Photos"` | `lab_out/manifest.csv`: sun, camera, rotation and date for every photo, read from the folder names |
| 1 annotate | `python -m lab.annotate` | `vessel_shadow_metrics.csv`, plus a YOLO-OBB dataset and debug overlays |
| 2 validate | `python -m lab.validate` | `validated_metrics.csv`, `session_summary.csv` and `height_fit.csv`: physics checks and the effective tank height |
| reference | `python -m lab.reference build` | `optical_reference.csv` and `coverage.csv`: scale-free signatures, plus which angles are still unshot |
| cross-cue | `python -m lab.crosscue` | `crosscue_selftest.csv` and `crosscue_scenes.csv` (see below) |
| 3 train | `python -m lab.train train` / `predict` | A trained detector and `predicted_metrics.csv` in the same column format |

Everything is written to `lab_out/`, which is not uploaded to GitHub. The photos stay on your computer.

## The quick way: one command (or F5 in IDLE)

Point it at the folder that **holds** your session folders. It reads the folder and file names, runs every step below, then runs the cross-cue test.

**In IDLE:**

1. Open `lab_run.py` (in the project folder).
2. Paste your folder path into `PHOTO_FOLDER`.
3. Press **F5**.

`PREVIEW = 5` measures just 5 photos so you can check the overlays in `lab_out\debug`. Set it to `0` to run everything.

**From a terminal:**

```
python -m lab.run "C:\Users\you\Downloads\Lab Photos" --preview 5
python -m lab.run "C:\Users\you\Downloads\Lab Photos"
```

**Re-running is quick.** Sessions measured before are reused from `lab_out\cache`, so only new folders take time. A session is measured again automatically if you add, remove or edit a photo in it, change `lab/settings.py`, or download new code. To force everything again, add `--redo`:

```
python -m lab.run "C:\Users\you\Downloads\PhotosFolder" --redo
```

### Reading the cross-cue test (`lab.crosscue`)

**A. Self-test (`crosscue_selftest.csv`).** Every 5th turntable position is held back. Each held-back photo is scaled up to real size and treated as a satellite sighting, with Sentinel-2 (10 m) and very-high-res (0.5 m) measurement noise added. It is then scored against four hypotheses: the right ship, 20% shorter, 20% longer, and a half-height ship.

- `height_told`: how often the shadow alone tells a Moss carrier from a lower ship.
- `hit_rate`: how often shadow plus measured length pick the right ship.

Expect both to be best at low sun, where shadows are long, and weakest at high sun.

**B. Scene cross-cue (`crosscue_scenes.csv`).** For every ping in the daily run (AIS off, spoofing, silent), it lists:

- the SAR and optical images found, and the closest SAR + optical pair in time;
- what the lab predicts the ship should look like: the shadow length at that image's sun angle, and SAR layover and radar shadow;
- which lab session to compare against, marked **NOT SHOT YET** if you haven't photographed that angle.

The lab model is the Moss LNG print, so only LNG tankers get numbers. Put real ship lengths in `data/vessel_dimensions.csv` (columns `imo,name,length_m,beam_m,containment`). Until then, LNG tankers are assumed to be 290 m. Run `git pull` first so you have the latest daily pings and images.

## One-time setup (Windows, in the project folder)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install ultralytics          # only for step 3
```

## Before the first run: measure the print

Open `lab/settings.py` and replace the numbers marked `CHANGE`:

- `model_length_mm`: hull length, measured with calipers.
- `model_beam_mm`: hull width.
- `shadow_height_mm`: height of the tallest tank above the board.
- `scale_denominator`: 700 for a 1:700 print.

Module 2 measures the effective height from the shadows anyway, so a rough value is fine to start. The length matters most, because it sets the mm-per-pixel scale.

Also set `rotation_step_deg` to the angle the turntable turns between photos. Rotation is worked out from the photo order inside each session folder (1st photo = 0°, 2nd = 10°, and so on). So **start every 360° sweep from the same mark** (e.g. bow pointing at the camera). Without that, rotation 90° in one session won't mean the same heading as in another, and Module 2 can't compare them.

## Folder names

The folder names you already use work as they are:

```
10-23-26_Sun15Camera20/IMG_6383.JPG
10-26-26Sun20Camera35/IMG_6501.JPG
11-3-26Sun25Camera25/IMG_7002.JPG
```

The date is month-day-year, read from the start of the folder name. The IMG numbers are never treated as angles.

## First run: preview before the full batch

```
python -m lab.ingest   --images "D:/Lab Photos" --dry-run           # check what it read
python -m lab.ingest   --images "D:/Lab Photos"
python -m lab.annotate --preview 5 --debug-save-dir lab_out/debug   # 5 photos spread over sessions
```

Open `lab_out/debug/*.jpg`. Each overlay is colour-coded:

- **blue outline**: the hull
- **green outline**: the shadow
- **light green**: shadow pixels bridged across the turntable ring
- **magenta**: the ring band (ignored when measuring)
- **red**: the two oriented boxes that become YOLO labels
- **yellow arrow**: how far the shadow sticks out past the hull (this is what is measured)

If those look right, run the full set:

```
python -m lab.annotate --debug-save-dir lab_out/debug
python -m lab.validate
python -m lab.reference build
```

## How the ring around the ship is handled

The turntable ring (the dolly) appears in the photo as a thin ellipse that cuts through the shadow. Annotate deals with it in four ways:

1. It finds the ellipse from many photos of the session and takes the median.
2. It ignores a narrow band along the ellipse.
3. It joins the shadow pieces back together across that band.
4. It never counts the ring's lip as part of the hull.

If the ring isn't found, give its position by hand: `--ring x,y,rx,ry` (pixels).

## What the numbers mean

- **reach_ground_mm**: how far the shadow sticks out past the hull, measured on the board after removing the camera tilt.
- **K_ground**: reach divided by `shadow_height_mm / tan(sun)`. A value of 1.0 means the shadow is exactly as long as the settings height predicts.
- **effective_height_mm** (in `height_fit.csv`): the slope of reach against cot(sun), fitted for each rotation over every sun angle you've shot. This is the model's real shadow-casting height. Times the scale, it is the height a satellite should see.
- **flags**:
  - `k_outlier`: this photo disagrees with the rest of its session; it is dropped.
  - `trend_break`: the shadow got longer as the sun rose, which can't happen; check the overlay.
  - `partial`: only the hull or only the shadow was found.

## The SAR ↔ optical link

- **Optical satellites** (e.g. Sentinel-2): the shadow follows the same rule as the lab, scaled by ship length. `R.shadow_score(...)` gives a 0–1 `p_shadow` that multiplies into `tracker/match.py`'s score.
- **SAR** (Sentinel-1) has no sun shadow, but the same height produces:
  - layover, which shifts toward the satellite by h / tan(incidence);
  - radar shadow, which extends away from it by h · tan(incidence).

  `R.sar_expectation(...)` gives both.
- **Camera angle ↔ incidence**: the lab camera's angle from straight down (90 − camera elevation) corresponds to the satellite's incidence angle. Sentinel-1 IW images at 29–46°, which is camera elevation 44–61°. Today only Camera45 overlaps, so add Camera50–60 sessions if you can.

`python -m lab.reference build` prints a sun × camera grid of what's shot and what's missing.

## Trying it without photos

```
python -m lab.synthetic lab_out/synthetic        # 36 fake photos, 3 sun angles
python -m lab.annotate --manifest lab_out/synthetic/manifest.csv --out lab_out/synthetic/out --debug-save-dir lab_out/synthetic/debug
python -m lab.validate --metrics lab_out/synthetic/out/vessel_shadow_metrics.csv --out lab_out/synthetic/out
```

On the fake photos, the true tank height is 60 mm and Module 2 recovers about 59 mm. The typical single-photo error is 5–8 mm. The few photos where a piece of shadow is missed come out as `k_outlier` and are dropped.

## Troubleshooting

- **background rms > 3**: the board is uneven or dirty, or the lighting has a strong gradient. Even out the lighting or clean the board.
- **The hull is drawn on only part of the ship (blue too small)**: the print's hull is about as dark as the shadow. A lighter print colour or more fill light helps most.
- **Status REJECT, no_shadow**: the sun is too high or the shadow is hidden under the hull. This is expected at some rotations.
- **Rotations look scrambled across sessions**: a session has a missing or extra photo. Add a `rotation` column with `--csv` (see `python -m lab.ingest -h`).
