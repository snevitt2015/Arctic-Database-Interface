"""
records.py  -  Check the team's imagery and 3D-model records before they are used as evidence.

Each check returns {"level": "blocking" | "warning", "text": ...}. A record with any blocking problem is
shown in the interface but never used in a confidence score until someone fixes it.
"""
import re
from urllib.parse import urlparse

# Real provider domains. A link to anything else (e.g. "copernicus85d3db1b.eu") is not a real source.
KNOWN_HOSTS = ("copernicus.eu", "esa.int", "dlr.de", "eodms-sgdot.nrcan-rncan.gc.ca", "nrcan.gc.ca", "asf.alaska.edu",
               "maxar.com", "planet.com", "airbus.com", "iceye.com", "capellaspace.com", "umbra.space",
               "nasa.gov", "usgs.gov", "sentinel-hub.com", "globalfishingwatch.org",
               "renderhub.com", "sketchfab.com", "cgtrader.com", "turbosquid.com", "3dmodels.org", "free3d.com",
               "drive.google.com", "docs.google.com", "airtable.com", "github.com")


def host_ok(url) -> bool:
    host = (urlparse(str(url)).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in KNOWN_HOSTS)


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if len(w) > 2} - {
        "ship", "model", "tanker", "the", "lng", "oil", "crude", "cargo", "general", "quality", "hquality", "project",
        "icebreaker", "icebreakers", "models", "terminal", "vessel"}


def check_imagery(rec: dict, ship_name: str = None) -> list:
    out = []
    link = rec.get("link")
    if not link:
        out.append({"level": "blocking", "text": "No image file or link attached"})
    elif not host_ok(link):
        out.append({"level": "blocking", "text": f"Link host '{urlparse(str(link)).hostname}' is not a real provider "
                                                 "domain: the record looks like a placeholder"})
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", str(rec.get("name") or ""))
    if m and rec.get("date") and f"{m[1]}-{m[2]}-{m[3]}" != str(rec["date"])[:10]:
        out.append({"level": "blocking", "text": f"Image name says {m[1]}-{m[2]}-{m[3]} but the date field says "
                                                 f"{str(rec['date'])[:10]}"})
    if rec.get("date") and len(str(rec["date"])) <= 10:
        out.append({"level": "warning", "text": "Date only, no UTC time: ships move hundreds of km in a day"})
    if str(rec.get("verification") or "").lower() != "verified":
        out.append({"level": "warning", "text": f"Verification status: {rec.get('verification') or 'none'}"})
    return out


def check_model(rec: dict, ship_name: str) -> list:
    out = []
    if not rec.get("link"):
        out.append({"level": "blocking", "text": "No model file or link attached"})
    elif not host_ok(rec["link"]):
        out.append({"level": "blocking", "text": f"Link host '{urlparse(str(rec['link'])).hostname}' is not recognised"})
    else:
        # trust the link over the label typed into the database: the link is what will actually be used
        slug = urlparse(str(rec["link"])).path.rstrip("/").split("/")[-1]
        ship = _words(ship_name) - {"arctic", "polar", "north", "nord", "sea", "star", "energy"}
        key = max(ship, key=len) if ship else None   # the ship's most distinctive word, e.g. VOSTOK, SAKHALIN
        if key and key not in _words(slug):
            verified = str(rec.get("verification") or "").lower() == "verified"
            out.append({"level": "warning" if verified else "blocking",
                        "text": f"The linked model ('{slug}') is not named for {ship_name}: it may be generic or a "
                                "different ship - blocked as a reference until someone marks it verified"})
    if not rec.get("verification"):
        out.append({"level": "warning", "text": "Accuracy not verified yet"})
    return out
