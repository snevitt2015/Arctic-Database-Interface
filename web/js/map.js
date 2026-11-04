/*
  map.js  -  Everything drawn on the map.

  Built on MapLibre GL (free, open source): https://maplibre.org
  Layer order, bottom to top:
    basemap -> overlays (NASA, sea marks, labels) -> our data (sequence, tracks,
    dark gaps, predictions, events) -> HTML markers (ports, latest ship positions)

  The UI never touches MapLibre directly; it calls the functions exported here.
*/
import { BASEMAPS, OVERLAYS, SHIP_CATEGORIES, EVENT_TYPES, START_VIEW, MAX_GAP_HOURS, VECTOR_LAYERS } from "./config.js";

let map;
const handlers = { eventClick: () => {}, shipClick: () => {}, portClick: () => {}, imageryClick: () => {}, vectorClick: () => {} };
const imageryMarkers = [];
const vectorLabels = {};   // layer id -> [{featureId, marker}]
const shipMarkers = new Map();   // vessel name -> maplibregl.Marker
const portMarkers = [];
let overlayDate = null;

const CAT_COLOR = Object.fromEntries(SHIP_CATEGORIES.map((c) => [c.id, c.color]));
const catColorExpr = ["match", ["get", "cat"], ...SHIP_CATEGORIES.flatMap((c) => [c.id, c.color]), "#94a3b8"];
const H = 3600e3;

// ------------------------------------------------------------------ setup
export function createMap(container) {
  const sources = {}, layers = [];
  for (const b of BASEMAPS) {
    sources["bm-" + b.id] = { type: "raster", tiles: b.tiles, tileSize: 256, maxzoom: b.maxzoom, attribution: b.attribution };
    layers.push({ id: "bm-" + b.id, type: "raster", source: "bm-" + b.id, layout: { visibility: "none" } });
  }
  for (const o of OVERLAYS) {
    sources["ov-" + o.id] = { type: "raster", tiles: o.dated ? o.tiles.map((t) => t.replace("{date}", "2026-01-01")) : o.tiles,
      tileSize: o.tileSize || 256, maxzoom: o.maxzoom || 19, minzoom: o.minzoom || 0, attribution: o.attribution };
    layers.push({ id: "ov-" + o.id, type: "raster", source: "ov-" + o.id,
      layout: { visibility: o.on ? "visible" : "none" }, paint: { "raster-opacity": o.opacity } });
  }

  map = new maplibregl.Map({
    container, style: { version: 8, projection: { type: "globe" }, sources, layers },
    ...START_VIEW, maxPitch: 70, attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "nautical" }), "bottom-right");
  map.on("load", () => {
    try { map.setSky({ "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] }); } catch (e) { /* older MapLibre */ }
  });
  // A layer that can't load (e.g. a NASA layer for a date that doesn't exist) should not break the page
  map.on("error", (e) => console.warn("Map layer problem:", e?.error?.message || e));
  return new Promise((resolve) => map.on("load", () => resolve(map)));
}

export const getMap = () => map;
export function on(name, fn) { handlers[name] = fn; }

// ------------------------------------------------------------------ basemaps & overlays
export function setBasemap(id) {
  for (const b of BASEMAPS) map.setLayoutProperty("bm-" + b.id, "visibility", b.id === id ? "visible" : "none");
}
export function setOverlay(id, visible) { map.setLayoutProperty("ov-" + id, "visibility", visible ? "visible" : "none"); }
export function setOverlayOpacity(id, value) { map.setPaintProperty("ov-" + id, "raster-opacity", value); }
export function setOverlayDate(date) {
  overlayDate = date;
  for (const o of OVERLAYS.filter((x) => x.dated)) {
    map.getSource("ov-" + o.id).setTiles(o.tiles.map((t) => t.replace("{date}", date)));
  }
}
export function setProjection(type) { map.setProjection({ type }); }

// ------------------------------------------------------------------ our data
const fc = (features) => ({ type: "FeatureCollection", features });
const pt = (lon, lat, props) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: props });
const line = (coords, props) => ({ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: props });

function circlePolygon(lon, lat, km, props, steps = 48) {
  const coords = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI, dLat = (km / 111.32) * Math.cos(a);
    const dLon = (km / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a);
    coords.push([lon + dLon, lat + dLat]);
  }
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: props };
}

export function setData(store) {
  const byShip = {};
  store.events.forEach((e) => (byShip[e.vessel] ??= []).push(e));

  // points
  const events = fc(store.events.map((e) => pt(e.lon, e.lat, { id: e.id, vessel: e.vessel, cat: e.cat, ev: e.ev, t: e.t, jump: e.jump })));

  // tracks, dark gaps, port-to-port sequence
  const tracks = [], gaps = [], seq = [];
  for (const [vessel, list] of Object.entries(byShip)) {
    const cat = list[0].cat;
    let seg = [list[0]];
    const flush = () => { if (seg.length > 1) tracks.push(line(seg.map((e) => [e.lon, e.lat]), { vessel, cat, t0: seg[0].t, t1: seg.at(-1).t })); };
    for (let i = 1; i < list.length; i++) {
      // break the line on long gaps, and never draw an "impossible move" as if it were a real track
      if (list[i].t - list[i - 1].t > MAX_GAP_HOURS * H || list[i].jump) { flush(); seg = []; }
      seg.push(list[i]);
    }
    flush();
    list.forEach((e, i) => {
      if (e.ev !== "ais_off") return;
      const back = list.slice(i + 1).find((x) => x.ev === "ais_on");
      if (back) gaps.push(line([[e.lon, e.lat], [back.lon, back.lat]], { vessel, cat, t0: e.t, t1: back.t }));
    });
    const visits = list.filter((e) => e.ev === "port_visit");
    for (let i = 1; i < visits.length; i++) {
      seq.push(line([[visits[i - 1].lon, visits[i - 1].lat], [visits[i].lon, visits[i].lat]],
        { vessel, cat, t0: visits[i - 1].t, t1: visits[i].t }));
    }
  }

  // predictions: dashed line from last known position + uncertainty circle at the end
  const predLines = [], predAreas = [];
  const groups = {};
  store.preds.forEach((p) => (groups[p.vessel + "|" + p.method] ??= []).push(p));
  for (const list of Object.values(groups)) {
    const last = byShip[list[0].vessel]?.at(-1);
    if (!last) continue;
    const cat = last.cat, end = list.at(-1);
    predLines.push(line([[last.lon, last.lat], ...list.map((p) => [p.lon, p.lat])], { vessel: end.vessel, cat, method: end.method }));
    predAreas.push(circlePolygon(end.lon, end.lat, Math.max(end.unc, 1), { vessel: end.vessel, cat, method: end.method }));
  }

  const add = (id, data) => (map.getSource(id) ? map.getSource(id).setData(data) : map.addSource(id, { type: "geojson", data }));
  add("events", events); add("tracks", fc(tracks)); add("darkgaps", fc(gaps)); add("sequence", fc(seq));
  add("pred-lines", fc(predLines)); add("pred-areas", fc(predAreas));
  if (!map.getLayer("events")) addLayers();
  addPortMarkers(store.ports);
}

function addLayers() {
  map.addLayer({ id: "sequence", type: "line", source: "sequence",
    paint: { "line-color": catColorExpr, "line-width": 1, "line-opacity": 0.45, "line-dasharray": [1, 3] } });
  map.addLayer({ id: "tracks-casing", type: "line", source: "tracks",
    layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#0b1220", "line-width": 4.5, "line-opacity": 0.6 } });
  map.addLayer({ id: "tracks", type: "line", source: "tracks",
    layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": catColorExpr, "line-width": 2.2 } });
  map.addLayer({ id: "darkgaps", type: "line", source: "darkgaps",
    paint: { "line-color": "#ef4444", "line-width": 2.5, "line-dasharray": [2, 2] } });
  map.addLayer({ id: "pred-areas", type: "fill", source: "pred-areas",
    paint: { "fill-color": catColorExpr, "fill-opacity": 0.12 } });
  map.addLayer({ id: "pred-lines", type: "line", source: "pred-lines",
    paint: { "line-color": catColorExpr, "line-width": 2, "line-dasharray": [2, 1.5] } });
  map.addLayer({ id: "events", type: "circle", source: "events", paint: {
    "circle-color": catColorExpr,
    "circle-opacity": ["case", ["==", ["get", "ev"], "loitering"], 0, 0.9],
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 2,
      ["*", 0.7, ["match", ["get", "ev"], ...Object.entries(EVENT_TYPES).flatMap(([k, v]) => [k, v.radius]), 4]], 8,
      ["*", 1.3, ["match", ["get", "ev"], ...Object.entries(EVENT_TYPES).flatMap(([k, v]) => [k, v.radius]), 4]]],
    "circle-stroke-color": ["case", ["get", "jump"], "#ef4444",
      ["match", ["get", "ev"], ...Object.entries(EVENT_TYPES).flatMap(([k, v]) => [k, v.ring || "#0b1220"]), "#0b1220"]],
    "circle-stroke-width": ["case", ["get", "jump"], 2.5, ["match", ["get", "ev"], "loitering", 1.6, "ais_off", 2.5, "ais_on", 2, "encounter", 2, 0.8]],
  } });

  map.on("click", "events", (e) => {
    const f = e.features[0];
    handlers.eventClick(f.properties.id, e.lngLat);
  });
  for (const id of ["events", "tracks", "darkgaps"]) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }
}

// ------------------------------------------------------------------ filtering (fast: no data reload)
export function applyFilter({ start, end, vessels, eventTypes, dataLayers, selected }) {
  const shipIn = ["in", ["get", "vessel"], ["literal", [...vessels]]];
  const inWindow = ["all", [">=", ["get", "t1"], start], ["<=", ["get", "t0"], end]];
  map.setFilter("events", ["all", shipIn, [">=", ["get", "t"], start], ["<=", ["get", "t"], end],
    ["in", ["get", "ev"], ["literal", [...eventTypes]]]]);
  for (const id of ["tracks", "tracks-casing", "darkgaps", "sequence"]) map.setFilter(id, ["all", shipIn, inWindow]);
  for (const id of ["pred-lines", "pred-areas"]) map.setFilter(id, shipIn);

  const vis = (id, on) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  vis("tracks", dataLayers.has("tracks")); vis("tracks-casing", dataLayers.has("tracks"));
  vis("darkgaps", dataLayers.has("darkgaps")); vis("sequence", dataLayers.has("sequence"));
  vis("pred-lines", dataLayers.has("predictions")); vis("pred-areas", dataLayers.has("predictions"));
  portMarkers.forEach((m) => (m.getElement().style.display = dataLayers.has("ports") ? "" : "none"));
  imageryMarkers.forEach((m) => (m.getElement().style.display = dataLayers.has("imagery") ? "" : "none"));

  // fade everything except the selected ship
  const dim = (on, off) => (selected ? ["case", ["==", ["get", "vessel"], selected], on, off] : on);
  map.setPaintProperty("events", "circle-stroke-opacity", dim(1, 0.15));
  map.setPaintProperty("events", "circle-opacity", selected
    ? ["case", ["==", ["get", "vessel"], selected], ["case", ["==", ["get", "ev"], "loitering"], 0, 0.95], 0.12]
    : ["case", ["==", ["get", "ev"], "loitering"], 0, 0.9]);
  map.setPaintProperty("tracks", "line-opacity", dim(1, 0.15));
  map.setPaintProperty("tracks-casing", "line-opacity", dim(0.6, 0));
  map.setPaintProperty("sequence", "line-opacity", dim(0.8, 0.1));
}

// ------------------------------------------------------------------ HTML markers
function shipSvg(color, course) {
  if (course == null) {   // heading unknown -> round marker
    return `<svg viewBox="0 0 24 24" width="22" height="22"><circle cx="12" cy="12" r="7" fill="${color}" stroke="#0b1220" stroke-width="2"/>
      <circle cx="12" cy="12" r="10.5" fill="none" stroke="${color}" stroke-opacity=".45" stroke-width="1.5"/></svg>`;
  }
  return `<svg viewBox="0 0 24 24" width="26" height="26" style="transform:rotate(${course}deg)">
    <path d="M12 1.5 L18 9 L17 21.5 Q12 23.5 7 21.5 L6 9 Z" fill="${color}" stroke="#0b1220" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="M12 6 L12 15" stroke="#0b1220" stroke-opacity=".5" stroke-width="1.2"/></svg>`;
}

export function setLatestShips(latest, selected) {
  const keep = new Set(latest.map((e) => e.vessel));
  for (const [name, m] of shipMarkers) if (!keep.has(name)) { m.remove(); shipMarkers.delete(name); }
  for (const e of latest) {
    let m = shipMarkers.get(e.vessel);
    if (!m) {
      const el = document.createElement("div");
      el.className = "ship-marker";
      el.addEventListener("click", (ev) => { ev.stopPropagation(); handlers.shipClick(e.vessel); });
      m = new maplibregl.Marker({ element: el, anchor: "left", offset: [-13, 0] }).setLngLat([e.lon, e.lat]).addTo(map);
      shipMarkers.set(e.vessel, m);
    }
    m.setLngLat([e.lon, e.lat]);
    const el = m.getElement();
    el.classList.toggle("selected", selected === e.vessel);
    el.classList.toggle("dimmed", !!selected && selected !== e.vessel);
    el.innerHTML = `<span class="icon">${shipSvg(CAT_COLOR[e.cat], e.course)}</span><span class="label">${e.vessel}</span>`;
    el.title = `${e.vessel} - last seen in this time window`;
  }
}

function addPortMarkers(ports) {
  portMarkers.forEach((m) => m.remove());
  portMarkers.length = 0;
  for (const p of ports) {
    const el = document.createElement("div");
    el.className = "port-marker" + (p.fromExcel ? " watch" : "");
    el.innerHTML = `<span class="pin"><svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor"
      d="M12 2a2.5 2.5 0 0 0-1 4.8V9H8v2h3v8.9A7 7 0 0 1 5.1 14H7l-3-4-3 4h2.1A9 9 0 0 0 12 22a9 9 0 0 0 8.9-8H23l-3-4-3 4h1.9A7 7 0 0 1 13 19.9V11h3V9h-3V6.8A2.5 2.5 0 0 0 12 2z"/></svg></span>
      <span class="label">${p.name}</span>`;
    el.addEventListener("click", (ev) => { ev.stopPropagation(); handlers.portClick(p); });
    portMarkers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [-9, 0] }).setLngLat([p.lon, p.lat]).addTo(map));
  }
}

// ------------------------------------------------------------------ vector layers (sea routes etc.)
export function setVectors(vectors) {
  for (const cfg of VECTOR_LAYERS) {
    const data = vectors[cfg.id];
    if (!data) continue;
    map.addSource("vec-" + cfg.id, { type: "geojson", data });
    const dashed = ["in", ["get", "id"], ["literal", cfg.dashed || []]];
    const width = ["case", ["in", ["get", "id"], ["literal", cfg.thin || []]], 1.3, 2.6];
    const before = map.getLayer("sequence") ? "sequence" : undefined;
    map.addLayer({ id: `vec-${cfg.id}-casing`, type: "line", source: "vec-" + cfg.id, filter: ["!", dashed],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#0b1220", "line-opacity": 0.55, "line-width": ["+", width, 2.5] } }, before);
    map.addLayer({ id: `vec-${cfg.id}-solid`, type: "line", source: "vec-" + cfg.id, filter: ["!", dashed],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": ["get", "color"], "line-width": width, "line-opacity": 0.9 } }, before);
    map.addLayer({ id: `vec-${cfg.id}-dashed`, type: "line", source: "vec-" + cfg.id, filter: dashed,
      paint: { "line-color": ["get", "color"], "line-width": width, "line-opacity": 0.85, "line-dasharray": [3, 2.5] } }, before);
    for (const part of ["solid", "dashed"]) {
      const lid = `vec-${cfg.id}-${part}`;
      map.on("click", lid, (e) => handlers.vectorClick(e.features[0].properties, e.lngLat));
      map.on("mouseenter", lid, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", lid, () => (map.getCanvas().style.cursor = ""));
    }
    // a name label part-way along each line
    vectorLabels[cfg.id] = data.features.map((f) => {
      const coords = f.geometry.type === "MultiLineString" ? f.geometry.coordinates[0] : f.geometry.coordinates;
      const at = coords[Math.floor(coords.length * (f.properties.labelAt ?? 0.45))];
      const el = document.createElement("div");
      el.className = "route-label";
      el.style.color = f.properties.color;
      el.textContent = f.properties.name;
      return { featureId: f.properties.id, marker: new maplibregl.Marker({ element: el }).setLngLat(at).addTo(map) };
    });
  }
}
export function setVectorVisible(layerId, onIds) {
  const filterOn = ["in", ["get", "id"], ["literal", [...onIds]]];
  const cfg = VECTOR_LAYERS.find((v) => v.id === layerId);
  const dashed = ["in", ["get", "id"], ["literal", cfg.dashed || []]];
  if (!map.getLayer(`vec-${layerId}-solid`)) return;
  map.setFilter(`vec-${layerId}-casing`, ["all", ["!", dashed], filterOn]);
  map.setFilter(`vec-${layerId}-solid`, ["all", ["!", dashed], filterOn]);
  map.setFilter(`vec-${layerId}-dashed`, ["all", dashed, filterOn]);
  (vectorLabels[layerId] || []).forEach((l) => (l.marker.getElement().style.display = onIds.has(l.featureId) ? "" : "none"));
}

// ------------------------------------------------------------------ imagery markers (Capstone DB)
const ICON_SAR = `<svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
  d="M5 19a10 10 0 0 1 0-14M8.5 15.5a5 5 0 0 1 0-7"/><circle cx="13" cy="12" r="2.2" fill="currentColor"/><path stroke="currentColor" stroke-width="2" d="M13 12l7 7"/></svg>`;
const ICON_OPT = `<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M9 4l-1.5 2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-3.5L15 4H9zm3 4.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 2a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"/></svg>`;
export function setImagery(images) {
  imageryMarkers.forEach((m) => m.remove());
  imageryMarkers.length = 0;
  for (const img of images.filter((i) => i.lat != null && i.lon != null)) {
    const el = document.createElement("div");
    el.className = "img-marker " + (img.kind === "SAR" ? "sar" : "optical");
    el.innerHTML = img.kind === "SAR" ? ICON_SAR : ICON_OPT;
    el.title = `${img.kind} image · ${img.date || ""}`;
    el.addEventListener("click", (ev) => { ev.stopPropagation(); handlers.imageryClick(img); });
    imageryMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([img.lon, img.lat]).addTo(map));
  }
}

// ------------------------------------------------------------------ camera & popups
export function flyTo(lon, lat, zoom = 7, opts = {}) { map.flyTo({ center: [lon, lat], zoom, speed: 1.4, essential: true, ...opts }); }
export function fitPoints(points, maxZoom = 8) {
  if (!points.length) return;
  const b = new maplibregl.LngLatBounds();
  points.forEach(([lon, lat]) => b.extend([lon, lat]));
  map.fitBounds(b, { padding: { top: 90, bottom: 170, left: 380, right: 380 }, maxZoom, duration: 1200 });
}
let popup;
export function showPopup(lngLat, html) {
  popup?.remove();
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px", className: "dark-popup" }).setLngLat(lngLat).setHTML(html).addTo(map);
}
export function resetView() { map.flyTo({ ...START_VIEW, duration: 1500 }); }
