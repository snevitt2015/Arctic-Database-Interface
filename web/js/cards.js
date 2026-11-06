/*
  cards.js  -  The information cards (ship, port, single event).

  Each card is a list of SECTIONS. A section is a function that gets the data
  and returns HTML (or "" to hide itself). To show new information - e.g.
  sanctions lists, ownership, cargo, satellite detections - write one function
  and add it to VESSEL_SECTIONS or PORT_SECTIONS. Nothing else needs to change.
*/
import { SHIP_CATEGORIES, EVENT_TYPES } from "./config.js";

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmtTime = (t) => (t ? new Date(t).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—");
export const fmtDay = (t) => (t ? new Date(t).toISOString().slice(0, 10) : "—");
const cat = (id) => SHIP_CATEGORIES.find((c) => c.id === id) || SHIP_CATEGORIES.at(-1);
const evLabel = (ev) => EVENT_TYPES[ev]?.label || ev;
const row = (k, v) => (v === "" || v == null ? "" : `<dt>${esc(k)}</dt><dd>${v}</dd>`);
const section = (title, body) => (body ? `<section><h4>${esc(title)}</h4>${body}</section>` : "");
const ago = (t, ref) => { const d = Math.round((ref - t) / 864e5); return d <= 0 ? "on the latest day of data" : `${d} day${d > 1 ? "s" : ""} before the latest data`; };

// ---------------------------------------------------------------- ship card
export const VESSEL_SECTIONS = [
  // Identity from the Excel sheet
  ({ v }) => section("Identity", `<dl>
    ${row("IMO", esc(v.imo) || "<span class='muted'>missing</span>")}
    ${row("MMSI", esc(v.mmsi))}
    ${row("Call sign", esc(v.call_sign))}
    ${row("Flag", esc(v.flag))}
    ${row("Type", esc(v.type))}
    ${row("Gross tonnage", v.gross_tonnage ? Math.round(+v.gross_tonnage).toLocaleString() : "")}
    ${row("Built", esc(String(v.year_built || "").replace(".0", "")))}
    ${row("Listed in", esc(v.list_source))}
    ${row("Sanctioned", v.sanctioned ? `<span class="tag red">${esc(v.sanctioned)}</span>` : "<span class='muted'>not recorded</span>")}
    ${row("Notes", esc(v.notes))}</dl>`),

  // Last seen + quick stats
  ({ events, store }) => {
    const last = events.at(-1);
    if (!last) return section("Activity", "<p class='muted'>No OSINT records yet.</p>");
    const count = (ev) => events.filter((e) => e.ev === ev).length;
    return section("Activity", `<dl>
      ${row("Last seen", `${fmtTime(last.t)}<br><span class="muted">${ago(last.t, store.maxT)}</span>`)}
      ${row("Position", `${last.lat.toFixed(3)}, ${last.lon.toFixed(3)}`)}
      ${row("Last event", esc(evLabel(last.ev)) + (last.port ? ` · near ${esc(last.port)}` : ""))}
      ${row("Speed / course", last.speed != null ? `${last.speed.toFixed(1)} kn · ${Math.round(last.course ?? 0)}°` : "<span class='muted'>unknown</span>")}
      ${row("Records", `${events.length.toLocaleString()} since ${fmtDay(events[0].t)}`)}</dl>
      <div class="chips">
        <span class="chip">${count("port_visit")} port visits</span>
        <span class="chip">${count("loitering")} loitering</span>
        <span class="chip">${count("encounter")} encounters</span>
        <span class="chip ${count("ais_off") ? "red" : ""}">${count("ais_off")} AIS off</span>
        <span class="chip ${events.filter((e) => e.jump).length ? "red" : ""}">${events.filter((e) => e.jump).length} impossible moves</span>
      </div>`);
  },

  // Weekly manual AIS checks from the Capstone Database
  ({ v, store }) => {
    const list = store.checks[v.imo]?.filter((c) => c.kind === "check") || [];
    if (!list.length) return "";
    const last = list.at(-1);
    const status = (s) => `<span class="tag ${/live/i.test(s) ? "green" : /very stale|out of range/i.test(s) ? "red" : "amber"}">${esc(s || "?")}</span>`;
    return section(`Manual AIS checks · ${list.length} (Capstone DB)`, `
      <div class="check-latest">
        <div><b>${esc(last.area || "Area unknown")}</b> ${status(last.ais_status)}</div>
        <div class="muted">Checked ${fmtTime(last.t)} · position ${last.tPos ? fmtTime(last.tPos) : "time unknown"}${last.position_age ? ` (${esc(last.position_age)} old)` : ""}</div>
        <dl>${row("Nav status", esc(last.nav_status))}
          ${row("Speed / course", last.speed != null ? `${last.speed} kn · ${last.course ?? "?"}°` : "")}
          ${row("Draught", last.draught != null ? `${last.draught} m` : "")}
          ${row("Destination", esc(last.destination))}${row("Last voyage", esc(last.last_voyage))}</dl>
        ${last.notes ? `<p class="note">${esc(last.notes)}</p>` : ""}
        ${last.lat ? "" : `<p class="hint">No latitude/longitude in this check, so it isn't plotted. Fill them in Airtable and it will appear on the map and feed the prediction.</p>`}
      </div>
      <table class="mini"><thead><tr><th>Checked</th><th>Area</th><th>Status</th><th>kn</th><th>Dest.</th></tr></thead><tbody>${
        list.slice().reverse().map((c) => `<tr><td>${fmtDay(c.t)}</td><td>${esc(c.area)}</td><td>${esc(c.ais_status)}</td>
          <td class="num">${c.speed ?? ""}</td><td>${esc(c.destination)}</td></tr>`).join("")}</tbody></table>`);
  },

  // Names and radio IDs used over the years (shadow-fleet tactic)
  ({ v, store, events }) => {
    const id = store.identities[v.name];
    if (!id) return "";
    const first = {}, lastSeen = {};
    events.forEach((e) => { const k = (e.name || "").toUpperCase() + "|" + e.mmsi; first[k] ??= e.t; lastSeen[k] = e.t; });
    const rows = Object.keys(first).sort((a, b) => first[a] - first[b]).map((k) => {
      const [n, m] = k.split("|");
      return `<tr><td>${esc(n)}</td><td class="mono">${esc(m)}</td><td>${fmtDay(first[k])} → ${fmtDay(lastSeen[k])}</td></tr>`;
    }).join("");
    return section(`Identity history · ${id.names.length} names, ${id.mmsis.length} MMSIs`,
      `<table class="mini"><thead><tr><th>Name</th><th>MMSI</th><th>Seen</th></tr></thead><tbody>${rows}</tbody></table>`);
  },

  // Ports most visited
  ({ events }) => {
    const counts = {};
    events.filter((e) => e.ev === "port_visit").forEach((e) => {
      const m = /Port: ([^(]+)/.exec(e.note || ""); const name = (m ? m[1] : e.port || "?").trim();
      counts[name] = (counts[name] || 0) + 1;
    });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
    if (!top.length) return "";
    const max = top[0][1];
    return section("Most visited ports", top.map(([n, c]) =>
      `<div class="bar"><span>${esc(n)}</span><i style="width:${(c / max) * 100}%"></i><b>${c}</b></div>`).join(""));
  },

  // Satellite & SAR images linked to this ship
  ({ v, store }) => {
    const imgs = store.imagery.filter((i) => i.imo && i.imo === v.imo);
    if (!imgs.length) return "";
    return section(`Satellite imagery · ${imgs.length}`, imgs.map((i) =>
      `<div class="alert-item"><b>${esc(i.kind)}</b> · ${esc(i.date)} · ${esc(i.platform)} · ${esc(i.resolution_m)} m
       <span class="tag ${/^verified/i.test(i.verification) ? "green" : "amber"}">${esc(i.verification || "unverified")}</span><br>
       <span class="muted">${esc(i.location)}</span> ${i.lat != null ? `<a href="#" data-fly="${i.lon},${i.lat}">show</a>` : ""}</div>`).join(""));
  },

  // 3D models
  ({ v, store }) => {
    const ms = store.models[v.imo] || [];
    if (!ms.length) return "";
    return section("3D models", ms.map((m) =>
      `<div class="alert-item"><b>${esc(m.model_name)}</b> · ${esc(m.source)} ${m.file_format ? `· ${esc(m.file_format)}` : ""}
       ${m.link ? `<br><a href="${esc(m.link)}" target="_blank" rel="noopener">Open model page ↗</a>` : "<br><span class='muted'>no link yet</span>"}</div>`).join(""));
  },

  // Web sources the team linked to this ship
  ({ v, store }) => {
    const src = (store.checks[v.imo] || []).filter((c) => c.kind === "source_link");
    const dataLinks = [...new Set((store.checks[v.imo] || []).map((c) => c.data_source).filter((d) => /^https?:/.test(d)))];
    const links = [...src.map((s) => s.update_id), ...dataLinks];
    if (!links.length) return "";
    return section("Sources", links.map((l) => `<div class="src"><a href="${esc(l)}" target="_blank" rel="noopener">${esc(l.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60))}…</a></div>`).join(""));
  },

  // Dark periods
  ({ v, store }) => {
    const d = store.dark.filter((x) => x.vessel === v.name);
    if (!d.length) return "";
    return section("AIS switched off", d.map((x) =>
      `<div class="alert-item"><b>${fmtTime(x.t)}</b><br>${x.hours ?? "?"} h dark · moved ${x.km ?? "?"} km
       <a href="#" data-fly="${x.lon},${x.lat}">show</a></div>`).join(""));
  },
];

export function vesselCard(store, name) {
  const v = store.vessels.find((x) => x.name === name) || { name };
  const events = store.events.filter((e) => e.vessel === name);
  const c = cat(v.cat);
  return `<header class="card-head">
      <span class="dot" style="background:${c.color}"></span>
      <div><h3>${esc(v.name)}</h3><div class="muted">${esc(c.label)}</div></div>
      <button class="icon-btn" data-close title="Close">×</button>
    </header>
    <div class="card-actions"><button class="btn" data-zoom-ship="${esc(name)}">Zoom to ship</button>
      <button class="btn ghost" data-only-ship="${esc(name)}">Show only this ship</button></div>
    ${VESSEL_SECTIONS.map((s) => s({ v, events, store })).join("")}`;
}

// ---------------------------------------------------------------- port card
export const PORT_SECTIONS = [
  ({ p }) => section("Port profile", `<dl>
    ${row("Country", esc(p.country))}
    ${row("Type", esc(p.port_type || p.primary_use))}
    ${row("Primary use", esc(p.primary_use))}
    ${row("Berths", esc(p.berths))}
    ${row("Depth / capacity", esc(p.depth_capacity))}
    ${row("Operator", esc(p.managing_company))}
    ${row("Infrastructure", esc(p.infrastructure))}
    ${row("Coordinates", `${p.lat.toFixed(3)}, ${p.lon.toFixed(3)} <span class="tag ${p.coordsNote === "from Excel" ? "" : "amber"}">${esc(p.coordsNote)}</span>`)}
    </dl>${p.notes ? `<p class="note">${esc(p.notes)}</p>` : ""}`),

  ({ p }) => {
    if (!p.visits.length) return section("Watch-list activity", "<p class='muted'>No watch-list ships recorded within 25 km.</p>");
    const ships = {};
    p.visits.forEach((e) => { const s = (ships[e.vessel] ??= { n: 0, last: 0, cat: e.cat }); s.n++; s.last = Math.max(s.last, e.t); });
    return section(`Watch-list activity · ${p.visits.length} records within 25 km`,
      `<table class="mini"><thead><tr><th>Ship</th><th>Records</th><th>Last</th></tr></thead><tbody>${
        Object.entries(ships).sort((a, b) => b[1].last - a[1].last).map(([n, s]) =>
          `<tr class="click" data-ship="${esc(n)}"><td><span class="dot sm" style="background:${cat(s.cat).color}"></span>${esc(n)}</td>
           <td class="num">${s.n}</td><td>${fmtDay(s.last)}</td></tr>`).join("")}</tbody></table>`);
  },
];

export function portCard(store, p) {
  return `<header class="card-head">
      <span class="dot port">⚓</span>
      <div><h3>${esc(p.name)}</h3><div class="muted">${p.fromExcel ? "Watch-list port (Excel)" : "Reference port"}</div></div>
      <button class="icon-btn" data-close title="Close">×</button>
    </header>
    <div class="card-actions"><button class="btn" data-zoom-port>View port up close</button></div>
    ${PORT_SECTIONS.map((s) => s({ p, store })).join("")}`;
}

// ---------------------------------------------------------------- popup for one event
export function eventPopup(e) {
  const c = cat(e.cat);
  return `<div class="pop">
    <div class="pop-head"><span class="dot" style="background:${c.color}"></span><b>${esc(e.vessel)}</b></div>
    <div class="pop-ev">${esc(evLabel(e.ev))}</div>
    <dl>${row("Time", fmtTime(e.t))}${row("Reported as", `${esc(e.name)} · MMSI ${esc(e.mmsi)}`)}
      ${row("Position", e.ev === "area_report" ? `${e.lat.toFixed(1)}, ${e.lon.toFixed(1)} <span class="tag amber">± ${Math.round(e.posErr)} km - area name only</span>`
                                              : `${e.lat.toFixed(3)}, ${e.lon.toFixed(3)}`)}
      ${row("Speed", e.speed != null ? `${e.speed.toFixed(1)} kn` : "")}
      ${row("Near", esc(e.port))}${row("Source", esc(e.source))}</dl>
    ${e.note ? `<p class="note">${esc(e.note)}</p>` : ""}
    ${e.jump ? `<p class="warn">⚠ Impossible move: ${Math.round(e.calcSpeed)} kn needed from the previous record</p>` : ""}
  </div>`;
}

// ---------------------------------------------------------------- registry ship (not on the watch list)
export function fleetCard(f) {
  const c = cat(f.cat);
  return `<header class="card-head">
      <span class="dot" style="background:${c.color}"></span>
      <div><h3>${esc(f.name)}</h3><div class="muted">${esc(f.type)}</div></div>
      <button class="icon-btn" data-close title="Close">×</button>
    </header>
    ${section("Registry (Capstone DB)", `<dl>${row("IMO", esc(f.imo))}${row("Flag", esc(f.flag))}
      ${row("Gross tonnage", f.gt ? f.gt.toLocaleString() : "")}${row("Built", esc(f.year))}</dl>
      <p class="hint">Not on the watch list, so no tracking data is downloaded. To track it, link an AIS update to it in Airtable
      (or add it to the Excel Vessels tab) - the next daily update will pull its Global Fishing Watch history.</p>`)}`;
}

// ---------------------------------------------------------------- satellite / SAR image
export function imageryCard(i, store) {
  const ship = i.imo ? store.fleetByImo[i.imo] : null;
  return `<header class="card-head">
      <span class="dot port">${i.kind === "SAR" ? "📡" : "🛰"}</span>
      <div><h3>${esc(i.kind)} image</h3><div class="muted">${esc(i.image_id)}</div></div>
      <button class="icon-btn" data-close title="Close">×</button>
    </header>
    ${section("Acquisition", `<dl>${row("Date", esc(i.date))}${row("Sensor", esc(i.platform))}
      ${row("Resolution", i.resolution_m ? `${esc(i.resolution_m)} m` : "")}${row("Details", esc(i.detail))}
      ${row("Location", esc(i.location))}${row("Provider", esc(i.provider))}
      ${row("Linked ship", ship ? `<a href="#" data-ship="${esc(ship.name)}">${esc(ship.name)}</a>` : "<span class='muted'>none</span>")}
      ${row("Annotation", esc(i.annotation))}${row("Verification", esc(i.verification))}
      ${row("Link", i.link ? `<a href="${esc(i.link)}" target="_blank" rel="noopener">open ↗</a>` : "")}</dl>`)}
    ${i.summary ? section("Analyst summary", `<p class="note">${esc(i.summary)}</p>`) : ""}
    ${i.notes ? section("Notes", `<p>${esc(i.notes)}</p>`) : ""}`;
}

// ---------------------------------------------------------------- sea route popup
export function routePopup(p) {
  return `<div class="pop"><div class="pop-head"><span class="dot" style="background:${esc(p.color)}"></span><b>${esc(p.name)}</b></div>
    <p style="margin:6px 0 0">${esc(p.about)}</p><p class="hint">Schematic line for orientation - not for navigation.</p></div>`;
}

// ---------------------------------------------------------------- ping (cue) card
const KIND_LABEL = { ais_off: "AIS switched off", spoofing: "Possible spoofing", silent: "No recent record", identity: "Identity change" };
export function cueCard(c, store) {
  const sar = c.scenes.filter((s) => s.kind === "SAR"), opt = c.scenes.filter((s) => s.kind === "Optical");
  const sceneRow = (s) => `<tr><td>${fmtTime(s.t).replace(" UTC", "")}</td><td>${esc(s.satellite)}</td>
    <td>${esc(s.mode || "")}${s.orbit_direction ? " · " + esc(s.orbit_direction.slice(0, 4).toLowerCase()) : ""}</td>
    <td>${s.kind === "Optical" ? `${s.sunEl != null ? s.sunEl.toFixed(0) + "° sun" : ""}${s.cloud_cover ? " · " + Math.round(+s.cloud_cover) + "% cloud" : ""}` : esc(s.polarisation || "")}</td>
    <td>${s.covers ? "✓" : ""}</td><td><a href="${esc(s.link)}" target="_blank" rel="noopener">↗</a></td></tr>`;
  const table = (list) => `<table class="mini"><thead><tr><th>Time (UTC)</th><th>Satellite</th><th>Mode</th><th></th><th title="Covers the centre of the search area">Ctr</th><th></th></tr></thead>
    <tbody>${list.map(sceneRow).join("")}</tbody></table>`;
  const dets = c.detections.filter((d) => +d.detection > 0);
  const chips = [...new Set(c.detections.map((d) => d.chip).filter(Boolean))];
  return `<header class="card-head">
      <span class="dot" style="background:${c.priority === "high" ? "#ef4444" : c.priority === "medium" ? "#f59e0b" : "#94a3b8"}"></span>
      <div><h3>${esc(KIND_LABEL[c.kind] || c.kind)}</h3><div class="muted">${esc(c.vessel)} · ${esc(c.priority)} priority${c.isNew ? ' · <span class="tag red">new</span>' : ""}</div></div>
      <button class="icon-btn" data-close title="Close">×</button>
    </header>
    <div class="card-actions"><button class="btn" data-zoom-cue="${esc(c.cue_id)}">Show search area</button>
      <button class="btn ghost" data-ship="${esc(c.vessel)}">Ship card</button></div>
    ${section("Why", `<p>${esc(c.reason)}</p><dl>
      ${row("Look between", `${fmtTime(c.t)}<br>${fmtTime(c.tEnd)}`)}
      ${row("Search area", `${c.lat.toFixed(2)}, ${c.lon.toFixed(2)} · radius ${Math.round(c.radius)} km${c.radius_capped === "True" ? " (capped)" : ""}`)}
      ${row("First flagged", esc(c.first_seen))}</dl>`)}
    ${section(`Radar (Sentinel-1) · ${sar.length} scenes`, sar.length ? table(sar)
      : "<p class='muted'>None found yet. Searches run with the daily update.</p>")}
    ${section(`Optical (Sentinel-2) · ${opt.length} scenes`, opt.length ? table(opt)
      : "<p class='muted'>None (cloud, polar night, or not searched yet).</p>")}
    ${chips.length || dets.length ? section(`Detections · ${dets.length}`, `
      ${chips.map((ch) => `<a href="${esc(ch)}" target="_blank" rel="noopener"><img class="chip" src="${esc(ch)}" alt="SAR chip"></a>`).join("")}
      ${dets.map((d) => `<div class="alert-item"><b>#${esc(d.detection)}</b> ${d.length ?? "?"} × ${d.width ?? "?"} m · heading ${esc(d.heading_deg)}°
        · ${d.dark ? '<span class="tag red">no AIS match - possible dark vessel</span>'
                    : `best match <b>${esc(d.best_vessel)}</b> (${d.score})`}
        ${d.missing ? `<br><span class="hint">No registry ${esc(d.missing.replaceAll("_m", "").replace(",", " / "))} - add ship dimensions in Airtable to use size in the score</span>` : ""}</div>`).join("")}
      <p class="hint">Contains modified Copernicus Sentinel data.</p>`) : ""}`;
}

export function detectionPopup(c, d) {
  return `<div class="pop"><div class="pop-head"><b>SAR detection #${esc(d.detection)}</b></div>
    <div class="pop-ev">${esc(c.vessel)} ping · ${fmtTime(Date.parse(d.time_utc))}</div>
    <dl>${row("Size", `${d.length ?? "?"} × ${d.width ?? "?"} m (±${esc(d.length_err_m)} m)`)}${row("Heading", `${esc(d.heading_deg)}° (or +180°)`)}
      ${row("Best match", d.dark ? "none - possible dark vessel" : `${esc(d.best_vessel)} · score ${d.score}`)}
      ${row("Other", esc(d.other_candidates))}</dl></div>`;
}
