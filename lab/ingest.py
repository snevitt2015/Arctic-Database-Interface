"""
ingest.py  -  Find every photo and work out its sun angle, camera angle and rotation.
Writes a manifest CSV that the other lab steps read.

Three ways to give the settings (it tries them in this order):

  1. --csv my_list.csv   your own table: filepath, vessel_name, sun_angle, cam_angle, rotation
  2. Folder names        like your sessions:  10-23-26_Sun15Camera20/IMG_6383.JPG
                         (also Sun15_Cam20, sun15/cam20, ... ; a "rot180" or "R180" folder or
                          filename part sets the rotation)
  3. Rotation from order if no rotation is written anywhere: photos in a session folder,
                         sorted by filename, are 0°, 10°, 20°... (rotation_step_deg in settings)

    python -m lab.ingest --images "D:/Ship Photos" --dry-run
    python -m lab.ingest --images "D:/Ship Photos" --out lab_out
    python -m lab.ingest --csv my_list.csv --out lab_out
"""
import argparse
import re
from pathlib import Path

import pandas as pd

from lab import settings as S

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
RX_SUN = re.compile(r"sun[\s_\-]*(\d+(?:\.\d+)?)", re.I)
RX_CAM = re.compile(r"cam(?:era)?[\s_\-]*(\d+(?:\.\d+)?)", re.I)
RX_ROT = re.compile(r"(?:rot(?:ation)?|(?<![a-z])r)[\s_\-]*(\d+(?:\.\d+)?)(?:deg)?(?![\d])", re.I)
RX_DATE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2,4})")

CSV_ALIASES = {
    "image_path": ["filepath", "file", "path", "image", "image_path"],
    "vessel": ["vessel_name", "vessel", "ship", "model"],
    "sun_elevation": ["sun_angle", "sun", "sun_elevation", "sun_deg"],
    "camera_angle": ["cam_angle", "camera_angle", "cam", "camera"],
    "rotation": ["rotation", "rot", "heading", "rotation_deg"],
}


def parse_path(path: Path, root: Path) -> dict:
    """Read sun / camera / rotation / date / vessel from the folder and file names."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    text_dirs = " / ".join(parts[:-1])
    out = {"sun_elevation": None, "camera_angle": None, "rotation": None, "capture_date": "", "vessel": ""}
    for rx, key in [(RX_SUN, "sun_elevation"), (RX_CAM, "camera_angle")]:
        m = rx.search(text_dirs) or rx.search(path.stem)
        if m:
            out[key] = float(m.group(1))
    # rotation only from an explicit "rot.." token (folder or filename), never from IMG_6383
    for token in parts[:-1] + [path.stem]:
        m = RX_ROT.search(token)
        if m and not token.upper().startswith("IMG"):
            out["rotation"] = float(m.group(1)) % 360
    m = RX_DATE.search(text_dirs)
    if m:
        mo, d, y = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        out["capture_date"] = f"{y:04d}-{int(mo):02d}-{int(d):02d}"
    return out


def from_folders(root: Path, vessel_name: str, settings: dict) -> pd.DataFrame:
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))
    rows = []
    for p in files:
        meta = parse_path(p, root)
        known_vessels = [v for v in settings["vessels"] if v.lower() in str(p.relative_to(root)).lower()]
        rows.append({"image_path": str(p), "session": str(p.parent.relative_to(root)),
                     "vessel": known_vessels[0] if known_vessels else vessel_name, **{k: v for k, v in meta.items() if k != "vessel"}})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # rotation from filename order inside each session when not written anywhere
    step = settings["rotation_step_deg"]
    df["rotation_source"] = df["rotation"].map(lambda r: "name" if pd.notna(r) else "")
    df["index_in_session"] = df.groupby("session").cumcount()
    df["n_in_session"] = df.groupby("session")["image_path"].transform("count")
    missing = df["rotation"].isna()
    df.loc[missing, "rotation"] = (df.loc[missing, "index_in_session"] * step) % 360
    df.loc[missing, "rotation_source"] = f"order x {step}°"
    df["metadata_source"] = "folders"
    return df


def from_csv(csv_path: Path, settings: dict) -> pd.DataFrame:
    raw = pd.read_csv(csv_path)
    low = {c.lower().strip(): c for c in raw.columns}
    df = pd.DataFrame()
    for std, aliases in CSV_ALIASES.items():
        col = next((low[a] for a in aliases if a in low), None)
        df[std] = raw[col] if col else None
    base = csv_path.parent
    df["image_path"] = [str(p if Path(p).is_absolute() else (base / p)) for p in df["image_path"]]
    df["vessel"] = df["vessel"].fillna(settings["default_vessel"])
    df["session"] = [f"sun{s:g}_cam{c:g}" if pd.notna(s) and pd.notna(c) else "unknown"
                     for s, c in zip(pd.to_numeric(df["sun_elevation"], errors="coerce"),
                                     pd.to_numeric(df["camera_angle"], errors="coerce"))]
    df["capture_date"] = ""
    df["rotation_source"] = "csv"
    df["metadata_source"] = "csv"
    return df


def check(df: pd.DataFrame, settings: dict) -> list:
    """Problems worth fixing before running the slow steps."""
    problems = []
    for col, label in [("sun_elevation", "sun angle"), ("camera_angle", "camera angle")]:
        n = pd.to_numeric(df[col], errors="coerce").isna().sum()
        if n:
            problems.append(f"{n} photos have no {label} - check folder names or use --csv")
    missing = [p for p in df["image_path"] if not Path(p).exists()]
    if missing:
        problems.append(f"{len(missing)} files listed but not found (first: {missing[0]})")
    step = settings["rotation_step_deg"]
    full = 360 // step
    if "n_in_session" in df:
        odd = df[(df["rotation_source"].str.startswith("order")) & (df["n_in_session"] != full)]
        for s, g in odd.groupby("session"):
            problems.append(f"session '{s}' has {len(g)} photos, not {full} - rotations from order may be off "
                            f"(add rot### to names or use --csv)")
    unknown = set(df["vessel"]) - set(settings["vessels"])
    if unknown:
        problems.append(f"vessel(s) {sorted(unknown)} not in lab/settings.py - add their scale and heights")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--images", help="top folder with your session folders")
    src.add_argument("--csv", help="CSV with filepath, vessel_name, sun_angle, cam_angle, rotation")
    ap.add_argument("--vessel", help="vessel name if not in folder names (default from settings)")
    ap.add_argument("--out", default="lab_out", help="output folder (default lab_out)")
    ap.add_argument("--settings", help="JSON with your set-up numbers (see lab/settings.py)")
    ap.add_argument("--dry-run", action="store_true", help="only show what was found; write nothing")
    a = ap.parse_args(argv)
    st = S.load(a.settings)
    df = from_csv(Path(a.csv), st) if a.csv else from_folders(Path(a.images), a.vessel or st["default_vessel"], st)
    if df.empty:
        print("No images found.")
        return 1
    print(f"Found {len(df)} photos in {df['session'].nunique()} sessions")
    summary = df.groupby(["session"]).agg(photos=("image_path", "size"), vessel=("vessel", "first"),
                                          sun=("sun_elevation", "first"), camera=("camera_angle", "first"),
                                          date=("capture_date", "first"), rotation=("rotation_source", "first"))
    print(summary.to_string())
    print("\nFirst 5 photos:")
    for _, r in df.head(5).iterrows():
        print(f"  {Path(r['image_path']).name:16} vessel={r['vessel']} sun={r['sun_elevation']} cam={r['camera_angle']} "
              f"rot={r['rotation']} ({r['rotation_source']})")
    problems = check(df, st)
    print("\n" + ("Problems:\n  - " + "\n  - ".join(problems) if problems else "No problems found."))
    if a.dry_run:
        print("\n(dry run - nothing written)")
        return 0
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "manifest.csv", index=False)
    print(f"\nWrote {out / 'manifest.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
