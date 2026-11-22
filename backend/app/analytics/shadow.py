"""
shadow.py  -  Shadow geometry for optical images and the 3D-print lab photos.

On flat water, an object of height H casts a shadow of length L on the ground plane:

        L = H / tan(sun elevation)          H = L * tan(sun elevation)

and the shadow points directly AWAY from the sun:   shadow azimuth = sun azimuth + 180°.

Low Arctic sun = long shadows: a 48 m superstructure casts ~ 132 m of shadow at 20°
elevation but only ~ 40 m at 50°. That's why the lab steps (sun 20-50°) matter.

The satellite's viewing angle doesn't change the shadow on an orthorectified image
(the shadow lies on the sea surface), but it makes TALL parts of the ship lean away
from the satellite ("relief displacement" = H * tan(off-nadir)). Measure shadow from
where it meets the hull at the waterline, not from the top of the leaning mast.
"""
import math


def height_from_shadow(shadow_m: float, sun_elevation_deg: float) -> float:
    """Object height from shadow length on flat water."""
    if sun_elevation_deg <= 0:
        raise ValueError("Sun is below the horizon - no shadow to measure")
    return shadow_m * math.tan(math.radians(sun_elevation_deg))


def shadow_from_height(height_m: float, sun_elevation_deg: float) -> float:
    """Expected shadow length for a known height (e.g. a ship's air draft from the registry)."""
    if sun_elevation_deg <= 0:
        return float("inf")
    return height_m / math.tan(math.radians(sun_elevation_deg))


def shadow_azimuth(sun_azimuth_deg: float) -> float:
    """Compass direction the shadow points (away from the sun)."""
    return (sun_azimuth_deg + 180) % 360


def height_error(shadow_m, shadow_err_m, sun_elevation_deg, sun_elev_err_deg=0.5) -> float:
    """How uncertain the height is, from the shadow-length and sun-angle errors."""
    e = math.radians(sun_elevation_deg)
    dh_dl = math.tan(e)
    dh_de = shadow_m / math.cos(e) ** 2
    return math.hypot(dh_dl * shadow_err_m, dh_de * math.radians(sun_elev_err_deg))


def relief_displacement(height_m: float, off_nadir_deg: float) -> float:
    """How far the top of a tall object appears shifted from its base in a satellite image."""
    return height_m * math.tan(math.radians(off_nadir_deg))


def lab_to_real(measured_mm: float, scale_denominator: float) -> float:
    """3D-print measurement to real size: 1:700 print, 427 mm -> 298.9 m."""
    return measured_mm * scale_denominator / 1000.0
