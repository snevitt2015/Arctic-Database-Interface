/*
  ui.js  -  The panels around the map: ship list, port list, layer controls,
  timeline, alert tables, search and header numbers.

  Each render function draws one part of the screen from `store` + `state`.
  main.js decides WHEN to call them.
*/
import { SHIP_CATEGORIES, EVENT_TYPES, BASEMAPS, OVERLAYS, DATA_LAYERS } from "./config.js";
import { esc, fmtTime, fmtDay } from "./cards.js";

const $ = (sel) => document.querySelector(sel);
const catInfo = (id) => SHIP_CATEGORIES.find((c) => c.id === id) || SHIP_CATEGORIES.at(-1);

// ---------------------------------------------------------------- header numbers
export function renderStats(store, state, visible) {
  const inWin = (x) => state.vessels.has(x.vessel) && x.t >= state.start && x.t <= state.end;
  const set = (id, v) => ($(id).textContent = v.toLocaleString());
  set("#s-events", visible.length);
  set("#s-ships", new Set(visible.map((e) => e.vessel)).size);
  set("#s-dark", store.dark.filter(inWin).length);
  set("#s-jumps", visible.filter((e) => e.jump).length);
  set("#s-ids", Object.keys(store.identities).filter((v) => state.vessels.has(v)).length);
  $("#updated").textContent = "Data to " + fmtDay(store.maxT);
}

// ---------------------------------------------------------------- vessels tab
export function renderVessels(store, state, latestByShip) {
  const q = state.search.toLowerCase();
  const darkCount = {};
  store.dark.forEach((d) => (darkCount[d.vessel] = (darkCount[d.vessel] || 0) + 1));
  const html = SHIP_CATEGORIES.map((c) => {
    const ships = store.vessels.filter((v) => v.cat === c.id &&
      (!q || v.name.toLowerCase().includes(q) || (v.imo || "").includes(q) || (v.mmsi || "").includes(q) ||
       (store.identities[v.name]?.names || []).some((n) => n.toLowerCase().includes(q))));
    if (!ships.length) return "";
    const allOn = ships.every((v) => state.vessels.has(v.name));
    return `<div class="group">
      <label class="group-head"><input type="checkbox" data-cat="${c.id}" ${allOn ? "checked" : ""}>
        <span class="dot" style="background:${c.color}"></span>${esc(c.label)} <span class="count">${ships.length}</span></label>
      ${ships.map((v) => {
        const last = latestByShip[v.name];
        const flags = [darkCount[v.name] ? `<span class="tag red">${darkCount[v.name]}× AIS off</span>` : "",
                       store.identities[v.name] ? `<span class="tag amber">${store.identities[v.name].names.length} names</span>` : ""].join("");
        return `<div class="ship-row ${state.selected === v.name ? "selected" : ""}" data-ship="${esc(v.name)}">
          <input type="checkbox" data-toggle="${esc(v.name)}" ${state.vessels.has(v.name) ? "checked" : ""}>
          <div><div class="name">${esc(v.name)}</div>
          <div class="meta">${last ? `${fmtDay(last.t)} · ${last.lat.toFixed(1)}°, ${last.lon.toFixed(1)}°` : "no records in window"}</div>
          ${flags ? `<div class="flags">${flags}</div>` : ""}</div></div>`;
      }).join("")}</div>`;
  }).join("");
  $("#tab-vessels").innerHTML = html || `<p class="muted pad">No ships match “${esc(state.search)}”.</p>`;
}

// ---------------------------------------------------------------- ports tab
export function renderPorts(store, state) {
  const q = state.search.toLowerCase();
  const ports = store.ports.filter((p) => !q || p.name.toLowerCase().includes(q))
    .sort((a, b) => b.visits.length - a.visits.length || a.name.localeCompare(b.name));
  $("#tab-ports").innerHTML = ports.map((p) => `
    <div class="port-row" data-port="${esc(p.name)}">
      <span class="pin-sm ${p.fromExcel ? "watch" : ""}">⚓</span>
      <div><div class="name">${esc(p.name)}</div>
      <div class="meta">${esc(p.port_type || p.primary_use || "")}${p.visits.length ? ` · ${p.visits.length} watch-list records` : ""}</div></div>
    </div>`).join("") || `<p class="muted pad">No ports match.</p>`;
}

// ---------------------------------------------------------------- layers tab
export function renderLayers(state) {
  const groups = [...new Set(OVERLAYS.map((o) => o.group))];
  $("#tab-layers").innerHTML = `
    <h5>Base map</h5>
    <div class="basemaps">${BASEMAPS.map((b) => `
      <button class="bm ${state.basemap === b.id ? "on" : ""}" data-basemap="${b.id}">
        <span class="thumb" style="background:${b.thumb}"></span>${esc(b.label)}</button>`).join("")}</div>
    ${groups.map((g) => `<h5>${esc(g)}</h5>${OVERLAYS.filter((o) => o.group === g).map((o) => {
      const s = state.overlays[o.id];
      return `<div class="layer-row">
        <label><input type="checkbox" data-overlay="${o.id}" ${s.on ? "checked" : ""}> ${esc(o.label)}</label>
        <input type="range" min="0" max="1" step="0.05" value="${s.opacity}" data-opacity="${o.id}" title="Opacity">
        ${o.note ? `<div class="hint">${esc(o.note)}</div>` : ""}</div>`;
    }).join("")}${g === "NASA daily" ? `<label class="date-row">Image date
        <input type="date" id="overlay-date" value="${state.overlayDate}" max="${state.today}"></label>` : ""}`).join("")}
    <h5>Vessel data</h5>
    ${DATA_LAYERS.map((d) => `<label class="layer-row"><input type="checkbox" data-datalayer="${d.id}" ${state.dataLayers.has(d.id) ? "checked" : ""}> ${esc(d.label)}</label>`).join("")}
    <h5>Event types</h5>
    ${Object.entries(EVENT_TYPES).map(([k, e]) => `<label class="layer-row"><input type="checkbox" data-evtype="${k}" ${state.eventTypes.has(k) ? "checked" : ""}>
      <span class="ev-swatch" style="border-color:${e.ring || "#cbd5e1"};background:${e.hollow ? "transparent" : "#cbd5e1"}"></span> ${esc(e.label)}</label>`).join("")}
    <h5>View</h5>
    <div class="seg"><button data-proj="globe" class="${state.projection === "globe" ? "on" : ""}">3D globe</button>
      <button data-proj="mercator" class="${state.projection === "mercator" ? "on" : ""}">Flat map</button></div>`;
}

// ---------------------------------------------------------------- legend (bottom-left)
export function renderLegend() {
  $("#legend").innerHTML = SHIP_CATEGORIES.filter((c) => c.id !== "other").map((c) =>
    `<span><i style="background:${c.color}"></i>${esc(c.label)}</span>`).join("") +
    `<span><i class="ring red"></i>AIS off</span><span><i class="ring hollow"></i>Loitering</span><span><i class="dash"></i>Dark gap</span>`;
}

// ---------------------------------------------------------------- timeline
export function renderTimeline(store, state) {
  const canvas = $("#histogram"), ctx = canvas.getContext("2d");
  const w = (canvas.width = canvas.clientWidth * devicePixelRatio), h = (canvas.height = canvas.clientHeight * devicePixelRatio);
  ctx.clearRect(0, 0, w, h);
  const bins = 160, span = store.maxT - store.minT || 1, counts = new Array(bins).fill(0), dark = new Array(bins).fill(0);
  for (const e of store.events) {
    if (!state.vessels.has(e.vessel)) continue;
    const i = Math.min(bins - 1, Math.floor(((e.t - store.minT) / span) * bins));
    counts[i]++; if (e.ev === "ais_off" || e.jump) dark[i]++;
  }
  const max = Math.max(1, ...counts), bw = w / bins;
  const x0 = ((state.start - store.minT) / span) * w, x1 = ((state.end - store.minT) / span) * w;
  ctx.fillStyle = "rgba(56,189,248,.10)"; ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
  counts.forEach((c, i) => {
    const bh = (Math.sqrt(c) / Math.sqrt(max)) * (h - 4), x = i * bw, inWin = x + bw >= x0 && x <= x1;
    ctx.fillStyle = inWin ? "rgba(125,211,252,.9)" : "rgba(148,163,184,.35)";
    ctx.fillRect(x + 0.5, h - bh, Math.max(1, bw - 1), bh);
    if (dark[i]) { ctx.fillStyle = "#ef4444"; ctx.fillRect(x + 0.5, h - 3 * devicePixelRatio, Math.max(1, bw - 1), 3 * devicePixelRatio); }
  });
  ctx.strokeStyle = "#7dd3fc"; ctx.lineWidth = 1.5 * devicePixelRatio;
  ctx.beginPath(); ctx.moveTo(x1, 0); ctx.lineTo(x1, h); ctx.stroke();
  $("#range-label").textContent = `${fmtDay(state.start)}  →  ${fmtDay(state.end)}`;
  $("#tl-start").textContent = fmtDay(store.minT).slice(0, 4);
  $("#tl-end").textContent = fmtDay(store.maxT);
}

// ---------------------------------------------------------------- alerts drawer
export const ALERT_TABS = {
  dark: {
    label: "AIS switched off",
    rows: (store, state, visible) => store.dark.filter((d) => state.vessels.has(d.vessel) && d.t >= state.start && d.t <= state.end),
    cols: [["Ship", (r) => r.vessel], ["Went dark", (r) => fmtTime(r.t)], ["Hours dark", (r) => r.hours ?? "?", "num"],
           ["Km moved", (r) => r.km ?? "?", "num"], ["Where", (r) => `${r.lat.toFixed(2)}, ${r.lon.toFixed(2)}`]],
  },
  jumps: {
    label: "Impossible moves",
    rows: (store, state, visible) => visible.filter((e) => e.jump),
    cols: [["Ship", (r) => r.vessel], ["Time", (r) => fmtTime(r.t)], ["Reported as", (r) => `${r.name} · ${r.mmsi}`],
           ["Implied speed (kn)", (r) => Math.round(r.calcSpeed ?? 0), "num"], ["Where", (r) => `${r.lat.toFixed(2)}, ${r.lon.toFixed(2)}`]],
  },
  meet: {
    label: "Encounters & loitering",
    rows: (store, state, visible) => visible.filter((e) => e.ev === "encounter" || e.ev === "loitering").reverse(),
    cols: [["Ship", (r) => r.vessel], ["Time", (r) => fmtTime(r.t)], ["Type", (r) => EVENT_TYPES[r.ev].label],
           ["Where", (r) => `${r.lat.toFixed(2)}, ${r.lon.toFixed(2)}`], ["Note", (r) => r.note]],
  },
  ids: {
    label: "Identity changes",
    rows: (store, state) => Object.entries(store.identities).filter(([v]) => state.vessels.has(v)).map(([vessel, i]) => ({ vessel, ...i })),
    cols: [["Ship (Excel)", (r) => r.vessel], ["Names used", (r) => r.names.join(", ")], ["MMSIs used", (r) => r.mmsis.join(", ")]],
  },
};

export function renderAlerts(store, state, visible) {
  const tabs = Object.entries(ALERT_TABS).map(([k, t]) => ({ k, t, rows: t.rows(store, state, visible) }));
  // the badge counts warning signs only: AIS off, impossible moves and encounters (not routine loitering)
  $("#alert-count").textContent = tabs.reduce((n, x) => n + (x.k === "dark" || x.k === "jumps" ? x.rows.length
    : x.k === "meet" ? x.rows.filter((r) => r.ev === "encounter").length : 0), 0);
  $("#alert-tabs").innerHTML = tabs.map(({ k, t, rows }) =>
    `<button data-alert-tab="${k}" class="${state.alertTab === k ? "on" : ""}">${esc(t.label)} <span class="count">${rows.length}</span></button>`).join("");
  const cur = tabs.find((x) => x.k === state.alertTab);
  $("#alert-table").innerHTML = cur.rows.length
    ? `<table><thead><tr>${cur.t.cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${
        cur.rows.slice(0, 500).map((r, i) => `<tr class="click" data-alert-row="${i}">${cur.t.cols.map((c) => `<td class="${c[2] || ""}">${esc(c[1](r))}</td>`).join("")}</tr>`).join("")
      }</tbody></table>`
    : `<p class="muted pad">Nothing for the selected ships in this time window.</p>`;
  return cur.rows;
}
