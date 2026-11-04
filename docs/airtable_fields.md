# Airtable fields: what to add so tracking and imagery line up

This is the checklist for the team's Capstone base. The tracker reads these exact field names
(`tracker/capstone_loader.py`, `tracker/airtable.py`).

**Most important:** the algorithm can only connect an image to a ship if both have an **exact UTC
time** and a **position in decimal degrees**. Everything below follows from that.

## Rules for every table

| Rule | Why |
|---|---|
| **Times in UTC, to the second** (`2026-10-22 06:14:37`) | A tanker at 12 kn moves about 22 km an hour. A date alone can't be matched to a ship. |
| **Coordinates as signed decimal degrees**: north and east are positive, south and west negative (`69.0412`, `33.0754`) | "30.19W" for St Petersburg put an image in the Atlantic. The data checks flag this now, but it's better not to type it in the first place. |
| **Link ships by IMO**, never by name or MMSI | Names and MMSIs change (Iris was North Sky; Nova Energy has had 5 MMSIs). |
| **Never rename a field or table** without changing `AIRTABLE_TABLES` in `config.py` | A renamed field silently becomes "missing". |
| **Record the measurement error** next to every measured number (± m, ± °, ± km) | The confidence score needs to know how much to trust each number. |
| **Never overwrite**: add a new record for each new observation | History is how dark periods and identity changes are found. |
| **Licensed images stay private** (TerraSAR-X, RADARSAT, Maxar, Capella, ICEYE): store them in Drive and put only the link and details in Airtable | The GitHub repo and website are public. Sentinel-1/2 imagery is free to share. |

---

## 1. Ships (existing table): add ship dimensions

| Field | Type | Example | Notes |
|---|---|---|---|
| Length overall (m) | Number | 299.0 | Needed to compare against length measured in an image |
| Beam (m) | Number | 50.0 | Width |
| Depth (m) | Number | 26.5 | Keel to main deck |
| Air draft (m) | Number | 48 | Waterline to highest point. Needed for shadow length → height |
| Superstructure position | Single select: aft / midships / forward | aft | Shapes the SAR bright-spot pattern and the optical shadow |
| Dimension source | URL / text | Equasis, class society, builder | So the numbers can be checked |

## 2. AIS Updates (existing table): fill these in

| Field | Status now | Needed |
|---|---|---|
| Latitude, Longitude | **empty on all 41 checks** | Decimal degrees. Without them a check can't be plotted or used to estimate where a ship was when an image was taken. |
| Last position time (UTC) | ✅ | Keep it to the minute or second |
| Heading (deg) | not recorded | The bow direction (Course is direction of travel). SAR measures the hull's heading, so this is what gets compared. |

## 3. SAR Scenes (new table, or add these to the existing SAR Imagery table)

One record per satellite image.

| Field | Type | Example |
|---|---|---|
| Scene ID | Text (primary) | Full product name, e.g. `S1A_EW_GRDM_1SDH_20261022T061437_…` |
| Satellite | Single select | Sentinel-1A, TerraSAR-X, RADARSAT-2, ICEYE … |
| Mode / product | Text | EW GRDM, IW GRDH, StripMap … |
| Acquisition start (UTC) | Date + time, seconds | 2026-10-22 06:14:37 |
| Acquisition end (UTC) | Date + time | |
| Orbit direction | Single select: ascending / descending | Decides which way moving ships appear shifted |
| Satellite heading (deg) | Number | Flight direction; moving ships are displaced along it |
| Look side | Single select: right / left | |
| Incidence angle near / far (deg) | Number ×2 | Sentinel-1 IW ≈ 29–46°, EW ≈ 19–47° |
| Pixel spacing range / azimuth (m) | Number ×2 | Sets the smallest length difference you can measure |
| Polarization | Single select | VV, VH, HH, HV, dual |
| Footprint | Long text (WKT polygon) | `POLYGON((lon lat, …))` |
| Sea-ice concentration in scene | Number % | From the NASA layer. Ice causes false detections |
| Storage link | URL | Drive folder (private) |
| Licence | Single select | Copernicus open / licensed - do not publish |

## 4. SAR Detections (new table)

One record per ship-like object found in a scene. **Keep every detection, matched or not**: the unmatched ones are the dark ships.

| Field | Type | Notes |
|---|---|---|
| Detection ID | Autonumber / text | |
| Scene | Link → SAR Scenes | Gives the exact time |
| Latitude, Longitude | Number | Centre of the bright return |
| Length (m), ± error | Number ×2 | |
| Width (m), ± error | Number ×2 | |
| Heading (deg), ± error | Number ×2 | Hull orientation. 180° ambiguous unless a wake shows |
| Wake visible | Checkbox | The wake points to the ship's *true* position if it is moving |
| Estimated azimuth offset (m) | Number | Moving ships appear shifted along the satellite track |
| Near ice edge / in ice | Checkbox | |
| Detection method | Single select | GFW SAR dataset / manual / our model vX |
| Detection confidence | Number 0–1 | |
| AIS matched | Checkbox | Filled by the matching step |
| Chip | Attachment | Small crop around the ship (private if the scene is licensed) |

## 5. Matches (new table): detection → ship candidates

Written by the algorithm (`tracker/interpolate.py` `candidates_near()` gives the candidates). An analyst confirms or rejects.

| Field | Type | Notes |
|---|---|---|
| Detection | Link → SAR Detections (or Optical) | |
| Candidate ship | Link → Ships | |
| Distance from estimated position (km) | Number | |
| Position estimate method | Single select | exact / interpolated / projected / last_known |
| Position uncertainty (km) | Number | |
| Length / width / heading difference | Number ×3 | Measured − registry |
| Match score | Number 0–1 | The confidence score (later) |
| Method version | Text | So old scores can be recomputed |
| Analyst verdict | Single select | confirmed / rejected / unsure |
| Verified by, date | Collaborator, date | **Confirmed matches become the ground-truth set** for testing the score. Keep some aside that are never used for tuning. |

## 6. Satellite Optical Imagery (existing): add or fix

| Field | Notes |
|---|---|
| Capture time (UTC, seconds) | Currently a date only |
| Latitude, Longitude | Decimal, signed. The St Petersburg record says 30.19**W**; it should be **E** |
| Sun elevation (deg), Sun azimuth (deg) | From the image metadata, or computed: `tracker/sun.py` `sun_position(time, lat, lon)` |
| Off-nadir angle (deg), Satellite azimuth (deg) | Viewing geometry, the satellite equivalent of the lab camera angle |
| Footprint | WKT polygon |

## 7. Capture Sessions: 3D-printed ships (existing, currently empty)

One record per lighting/camera setup. Your plan: **sun 20–50° and camera 20–45°, in 5° steps = 7 × 6 = 42 setups per model**.

| Field | Type | Notes |
|---|---|---|
| Session ID | Text (primary) | e.g. `ARCTIC-VOSTOK_S35_C30` |
| 3D model | Link → 3D Models | |
| Print scale | Text | e.g. 1:700. Without it shadow lengths can't be converted to metres |
| Model length (mm) | Number | Measured on the print |
| Sun elevation (deg) | Number: 20, 25 … 50 | Lamp height angle above the "water" surface |
| Sun azimuth relative to bow (deg) | Number | Shadow direction changes the shape a lot. Fix it or vary it on purpose |
| Camera angle (deg) | Number: 20, 25 … 45 | **Write down the convention**: measured from vertical (= satellite *off-nadir*) or from horizontal |
| Camera azimuth relative to bow (deg) | Number | |
| Camera distance, focal length, aperture, shutter, ISO, white balance | Number / text | Existing columns |
| Light source | Text | Hard light gives sharp shadows like sunlight. Diffuse light behaves like overcast sky |
| Surface / condition | Single select | open water / ice / snow / overcast / fog |
| Scale bar in frame | Checkbox | Lets length be measured from the photo itself |

**Optical Images (lab)**: one record per photo, linked to its session, plus measured *hull length (px)*,
*shadow length (px)*, and *scale (mm per px)*.

### How well the angle plan fits the Arctic

* **The sun never reaches 50° inside the Arctic.** The highest it gets is 90° − latitude + 23.4°: about 48° at 65°N, 43° at 70°N, 38° at 75°N and 33° at 80°N (computed by `tracker/sun.py`). The 45–50° setups only match images of southern ports such as St Petersburg or Arkhangelsk. Most Arctic imagery will fall at **20–40°**.
* **Low sun is common in the Arctic.** In spring and autumn the sun is often **below 20°** and shadows are very long. Consider adding 10° and 15° setups.
* **No optical imagery in polar night.** From about November to February the sun is below the horizon north of about 67°N, so SAR is the only source. Cross-cueing should expect optical to drop out in winter.
* **Check the camera-angle convention against satellites.** Optical satellites usually image at 0–30° (sometimes up to 45°) *off-nadir*, measured from vertical. If your camera angle is measured from vertical, 20–45° fits well. If it is measured from horizontal, 20–45° equals 45–70° off-nadir, which is steeper than most satellite images.
* **For the SAR side of cross-cueing, the matching parameter is the *incidence angle*.** Sentinel-1 is about 29–46° in IW mode, and over Arctic sea ice it often uses EW mode at about 19–47° with coarser pixels. Record it on every SAR scene so radar and optical views of the same ship can be paired.
