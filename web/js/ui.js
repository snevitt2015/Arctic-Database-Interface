/*
  ui.js  -  The panels around the map: ship list, port list, layer controls,
  timeline, alert tables, search and header numbers.

  Each render function draws one part of the screen from `store` + `state`.
  main.js decides WHEN to call them.
*/
import { SHIP_CATEGORIES, EVENT_TYPES, BASEMAPS, OVERLAYS, DATA_LAYERS, VECTOR_LAYERS, FOCUS } from "./config.js";
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
  const pings = (store.cues || []).filter((c) => state.vessels.has(c.vessel));
  set("#s-pings", pings.filter((c) => c.priority !== "low").length);
  $("#s-pings-new").textContent = pings.some((c) => c.isNew) ? `${pings.filter((c) => c.isNew).length} new` : "";
  $("#updated").textContent = "Data to " + fmtDay(store.maxT);
}

// ---------------------------------------------------------------- vessels tab
export function renderVessels(store, state, latestByShip) {
  if (state.focus) return renderFocusVessels(store, state, latestByShip);
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
          <div class="meta">${last ? `${fmtDay(last.t)} · ${last.lat.toFixed(1)}°, ${last.lon.toFixed(1)}°` : lastCheck(store, v)}</div>
          ${flags ? `<div class="flags">${flags}</div>` : ""}</div></div>`;
      }).join("")}</div>`;
  }).join("");
  $("#tab-vessels").innerHTML = html || `<p class="muted pad">No ships match “${esc(state.search)}”.</p>`;
}

// ---------------------------------------------------------------- vessels tab, focus mode: "what's new" + ships ranked by warning signs
const matches = (store, v, q) => !q || v.name.toLowerCase().includes(q) || (v.imo || "").includes(q) || (v.mmsi || "").includes(q) ||
  (store.identities[v.name]?.names || []).some((n) => n.toLowerCase().includes(q));

function renderFocusVessels(store, state, latestByShip) {
  const q = state.search.toLowerCase();
  const feed = store.feed.filter((f) => state.vessels.has(f.vessel)).slice(0, 8);
  const feedHtml = q ? "" : `<div class="feed">
    <h5>What's new <span class="count">last ${FOCUS.FEED_DAYS} days of data, to ${fmtDay(store.maxT)}</span></h5>
    ${feed.length ? feed.map((f) => `<div class="feed-item ${f.sev}" data-feed="${store.feed.indexOf(f)}">
        <span class="feed-dot"></span>
        <div><div><b>${esc(f.vessel)}</b> <span class="meta">${fmtDay(f.t)}</span></div>
        <div class="feed-text">${esc(f.text)}</div></div></div>`).join("")
      : `<p class="muted feed-empty">No new warning signs in this period.</p>`}
  </div>`;

  const ranked = store.vessels.filter((v) => matches(store, v, q))
    .sort((a, b) => (store.risk[b.name]?.score ?? 0) - (store.risk[a.name]?.score ?? 0));
  const row = (v) => {
    const r = store.risk[v.name], last = latestByShip[v.name];
    const chips = (r?.reasons || []).slice(0, 3).map((x) => `<span class="tag ${x.sev}${x.recent ? " recent" : ""}">${esc(x.label)}</span>`).join("");
    return `<div class="ship-row ${state.selected === v.name ? "selected" : ""}" data-ship="${esc(v.name)}">
      <input type="checkbox" data-toggle="${esc(v.name)}" ${state.vessels.has(v.name) ? "checked" : ""}>
      <div><div class="name"><span class="dot sm" style="background:${r?.color}"></span>${esc(v.name)}
        ${r?.score ? `<span class="score" title="Warning-sign score (weights in config.js)">${r.score}</span>` : ""}</div>
      <div class="meta">${last ? `last seen ${fmtDay(last.t)}` : lastCheck(store, v)}</div>
      ${chips ? `<div class="flags">${chips}</div>` : ""}</div></div>`;
  };
  const groups = FOCUS.LEVELS.map((l) => {
    const ships = ranked.filter((v) => store.risk[v.name]?.level === l.id);
    if (!ships.length) return "";
    const body = ships.map(row).join("");
    const head = `<span class="dot" style="background:${l.color}"></span>${esc(l.label)} <span class="count">${ships.length}</span>`;
    return l.id === "quiet" && !q
      ? `<details class="group quiet-group"><summary class="group-head">${head}</summary>${body}</details>`
      : `<div class="group"><div class="group-head">${head}</div>${body}</div>`;
  }).join("");
  $("#tab-vessels").innerHTML = feedHtml + (groups || `<p class="muted pad">No ships match “${esc(state.search)}”.</p>`) +
    `<p class="hint pad">Score = AIS-off periods, impossible moves, extra MMSIs and names, and encounters at sea. Recent signals count double. These are leads to verify, not conclusions.</p>`;
}

function lastCheck(store, v) {
  const c = (store.checks[v.imo] || []).filter((x) => x.kind === "check").at(-1);
  return c ? `AIS check ${fmtDay(c.t)} · ${esc(c.area || "?")}` : "no records in window";
}

// ---------------------------------------------------------------- fleet tab (Capstone registry)
export function renderFleet(store, state) {
  const q = state.search.toLowerCase();
  const counts = {};
  store.fleet.forEach((f) => (counts[f.type] = (counts[f.type] || 0) + 1));
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = top[0]?.[1] || 1;
  let list = store.fleet.filter((f) => (!state.fleetType || f.type === state.fleetType) &&
    (!q || f.name.toLowerCase().includes(q) || f.imo.includes(q) || f.type.toLowerCase().includes(q)));
  if (!q && !state.fleetType) list = list.filter((f) => f.tracked);
  const shown = list.sort((a, b) => (b.tracked - a.tracked) || (b.gt || 0) - (a.gt || 0)).slice(0, 80);
  const onList = new Set(store.vessels.map((v) => v.imo));
  $("#tab-fleet").innerHTML = `
    <div class="fleet-sum"><div><b>${store.fleet.length.toLocaleString()}</b><span>Russian-flagged ships in registry</span></div>
      <div><b>${store.fleet.filter((f) => f.tracked).length}</b><span>tracked by the team</span></div></div>
    <h5>Fleet by type ${state.fleetType ? `<a href="#" data-fleet-type="">clear filter</a>` : "· click to filter"}</h5>
    ${top.slice(0, state.showAllTypes ? 999 : 12).map(([t, n]) => `
      <div class="bar click ${state.fleetType === t ? "on" : ""}" data-fleet-type="${esc(t)}"><span>${esc(t)}</span>
        <i style="width:${(n / max) * 100}%;background:${catInfo(SHIP_CATEGORIES.find((c) => c.match.test(t))?.id).color}"></i><b>${n}</b></div>`).join("")}
    ${top.length > 12 ? `<a href="#" class="more" data-more-types>${state.showAllTypes ? "show fewer" : `show all ${top.length} types`}</a>` : ""}
    <h5>${q || state.fleetType ? `${list.length.toLocaleString()} matching ships${list.length > 80 ? " (largest 80 shown)" : ""}` : "Ships the team tracks"}</h5>
    ${shown.map((f) => `<div class="ship-row fleet" data-fleet="${esc(f.imo)}">
        <span class="dot" style="background:${catInfo(f.cat).color};margin-top:5px"></span>
        <div><div class="name">${esc(f.name)} ${onList.has(f.imo) ? `<span class="tag green">watch list</span>` : ""}</div>
        <div class="meta">${esc(f.type)} · IMO ${esc(f.imo)} · ${f.gt ? f.gt.toLocaleString() + " GT" : ""} · ${esc(f.year)}</div></div></div>`).join("")
      || `<p class="muted pad">No ships match.</p>`}
    ${store.typeStats.length ? `<h5>Team's ship-type statistics</h5><table class="mini"><tbody>${store.typeStats.map((s) =>
      `<tr><td>${esc(s.ship_type)}</td><td class="num">${esc(s.number_in_service)}</td></tr>`).join("")}</tbody></table>` : ""}`;
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
    ${VECTOR_LAYERS.map((cfg) => {
      const feats = state.vectorData[cfg.id]?.features || [];
      return `<h5>${esc(cfg.label)}</h5>` + feats.map((ft) => `<label class="layer-row">
        <input type="checkbox" data-vector="${cfg.id}" data-feature="${esc(ft.properties.id)}" ${state.vectorsOn[cfg.id].has(ft.properties.id) ? "checked" : ""}>
        <span class="route-swatch ${(cfg.dashed || []).includes(ft.properties.id) ? "dashed" : ""}" style="border-color:${esc(ft.properties.color)}"></span> ${esc(ft.properties.name)}</label>`).join("");
    }).join("")}
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
export function renderLegend(focus = false) {
  if (focus) {
    $("#legend").innerHTML = FOCUS.LEVELS.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label.split(" - ")[0])}</span>`).join("") +
      `<span><i class="ring red"></i>AIS off</span><span><i style="background:#ef4444"></i>Impossible move</span>` +
      `<span><i style="background:#f59e0b"></i>Encounter</span><span><i class="dash"></i>Dark gap</span>`;
    return;
  }
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
    ctx.fillStyle = state.focus ? (inWin ? "rgba(125,211,252,.35)" : "rgba(148,163,184,.18)") : inWin ? "rgba(125,211,252,.9)" : "rgba(148,163,184,.35)";
    ctx.fillRect(x + 0.5, h - bh, Math.max(1, bw - 1), bh);
    if (dark[i] && state.focus) {   // focus: a full-height red band + a marker on top, so warning days are easy to find
      ctx.fillStyle = "rgba(239,68,68,.13)"; ctx.fillRect(x, 0, Math.max(2, bw), h);
      ctx.fillStyle = "#ef4444"; ctx.fillRect(x, 0, Math.max(2, bw), 4 * devicePixelRatio);
    } else if (dark[i]) { ctx.fillStyle = "#ef4444"; ctx.fillRect(x + 0.5, h - 3 * devicePixelRatio, Math.max(1, bw - 1), 3 * devicePixelRatio); }
  });
  ctx.strokeStyle = "#7dd3fc"; ctx.lineWidth = 1.5 * devicePixelRatio;
  ctx.beginPath(); ctx.moveTo(x1, 0); ctx.lineTo(x1, h); ctx.stroke();
  $("#range-label").textContent = `${fmtDay(state.start)}  →  ${fmtDay(state.end)}`;
  $("#tl-start").textContent = fmtDay(store.minT).slice(0, 4);
  $("#tl-end").textContent = fmtDay(store.maxT);
}

// ---------------------------------------------------------------- alerts drawer
export const ALERT_TABS = {
  pings: {
    label: "Pings",
    rows: (store, state) => (store.cues || []).filter((c) => state.vessels.has(c.vessel)),
    cols: [["", (r) => (r.isNew ? "● new" : "")], ["Priority", (r) => r.priority], ["Type", (r) => r.kind.replace("_", " ")],
           ["Ship", (r) => r.vessel], ["From", (r) => fmtTime(r.t)], ["Why", (r) => r.reason],
           ["SAR", (r) => r.scenes.filter((s) => s.kind === "SAR").length, "num"],
           ["Optical", (r) => r.scenes.filter((s) => s.kind === "Optical").length, "num"],
           ["Detections", (r) => r.detections.filter((d) => +d.detection > 0).length, "num"]],
  },
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
  checks: {
    label: "Data checks",
    rows: (store) => store.dataChecks || [],
    cols: [["Severity", (r) => r.severity], ["Table", (r) => r.table], ["Record", (r) => r.record],
           ["Field", (r) => r.field], ["Problem", (r) => r.problem], ["Value", (r) => r.value]],
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
  $("#alert-count").textContent = tabs.reduce((n, x) => n + (x.k === "pings" ? x.rows.filter((r) => r.priority === "high").length
    : x.k === "dark" || x.k === "jumps" ? x.rows.length
    : x.k === "checks" ? x.rows.filter((r) => r.severity === "error").length
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
