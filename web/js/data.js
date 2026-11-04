/*
  data.js  -  Load the CSV files written by run_update.py and tidy them into
  one `store` object that the map and the panels read from.

  To add a new data file: load it in loadAll() and attach it to the store.
*/
import { DATA_DIR, SHIP_CATEGORIES, PORT_RADIUS_KM } from "./config.js";

const parse = (file, optional = false) => new Promise((resolve, reject) =>
  Papa.parse(DATA_DIR + file + "?v=" + Date.now(), {
    download: true, header: true, skipEmptyLines: true,
    complete: (r) => resolve(r.data),
    error: (e) => (optional ? resolve([]) : reject(new Error(`Could not load ${file}: ${e}`))),
  }));

const num = (v) => (v === "" || v == null || isNaN(+v) ? null : +v);
const yes = (v) => v === true || v === "True" || v === "true";

export function categoryOf(type) {
  return (SHIP_CATEGORIES.find((c) => c.match.test(type || "")) || SHIP_CATEGORIES.at(-1)).id;
}

export function distanceKm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}

export async function loadAll() {
  const [features, vessels, ports, refPorts, dark, preds, ids] = await Promise.all([
    parse("output_features.csv"), parse("output_vessels.csv"), parse("output_ports.csv", true),
    parse("reference_ports.csv", true), parse("output_dark_periods.csv", true),
    parse("output_predictions.csv", true), parse("output_identities.csv", true),
  ]);

  const store = {};

  store.vessels = vessels.map((v) => ({ ...v, cat: categoryOf(v.type) }));
  const catOf = Object.fromEntries(store.vessels.map((v) => [v.name, v.cat]));

  store.events = features
    .filter((r) => r.vessel)
    .map((r, i) => ({
      id: i, vessel: r.vessel, name: r.name, mmsi: r.mmsi, imo: r.imo,
      t: Date.parse(r.timestamp), lat: +r.lat, lon: +r.lon, ev: r.event_type || "position",
      speed: num(r.speed_used), course: num(r.course_used), source: r.source, note: r.note,
      port: r.nearest_port, kmToPort: num(r.km_to_port),
      jump: yes(r.impossible_jump) || yes(r.speed_mismatch), calcSpeed: num(r.calc_speed_kn),
      cat: catOf[r.vessel] || "other",
    }))
    .filter((r) => !isNaN(r.t) && !isNaN(r.lat))
    .sort((a, b) => a.t - b.t);

  // Ports: Excel details first, then the reference list fills in the rest
  const byName = new Map();
  for (const p of refPorts) byName.set(p.name.toLowerCase(), {
    ...p, lat: +p.lat, lon: +p.lon, coordsNote: "approximate - verify", fromExcel: false });
  for (const p of ports) {
    const key = p.name.toLowerCase(), ref = byName.get(key) || {};
    const fromExcelCoords = p.coords_source === "excel" && num(p.lat) != null;
    byName.set(key, {
      ...ref, ...Object.fromEntries(Object.entries(p).filter(([, v]) => v !== "")),
      lat: fromExcelCoords ? +p.lat : ref.lat ?? num(p.lat), lon: fromExcelCoords ? +p.lon : ref.lon ?? num(p.lon),
      coordsNote: fromExcelCoords ? "from Excel" : "approximate - verify", fromExcel: true,
    });
  }
  store.ports = [...byName.values()].filter((p) => p.lat != null && p.lon != null);

  // Count watch-list events near each port
  for (const p of store.ports) {
    p.visits = store.events.filter((e) => Math.abs(e.lat - p.lat) < 1 &&
      distanceKm(e.lat, e.lon, p.lat, p.lon) <= PORT_RADIUS_KM);
  }

  store.dark = dark.map((d) => ({
    vessel: d.vessel, t: Date.parse(d.went_dark), tEnd: Date.parse(d.reappeared) || null,
    hours: num(d.hours_dark), km: num(d.km_moved), lat: +d.dark_lat, lon: +d.dark_lon, note: d.note,
  }));
  store.preds = preds.map((p) => ({
    vessel: p.vessel, method: p.method, hours: +p.hours_ahead, t: Date.parse(p.timestamp),
    lat: +p.lat, lon: +p.lon, unc: +p.uncertainty_km,
  }));
  store.identities = Object.fromEntries(ids.map((i) => [i.vessel, {
    names: (i.names_seen || "").split(",").map((s) => s.trim()).filter(Boolean),
    mmsis: (i.mmsis_seen || "").split(",").map((s) => s.trim()).filter(Boolean) }]));

  store.minT = store.events[0]?.t ?? Date.now();
  store.maxT = store.events.at(-1)?.t ?? Date.now();
  return store;
}
