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
    ${row("Flag (Excel)", esc(v.flag))}
    ${row("Type", esc(v.type))}
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
      ${row("Position", `${e.lat.toFixed(3)}, ${e.lon.toFixed(3)}`)}
      ${row("Speed", e.speed != null ? `${e.speed.toFixed(1)} kn` : "")}
      ${row("Near", esc(e.port))}${row("Source", esc(e.source))}</dl>
    ${e.note ? `<p class="note">${esc(e.note)}</p>` : ""}
    ${e.jump ? `<p class="warn">⚠ Impossible move: ${Math.round(e.calcSpeed)} kn needed from the previous record</p>` : ""}
  </div>`;
}
