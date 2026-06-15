/* Info block: the driver's in-race companion, ported from the F1 Racing
 * Companion overlay into our styled panels. Three pages switched server-side by
 * the in-game UDP Action buttons (the snapshot carries the active page):
 *
 *   1 LIVE  — player tyre wear + laps-left, ERS state + deltas, standings ±5.
 *   2 PIT   — pit projection (rejoin slot/gaps/loss/penalties) + weather.
 *   3 PACE  — best sectors per compound + leader/ahead/you/behind pace table.
 *
 * A persistent ahead/behind header sits above every page (race only). All the
 * calculation lives server-side (see info.py); this block only paints state.info.
 */

import { registerBlock } from "../core/registry.js";
import { fmtClock } from "../core/format.js";

const SIZE = { w: 392, h: 480 };

const TEMPLATE = `<div id="info-panel"><div id="info-body"></div></div>`;

// Single-letter compound -> colour: S red, M yellow, H white, I green, W blue.
const TYRE_COLOUR = { S: "#e0454b", M: "#e6c93f", H: "#e8eaed",
                      I: "#3fbf57", W: "#3f7fe6", "?": "#8893a3" };
const ERS_CLASS = { None: "ers-none", Medium: "ers-medium",
                    Hotlap: "ers-hotlap", Overtake: "ers-overtake" };

// --- formatters -------------------------------------------------------------
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function fmtGap(gap) {
  if (!gap) return "";
  if (gap.lapDiff) return `${gap.lapDiff > 0 ? "+" : ""}${gap.lapDiff}L`;
  if (gap.seconds == null) return "";
  return `${gap.seconds >= 0 ? "+" : ""}${gap.seconds.toFixed(2)}s`;
}
// Sign class for a gap relative to the player: behind you (slower, +) vs ahead (−).
const gapClass = (gap) => (!gap ? "" : (gap.lapDiff || gap.seconds || 0) >= 0 ? "g-pos" : "g-neg");

function wearClass(pct) {
  if (pct == null) return "";
  if (pct >= 70) return "wear-hi";
  if (pct >= 45) return "wear-mid";
  return "wear-lo";
}
const tyrePill = (c) => `<span class="t-letter" style="--t:${TYRE_COLOUR[c] || TYRE_COLOUR["?"]}">${esc(c)}</span>`;
const signed = (v, unit = "", d = 1) => `${v >= 0 ? "+" : ""}${v.toFixed(d)}${unit}`;

function fmtLapDelta(ms) {
  if (ms == null) return "—";
  return `${ms >= 0 ? "+" : ""}${(ms / 1000).toFixed(2)}s`;
}

// --- header (ahead / behind) ------------------------------------------------
function renderHeader(header) {
  if (!header) return "";
  const row = (r, label) => {
    if (!r) return `<div class="hdr-row empty"><span class="hdr-tag">${label}</span><span class="dash">—</span></div>`;
    return `<div class="hdr-row">
      <span class="hdr-tag">${label}</span>
      <span class="hdr-gap ${gapClass(r.gap)}">${fmtGap(r.gap)}</span>
      ${tyrePill(r.tyre)}
      <span class="hdr-wear ${wearClass(r.wearPct)}">${r.wearPct}%</span>
      <span class="hdr-batt">${r.batteryPct}%</span>
      <span class="hdr-ers ${ERS_CLASS[r.ersMode] || "ers-none"}">${esc(r.ersMode)}</span>
      <span class="drs ${r.drs ? "on" : ""}">DRS</span>
    </div>`;
  };
  return `<div class="info-header">${row(header[0], "AHEAD")}${row(header[1], "BEHIND")}</div>`;
}

// --- page 1: live -----------------------------------------------------------
function renderLive(page) {
  if (page.grid) {
    return `<div class="grid-note">FORMATION / RACE START</div>` +
      `<div class="info-rows">${page.grid.map((c) => `
        <div class="grow ${c.isPlayer ? "me" : ""}">
          <span class="pos">${c.pos}</span><span class="name">${esc(c.name)}</span>
          <span class="cmp">${tyrePill(c.tyre)}</span>
        </div>`).join("")}</div>`;
  }
  let html = "";
  if (page.player) html += renderPlayerExtras(page.player);
  if (page.standings) html += renderStandings(page.standings);
  if (!html) html = `<div class="muted">Waiting for race data…</div>`;
  return html;
}

function renderPlayerExtras(p) {
  let out = `<div class="player-extras">`;
  if (p.tyre) {
    const t = p.tyre;
    let proj = "";
    if (t.degPerLap != null) {
      const laps = t.raceLaps != null ? `~${t.tyreLaps}/${t.raceLaps}L` : `~${t.tyreLaps}L`;
      proj = `<span class="deg">${signed(t.degPerLap, "%/lap")}</span><span class="laps-left">${laps}</span>`;
    }
    out += `<div class="pe-row"><span class="pe-key">TYRE</span>
      <span class="pe-big ${wearClass(t.wearPct)}">${t.wearPct}%</span>
      <span class="pe-sub">${esc(t.corner)}</span>${proj}</div>`;
  }
  if (p.ers) {
    const e = p.ers;
    let d = "";
    if (e.prevDelta != null) d += `<span class="ers-d">prev ${signed(e.prevDelta, "%")}</span>`;
    if (e.nowDelta != null) d += `<span class="ers-d">now ${signed(e.nowDelta, "%")}</span>`;
    out += `<div class="pe-row"><span class="pe-key">ERS</span>
      <span class="pe-big">${e.pct}%</span>
      <span class="hdr-ers ${ERS_CLASS[e.mode] || "ers-none"}">${esc(e.mode)}</span>${d}</div>`;
  }
  return out + `</div>`;
}

function renderStandings(rows) {
  const head = `<div class="srow shead">
    <span class="pos">P</span><span class="name">Driver</span>
    <span class="gap">Gap</span><span class="cmp">Cmp</span>
    <span class="wear">Wear</span><span class="fw">FW</span><span class="pen">Pen</span></div>`;
  const body = rows.map((r) => {
    const pen = [];
    if (r.pen.timeSec) pen.push(`+${r.pen.timeSec}s`);
    if (r.pen.dt) pen.push("DT");
    if (r.pen.sg) pen.push("SG");
    return `<div class="srow ${r.isPlayer ? "me" : ""}">
      <span class="pos">${r.pos}</span>
      <span class="name">${esc(r.name)}</span>
      <span class="gap ${gapClass(r.gap)}">${fmtGap(r.gap)}</span>
      <span class="cmp">${tyrePill(r.tyre)}</span>
      <span class="wear ${wearClass(r.wearPct)}">${r.wearPct == null ? "" : r.wearPct + "%"}</span>
      <span class="fw">${r.fw ? esc(r.fw) : ""}</span>
      <span class="pen">${esc(pen.join(" "))}</span>
    </div>`;
  }).join("");
  return `<div class="info-rows standings">${head}${body}</div>`;
}

// --- page 2: pit + weather --------------------------------------------------
function renderPit(page) {
  let html = "";
  if (page.pit) {
    const p = page.pit;
    const lost = p.positionsLost === 0 ? "±0" : signed(p.positionsLost, "", 0);
    html += `<div class="pit-box">
      <div class="pit-main"><span class="pit-key">PIT?</span>
        <span class="pit-pos">P${p.projectedPos}</span><span class="pit-lost">(${lost})</span></div>
      <div class="pit-gaps">
        <span class="pit-g"><b>ahead</b> <span class="${gapNeg(p.aheadGap)}">${fmtGap(p.aheadGap) || "—"}</span></span>
        <span class="pit-g"><b>behind</b> <span class="${gapNeg(p.behindGap)}">${fmtGap(p.behindGap) || "—"}</span></span>
        <span class="pit-g"><b>loss</b> ${p.pitLoss}s${p.scActive ? " (SC)" : ""}</span>
      </div>
      ${p.penOwed ? `<div class="pit-pen">Pen owed: ${penText(p.penOwed)} = +${p.penOwed.totalSec}s</div>` : ""}
    </div>`;
  } else if (page.note) {
    html += `<div class="muted">${esc(page.note)}</div>`;
  }
  html += renderWeather(page.weather);
  return html;
}
const gapNeg = (g) => (g && (g.lapDiff || g.seconds || 0) < 0 ? "g-neg" : "g-pos");
function penText(pen) {
  const parts = [];
  if (pen.dt) parts.push(`${pen.dt}×DT`);
  if (pen.sg) parts.push(`${pen.sg}×SG`);
  return parts.join(" ");
}

function renderWeather(samples) {
  if (!samples || !samples.length) return `<div class="muted">No weather data yet</div>`;
  const cells = samples.map((s) => `
    <div class="wx">
      <span class="wx-t">+${s.timeOffset}m</span>
      <span class="wx-w">${esc(s.weather)}</span>
      <span class="wx-r ${s.rainPct >= 40 ? "rainy" : ""}">${s.rainPct}%</span>
    </div>`).join("");
  return `<div class="weather-title">WEATHER</div><div class="weather-grid">${cells}</div>`;
}

// --- page 3: sectors + pace -------------------------------------------------
function renderPace(page) {
  let html = "";
  if (page.sectors) html += renderSectors(page.sectors);
  if (page.pace && page.pace.length) html += renderPaceTable(page.pace);
  if (!html) html = `<div class="muted">No race data yet</div>`;
  return html;
}

function renderSectors(rows) {
  const head = `<div class="sec-row sec-head"><span class="sec-cmp"></span>
    <span>S1</span><span>S2</span><span>S3</span></div>`;
  const body = rows.map((r) => {
    const cells = r.cells.map((c) => {
      if (!c) return `<span class="sec-cell dash">-</span>`;
      const val = c.isAbsolute ? c.value.toFixed(3) : signed(c.value, "", 3);
      return `<span class="sec-cell ${c.isAbsolute ? "best" : "delta"}">${val}${c.hot ? '<i class="hot">!</i>' : ""}</span>`;
    }).join("");
    const stars = "*".repeat(r.stars);
    return `<div class="sec-row"><span class="sec-cmp" style="--t:${TYRE_COLOUR[r.compound]}">${r.compound}<i>${stars}</i></span>${cells}</div>`;
  }).join("");
  return `<div class="sec-title">BEST SECTORS / COMPOUND</div><div class="sec-table">${head}${body}</div>`;
}

function renderPaceTable(rows) {
  const head = `<div class="pace-row pace-head"><span>Pos</span><span class="pl">Driver</span>
    <span>Cmp</span><span class="pw">Wear</span><span class="pa">AvgLap</span><span class="pd">ΔLap</span></div>`;
  const body = rows.map((r) => {
    const wear = r.wearPct == null ? "—"
      : `${r.wearPct}%${r.wearDelta != null ? `<i>(${signed(r.wearDelta, "%")})</i>` : ""}`;
    const avg = r.avgLapMs == null ? "—" : fmtClock(r.avgLapMs);
    return `<div class="pace-row ${r.isPlayer ? "me" : ""}">
      <span>P${r.pos}</span><span class="pl">${esc(r.name)}</span>
      ${tyrePill(r.compound)}<span class="pw ${wearClass(r.wearPct)}">${wear}</span>
      <span class="pa">${avg}</span><span class="pd">${fmtLapDelta(r.lapDeltaMs)}</span>
    </div>`;
  }).join("");
  return `<div class="pace-title">PACE &amp; WEAR</div><div class="pace-table">${head}${body}</div>`;
}

// --- entry ------------------------------------------------------------------
function render(state) {
  const body = document.getElementById("info-body");
  const info = state.info;
  if (!info || !info.available) {
    body.innerHTML = `<div class="muted">Waiting for telemetry…</div>`;
    return;
  }
  let html = "";
  if (info.banner) html += `<div class="banner">${esc(info.banner)}</div>`;
  html += renderHeader(info.header);

  const page = info.page || {};
  if (page.type === "live") html += renderLive(page);
  else if (page.type === "pit") html += renderPit(page);
  else if (page.type === "pace") html += renderPace(page);

  body.innerHTML = html;
}

registerBlock({ name: "info", template: TEMPLATE, size: SIZE, render });
