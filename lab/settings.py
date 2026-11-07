"""
settings.py  -  Everything about the physical setup. Edit the numbers here (or pass
--settings my_setup.json with the same keys to any lab script).

Measure these once on the real set-up. The physics checks (Module 2) and the
SAR <-> optical reference are only as good as these numbers.
"""
import json
from pathlib import Path

DEFAULTS = {
    # ---- the model(s): one entry per 3D print. Key = vessel name used in folders/CSV.
    "vessels": {
        "MossTypeLNG": {
"scale_denominator": 700,      # 1:700 print - CHANGE to the real scale
"model_length_mm": 410.0,      # hull length of the print - CHANGE
"model_beam_mm": 70.0,         # hull width of the print - CHANGE
"shadow_height_mm": 60.0,      # height of the tallest tank above the board - CHANGE
                                           # (top of the tallest tank/superstructure above the board) - CHANGE
            "real_imo": "",                # IMO of the real ship this model represents, if any
        },
    },
    "default_vessel": "MossTypeLNG",

    # ---- camera
    # How "Camera20".."Camera45" is measured:
    #   "elevation"  = angle of the camera ABOVE the board (90 = straight down)
    #   "off_nadir"  = angle FROM straight down (0 = straight down) - same as satellites use
    "camera_angle_convention": "elevation",
    # In the photo, which way is the camera looking? Lengths along this image direction are
    # foreshortened. "up" = far side of the board is at the top of the image (normal set-up).
    "camera_look_direction_in_image": "up",

    # ---- rotations: if photos in a session folder are a 360° sweep in filename order
    "rotation_step_deg": 10,

    # ---- turntable ring
    # "auto" = find the ring (an ellipse in the photo) automatically, "none" = no ring,
    # or [x_frac, y_frac, r_frac] = centre and radius as fractions of image width/height/width
    "ring": "auto",
    "ring_band_px": 18,                # half-width of the band around the ring edge to ignore (at working size)

    # ---- processing
    "work_max_side_px": 1600,          # photos are shrunk to this for speed; outputs are scaled back
    "background_rms_limit": 3.0,       # Lab units; above this the board is uneven / dirty
}


def load(path=None) -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    if path:
        user = json.loads(Path(path).read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(s.get(k), dict):
                s[k].update(v)
            else:
                s[k] = v
    return s


def vessel(settings: dict, name: str) -> dict:
    v = settings["vessels"].get(name)
    if v is None:
        v = settings["vessels"][settings["default_vessel"]]
    return v


def off_nadir(settings: dict, camera_angle: float) -> float:
    """Camera angle from straight down (0 = vertical), whatever the folder convention is."""
    if camera_angle is None:
        return None
    return 90.0 - camera_angle if settings["camera_angle_convention"] == "elevation" else camera_angle
