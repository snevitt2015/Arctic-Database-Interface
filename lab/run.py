"""
run.py  -  Everything in one go: point it at the folder with your photos.

    python -m lab.run "C:/Users/you/Downloads/Lab Photos"
    python -m lab.run "C:/Users/you/Downloads/Lab Photos" --preview 5     # quick look first

It reads the session folders (e.g. 11-3-26Sun25Camera25/IMG_7002.JPG), then runs
    ingest -> annotate (Module 1) -> validate (Module 2) -> reference -> cross-cue test
and writes everything to lab_out/ in the project folder (photos are never changed).

No command line? Open lab_run.py (in the project folder) in IDLE, paste your folder path
into PHOTO_FOLDER, and press F5.
"""
import argparse
import time
from pathlib import Path

from lab import ingest, annotate, validate, reference, crosscue

REPO = Path(__file__).resolve().parents[1]


def run(photo_folder, out=None, preview=0, debug=True, settings=None):
    folder = Path(str(photo_folder).strip().strip('"'))
    if not folder.is_dir():
        print(f"Can't find the folder: {folder}\nCopy the path from File Explorer's address bar.")
        return 1
    out = Path(out) if out else REPO / "lab_out"
    s = ["--settings", settings] if settings else []
    t0 = time.time()

    def step(title):
        print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)

    step(f"0. Reading photos in {folder}")
    if ingest.main(["--images", str(folder), "--out", str(out)] + s):
        return 1
    manifest = str(out / "manifest.csv")

    if preview:
        step(f"1. Preview: measuring {preview} photos (overlays in {out / 'debug'})")
        annotate.main(["--manifest", manifest, "--out", str(out), "--preview", str(preview),
                       "--debug-save-dir", str(out / "debug")] + s)
        print(f"\nOpen the pictures in {out / 'debug'}. If the blue (hull) and green (shadow) outlines "
              "look right, run again without --preview for the full set.")
        return 0

    step("1. Measuring hull and shadow in every photo (Module 1) - about 1-2 s per photo")
    annotate.main(["--manifest", manifest, "--out", str(out)]
                  + (["--debug-save-dir", str(out / "debug")] if debug else []) + s)

    step("2. Checking against sun geometry (Module 2)")
    validate.main(["--metrics", str(out / "vessel_shadow_metrics.csv"), "--out", str(out)] + s)

    step("3. Building the SAR <-> optical reference")
    reference.main(["build", "--validated", str(out / "validated_metrics.csv"),
                    "--heights", str(out / "height_fit.csv"), "--out", str(out)] + s)

    step("4. Cross-cue test")
    crosscue.main(["--lab", str(out)] + s)

    print(f"\nDone in {(time.time() - t0) / 60:.1f} min. Results are in {out}:")
    for f in ["vessel_shadow_metrics.csv", "validated_metrics.csv", "height_fit.csv", "optical_reference.csv",
              "coverage.csv", "crosscue_selftest.csv", "crosscue_scenes.csv"]:
        if (out / f).exists():
            print(f"  {f}")
    if debug:
        print(f"  debug/  (one overlay per photo)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the whole lab pipeline on a photo folder")
    ap.add_argument("folder", help="the folder that holds your session folders")
    ap.add_argument("--out", default=None, help="output folder (default: lab_out in the project)")
    ap.add_argument("--preview", type=int, default=0, help="only measure N photos and save their overlays")
    ap.add_argument("--no-debug", action="store_true", help="don't save an overlay for every photo")
    ap.add_argument("--settings", default=None)
    a = ap.parse_args(argv)
    return run(a.folder, a.out, a.preview, not a.no_debug, a.settings)


if __name__ == "__main__":
    raise SystemExit(main())
