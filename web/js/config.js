/*
  config.js  -  EVERYTHING you might want to change or add lives here.

  The rest of the code reads these lists, so adding a basemap, an overlay,
  a ship category or an event type is just adding one entry below.
*/

// Where run_update.py writes its CSV files (relative to index.html)
export const DATA_DIR = "data/";

// Map start view: looking down on the Russian Arctic
export const START_VIEW = { center: [60, 72], zoom: 2.4, pitch: 0, bearing: 0 };

// Join two events with a track line only if they are this close in time (hours)
export const MAX_GAP_HOURS = 12;

// Two events closer than this to a port count as a visit to it (km)
export const PORT_RADIUS_KM = 25;

// ---------------------------------------------------------------------------
// BASEMAPS - only one is shown at a time. `tiles` follows the {z}/{x}/{y} pattern.
// ---------------------------------------------------------------------------
export const BASEMAPS = [
  {
    id: "satellite", label: "Satellite", thumb: "#2c4a3a",
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    maxzoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  },
  {
    id: "ocean", label: "Ocean depth", thumb: "#3d6e8f",
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"],
    maxzoom: 13, attribution: "Bathymetry © Esri, GEBCO, NOAA, Garmin",
  },
  {
    id: "dark", label: "Dark", thumb: "#1b2230",
    tiles: ["https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
            "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png"],
    maxzoom: 19, attribution: "© OpenStreetMap contributors © CARTO",
  },
  {
    id: "light", label: "Light", thumb: "#e6eaee",
    tiles: ["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
            "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png"],
    maxzoom: 19, attribution: "© OpenStreetMap contributors © CARTO",
  },
];
export const DEFAULT_BASEMAP = "satellite";

// ---------------------------------------------------------------------------
// OVERLAYS - transparent layers drawn on top of the basemap; any number can be on.
// `dated: true` means the layer has a date (NASA daily imagery); {date} is filled in.
// NASA GIBS: https://nasa-gibs.github.io/gibs-api-docs/  (free, no key)
// ---------------------------------------------------------------------------
const GIBS_WMS = (layer) =>
  "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0" +
  `&LAYERS=${layer}&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&CRS=EPSG:3857&WIDTH=512&HEIGHT=512` +
  "&BBOX={bbox-epsg-3857}&TIME={date}";

export const OVERLAYS = [
  {
    id: "labels", label: "Place names & borders", group: "Reference", on: true, opacity: 0.9,
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Labels © Esri",
  },
  {
    id: "seamarks", label: "Sea marks (buoys, lights, harbours)", group: "Reference", on: false, opacity: 1, minzoom: 8,
    tiles: ["https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png"],
    attribution: "Sea marks © OpenSeaMap", note: "Appears when zoomed in near a coast",
  },
  {
    id: "truecolor", label: "Daily satellite image (MODIS)", group: "NASA daily", on: false, opacity: 0.85, dated: true,
    tiles: [GIBS_WMS("MODIS_Terra_CorrectedReflectance_TrueColor")], tileSize: 512, maxzoom: 9,
    attribution: "NASA GIBS / MODIS Terra", note: "Real colour image of the chosen day - shows ice and cloud",
  },
  {
    id: "seaice", label: "Sea-ice concentration (AMSR2)", group: "NASA daily", on: false, opacity: 0.7, dated: true,
    tiles: [GIBS_WMS("AMSRU2_Sea_Ice_Concentration_12km")], tileSize: 512, maxzoom: 6,
    attribution: "NASA GIBS / AMSR2", note: "White/pink = solid ice, colours = ice edge, clear = open water",
  },
];

// ---------------------------------------------------------------------------
// SHIP CATEGORIES - matched against the "Type" column of the Excel sheet (first match wins).
// ---------------------------------------------------------------------------
export const SHIP_CATEGORIES = [
  { id: "lng",        label: "LNG / gas carrier",    color: "#38bdf8", match: /lng|gas/i },
  { id: "icebreaker", label: "Icebreaker",           color: "#f1f5f9", match: /ice ?breaker/i },
  { id: "chemical",   label: "Chemical tanker",      color: "#f472b6", match: /chemical/i },
  { id: "bunker",     label: "Bunkering tanker",     color: "#a78bfa", match: /bunker/i },
  { id: "oil",        label: "Oil / product tanker", color: "#fbbf24", match: /oil|tanker|product/i },
  { id: "container",  label: "Container ship",       color: "#34d399", match: /container/i },
  { id: "cargo",      label: "Bulk / general cargo", color: "#a3e635", match: /bulk|cargo|general/i },
  { id: "other",      label: "Other",                color: "#94a3b8", match: /.*/ },
];

// ---------------------------------------------------------------------------
// EVENT TYPES - how each kind of record is drawn (radius in pixels, ring colour).
// ---------------------------------------------------------------------------
export const EVENT_TYPES = {
  position:   { label: "AIS position",           radius: 3.5, ring: null,      on: true },
  port_visit: { label: "Port visit",             radius: 4.5, ring: null,      on: true },
  loitering:  { label: "Loitering at sea",       radius: 5,   ring: "#ffffff", on: true, hollow: true },
  encounter:  { label: "Encounter with a ship",  radius: 7,   ring: "#ffffff", on: true },
  ais_off:    { label: "AIS switched off",       radius: 8,   ring: "#ef4444", on: true },
  ais_on:     { label: "AIS back on",            radius: 6,   ring: "#22c55e", on: true },
};

// Extra data layers (drawn from our own data, not tiles)
export const DATA_LAYERS = [
  { id: "tracks",      label: "Tracks (events < 12 h apart)", on: true },
  { id: "darkgaps",    label: "Dark gaps (AIS off → on)",     on: true },
  { id: "sequence",    label: "Port-to-port sequence (straight lines, not real route)", on: false },
  { id: "predictions", label: "Predicted positions",          on: true },
  { id: "ports",       label: "Ports",                        on: true },
];
