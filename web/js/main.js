/*
  main.js  -  Starts the app and connects everything.

  `state` = what the user has chosen (time window, ships, layers, selection).
  Whenever it changes we call update(), which redraws the map filters and panels.
*/
import { BASEMAPS, OVERLAYS, DATA_LAYERS, EVENT_TYPES, DEFAULT_BASEMAP, VECTOR_LAYERS } from "./config.js";
import { loadAll } from "./data.js";
import * as M from "./map.js";
import * as UI from "./ui.js";
import { vesselCard, portCard, eventPopup, fleetCard, imageryCard, routePopup } from "./cards.js";

const $ = (s) => document.querySelector(s);
const DAY = 864e5;
const today = new Date(Date.now() - DAY).toISOString().slice(0, 10);   // NASA images are ready ~1 day later

const state = {
  windowDays: 90, start: 0, end: 0, vessels: new Set(), selected: null, search: "",
  eventTypes: new Set(Object.entries(EVENT_TYPES).filter(([, v]) => v.on).map(([k]) => k)),
  dataLayers: new Set(DATA_LAYERS.filter((d) => d.on).map((d) => d.id)),
  basemap: DEFAULT_BASEMAP, projection: "globe", leftTab: "vessels", alertTab: "dark", alertsOpen: false,
  overlays: Object.fromEntries(OVERLAYS.map((o) => [o.id, { on: o.on, opacity: o.opacity }])),
  overlayDate: today, today,
  fleetType: "", showAllTypes: false,
  vectorsOn: Object.fromEntries(VECTOR_LAYERS.map((v) => [v.id, new Set(v.on || [])])), vectorData: {},
};
let store, alertRows = [];

// ---------------------------------------------------------------- redraw
function visibleEvents() {
  return store.events.filter((e) => state.vessels.has(e.vessel) && e.t >= state.start && e.t <= state.end && state.eventTypes.has(e.ev));
}
function update() {
  const visible = visibleEvents();
  const latest = {};
  for (const e of store.events) if (state.vessels.has(e.vessel) && e.t >= state.start && e.t <= state.end) latest[e.vessel] = e;
  M.applyFilter({ ...state });
  M.setLatestShips(Object.values(latest), state.selected);
  UI.renderStats(store, state, visible);
  if (state.leftTab === "vessels") UI.renderVessels(store, state, latest);
  UI.renderTimeline(store, state);
  alertRows = UI.renderAlerts(store, state, visible);
}
function setWindow(days, end = state.end) {
  state.windowDays = days;
  state.end = Math.min(store.maxT, Math.max(store.minT, end));
  state.start = days ? state.end - days * DAY : store.minT;
  document.querySelectorAll("[data-window]").forEach((b) => b.classList.toggle("on", +b.dataset.window === days));
  update();
}

// ---------------------------------------------------------------- selection & cards
function openDetail(html) { $("#detail").innerHTML = html; $("#detail").classList.add("open"); }
function closeDetail() { $("#detail").classList.remove("open"); state.selected = null; update(); }

function selectShip(name, zoom = true) {
  state.selected = name;
  openDetail(vesselCard(store, name));
  update();
  if (zoom) zoomShip(name);
}
function zoomShip(name) {
  let pts = store.events.filter((e) => e.vessel === name && e.t >= state.start && e.t <= state.end);
  if (!pts.length) pts = store.events.filter((e) => e.vessel === name).slice(-30);
  M.fitPoints(pts.map((e) => [e.lon, e.lat]));
}
function selectPort(p) {
  state.selected = null;
  openDetail(portCard(store, p));
  update();
  M.flyTo(p.lon, p.lat, 9);
  $("#detail").dataset.port = p.name;
}

// ---------------------------------------------------------------- events from the map
M.on("eventClick", (id, lngLat) => M.showPopup(lngLat, eventPopup(store.events[id])));
M.on("shipClick", (name) => selectShip(name, false));
M.on("portClick", (p) => selectPort(p));
M.on("imageryClick", (img) => { state.selected = null; openDetail(imageryCard(img, store)); update(); M.flyTo(img.lon, img.lat, 6); });
M.on("vectorClick", (props, lngLat) => M.showPopup(lngLat, routePopup(props)));

function openFleetShip(imo) {
  const onList = store.vessels.find((v) => v.imo === imo);
  if (onList) return selectShip(onList.name);
  state.selected = null;
  openDetail(fleetCard(store.fleetByImo[imo]));
  update();
}

// ---------------------------------------------------------------- clicks anywhere (event delegation)
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-ship],[data-toggle],[data-cat],[data-port],[data-close],[data-zoom-ship],[data-only-ship]," +
    "[data-zoom-port],[data-fly],[data-window],[data-left-tab],[data-alert-tab],[data-alert-row],[data-basemap],[data-proj],#alerts-toggle,#play,#home," +
    "[data-fleet],[data-fleet-type],[data-more-types]");
  if (!t) return;
  const d = t.dataset;
  if (d.fleet) return openFleetShip(d.fleet);
  if (d.fleetType !== undefined) { ev.preventDefault(); state.fleetType = state.fleetType === d.fleetType ? "" : d.fleetType; return UI.renderFleet(store, state); }
  if (d.moreTypes !== undefined) { ev.preventDefault(); state.showAllTypes = !state.showAllTypes; return UI.renderFleet(store, state); }
  if (d.toggle !== undefined) { t.checked ? state.vessels.add(d.toggle) : state.vessels.delete(d.toggle); ev.stopPropagation(); return update(); }
  if (d.cat) { store.vessels.filter((v) => v.cat === d.cat).forEach((v) => (t.checked ? state.vessels.add(v.name) : state.vessels.delete(v.name))); return update(); }
  if (d.ship !== undefined && !t.matches("input")) return selectShip(d.ship);
  if (d.port !== undefined) return selectPort(store.ports.find((p) => p.name === d.port));
  if (d.close !== undefined) return closeDetail();
  if (d.zoomShip) return zoomShip(d.zoomShip);
  if (d.onlyShip) { state.vessels = new Set([d.onlyShip]); return update(); }
  if (d.zoomPort !== undefined) {
    const p = store.ports.find((x) => x.name === $("#detail").dataset.port);
    if (state.basemap !== "satellite") { state.basemap = "satellite"; M.setBasemap("satellite"); }
    return M.flyTo(p.lon, p.lat, 13.5, { pitch: 45 });
  }
  if (d.fly) { ev.preventDefault(); const [lon, lat] = d.fly.split(",").map(Number); return M.flyTo(lon, lat, 7); }
  if (d.window !== undefined) return setWindow(+d.window);
  if (d.leftTab) return showLeftTab(d.leftTab);
  if (d.alertTab) { state.alertTab = d.alertTab; return update(); }
  if (d.alertRow !== undefined) {
    const r = alertRows[+d.alertRow];
    if (r.lat != null) { M.flyTo(r.lon, r.lat, 7); if (r.id != null) M.showPopup([r.lon, r.lat], eventPopup(r)); }
    else selectShip(r.vessel);
    return;
  }
  if (d.basemap) { state.basemap = d.basemap; M.setBasemap(d.basemap); return UI.renderLayers(state); }
  if (d.proj) { state.projection = d.proj; M.setProjection(d.proj); return UI.renderLayers(state); }
  if (t.id === "alerts-toggle") { state.alertsOpen = !state.alertsOpen; document.body.classList.toggle("alerts-open", state.alertsOpen); return; }
  if (t.id === "play") return togglePlay();
  if (t.id === "home") return M.resetView();
});

// checkboxes / sliders / dates inside the Layers tab
document.addEventListener("input", (ev) => {
  const t = ev.target, d = t.dataset;
  if (d.overlay) { state.overlays[d.overlay].on = t.checked; M.setOverlay(d.overlay, t.checked); }
  if (d.opacity) { state.overlays[d.opacity].opacity = +t.value; M.setOverlayOpacity(d.opacity, +t.value); }
  if (d.datalayer) { t.checked ? state.dataLayers.add(d.datalayer) : state.dataLayers.delete(d.datalayer); update(); }
  if (d.evtype) { t.checked ? state.eventTypes.add(d.evtype) : state.eventTypes.delete(d.evtype); update(); }
  if (d.vector) {
    const set = state.vectorsOn[d.vector];
    t.checked ? set.add(d.feature) : set.delete(d.feature);
    M.setVectorVisible(d.vector, set);
  }
  if (t.id === "overlay-date" && t.value) { state.overlayDate = t.value; M.setOverlayDate(t.value); }
  if (t.id === "search") {
    state.search = t.value.trim();
    if (state.leftTab === "ports") UI.renderPorts(store, state);
    else if (state.leftTab === "fleet") UI.renderFleet(store, state);
    else update();
  }
});
$("#search").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter") return;
  const q = state.search.toLowerCase();
  const ship = store.vessels.find((v) => v.name.toLowerCase().includes(q) || v.imo === q || v.mmsi === q ||
    (store.identities[v.name]?.names || []).some((n) => n.toLowerCase().includes(q)));
  const port = store.ports.find((p) => p.name.toLowerCase().includes(q));
  const reg = store.fleet.find((f) => f.imo === q || f.name.toLowerCase() === q);
  if (state.leftTab === "fleet" && reg) openFleetShip(reg.imo);
  else if (state.leftTab === "ports" && port) selectPort(port);
  else if (ship) selectShip(ship.name);
  else if (port) selectPort(port);
  else if (reg) openFleetShip(reg.imo);
});

function showLeftTab(tab) {
  state.leftTab = tab;
  document.querySelectorAll("[data-left-tab]").forEach((b) => b.classList.toggle("on", b.dataset.leftTab === tab));
  document.querySelectorAll(".tab-body").forEach((el) => (el.hidden = el.id !== "tab-" + tab));
  $("#search").placeholder = tab === "ports" ? "Search ports…" : tab === "fleet" ? "Search 4,000+ registry ships by name, IMO or type…"
    : "Search ship, IMO, MMSI or former name…";
  if (tab === "ports") UI.renderPorts(store, state);
  if (tab === "fleet") UI.renderFleet(store, state);
  if (tab === "layers") UI.renderLayers(state);
  if (tab === "vessels") update();
}

// ---------------------------------------------------------------- timeline: click/drag + play
const hist = $("#histogram");
let dragging = false;
const endFromX = (x) => store.minT + ((x - hist.getBoundingClientRect().left) / hist.clientWidth) * (store.maxT - store.minT);
hist.addEventListener("pointerdown", (e) => { dragging = true; hist.setPointerCapture(e.pointerId); setWindow(state.windowDays, endFromX(e.clientX)); });
hist.addEventListener("pointermove", (e) => dragging && setWindow(state.windowDays, endFromX(e.clientX)));
hist.addEventListener("pointerup", () => (dragging = false));

let timer = null;
function togglePlay() {
  if (timer) { clearInterval(timer); timer = null; $("#play").textContent = "▶"; return; }
  if (!state.windowDays) setWindow(90);
  if (state.end >= store.maxT) setWindow(state.windowDays, store.minT + state.windowDays * DAY);
  $("#play").textContent = "❚❚";
  const step = Math.max(DAY, (store.maxT - store.minT) / 300);
  timer = setInterval(() => {
    setWindow(state.windowDays, state.end + step);
    if (state.end >= store.maxT) togglePlay();
  }, 90);
}
window.addEventListener("resize", () => store && UI.renderTimeline(store, state));

// ---------------------------------------------------------------- start
(async function init() {
  try {
    const [, data] = await Promise.all([M.createMap("map"), loadAll()]);
    store = data;
    store.vessels.forEach((v) => state.vessels.add(v.name));
    M.setBasemap(state.basemap);
    M.setOverlayDate(state.overlayDate);
    M.setData(store);
    state.vectorData = store.vectors;
    M.setVectors(store.vectors);
    VECTOR_LAYERS.forEach((v) => M.setVectorVisible(v.id, state.vectorsOn[v.id]));
    M.setImagery(store.imagery);
    UI.renderLegend();
    showLeftTab("vessels");
    setWindow(90, store.maxT);
    $("#loading").classList.add("done");
  } catch (err) {
    console.error(err);
    $("#loading").innerHTML = `<div><b>Could not load the data.</b><br>${String(err.message || err)}<br><br>
      Open this page through GitHub Pages or a local web server (<code>python -m http.server</code>), not by double-clicking.</div>`;
  }
})();
