/* Quali lap-sectors block: the live sector-timing panel for the active driver.
 *
 * Heading: position / team logo / surname / fitted tyre. Main: the running lap
 * clock (live, to a tenth) on the left and the fastest lap's split to the
 * current sector boundary on the right. Bottom: three sector segments coloured
 * gray / yellow / green / purple. On each sector crossing the clock briefly
 * freezes and shows the coloured delta versus the fastest lap. All show/hide and
 * colouring is decided server-side; this block just paints `state.sectorPanel`
 * (null hides it).
 */

import { registerBlock } from "../core/registry.js";
import { fmtClock, fmtClockTenths, fmtDelta } from "../core/format.js";

const TEMPLATE = `
  <!-- Live sector-timing block for the active driver (qualifying only). -->
  <div id="sector-panel" class="hidden">
    <div class="sec-head">
      <span id="sec-pos" class="sec-pos"></span>
      <img id="sec-logo" class="sec-logo" alt="" />
      <span id="sec-name" class="sec-name"></span>
      <span id="sec-tyre" class="sec-tyre"></span>
    </div>
    <div class="sec-main">
      <span class="sec-clock">
        <span id="sec-time" class="sec-time"></span>
        <span class="sec-delta">
          <span id="sec-delta-time"></span>
          <span id="sec-delta-gap"></span>
        </span>
      </span>
      <span id="sec-ref" class="sec-ref">
        <span id="sec-ref-time" class="sec-ref-time"></span>
        <span id="sec-ref-name" class="sec-ref-name"></span>
      </span>
    </div>
    <div class="sec-bottom">
      <span class="seg gray">S1</span>
      <span class="seg gray">S2</span>
      <span class="seg gray">S3</span>
    </div>
  </div>`;

// On each crossing, instantly replace the running clock with the frozen time at
// that crossing (live-clock font) plus the coloured delta — e.g. "1:23.4 +0.230"
// — for 2s, then instantly restore the live clock (which kept ticking, hidden,
// underneath). No transitions. Each delta key flashes once.
let lastDeltaKey = null;
let deltaTimer = null;
function maybeFlashDelta(delta) {
  if (!delta || delta.key === lastDeltaKey) return;
  lastDeltaKey = delta.key;
  const clock = document.querySelector(".sec-clock");
  const faster = delta.ms < 0;
  document.getElementById("sec-delta-time").textContent = fmtClockTenths(delta.playerMs);
  const gap = document.getElementById("sec-delta-gap");
  gap.textContent = fmtDelta(delta.ms);
  gap.classList.toggle("faster", faster);
  gap.classList.toggle("slower", !faster);
  clock.classList.add("flashing");
  clearTimeout(deltaTimer);
  deltaTimer = setTimeout(() => clock.classList.remove("flashing"), 2000);
}

// Paint the panel from the snapshot's sectorPanel (quali only; null hides it).
function renderSectorPanel(panel) {
  const el = document.getElementById("sector-panel");
  if (!panel) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.style.setProperty("--team", panel.teamColour || "#888");

  document.getElementById("sec-pos").textContent = panel.position;
  const logo = document.getElementById("sec-logo");
  const src = panel.teamLogo ? `teams/${panel.teamLogo}` : "";
  if (logo.getAttribute("src") !== src) logo.setAttribute("src", src);
  logo.classList.toggle("hidden", !panel.teamLogo);
  document.getElementById("sec-name").textContent = panel.code;
  const tyre = document.getElementById("sec-tyre");
  tyre.textContent = panel.tyre || "";
  tyre.style.color = panel.tyreColour || "";

  document.getElementById("sec-time").textContent = fmtClockTenths(panel.currentLapMs);
  maybeFlashDelta(panel.delta);
  const ref = document.getElementById("sec-ref");
  if (panel.reference) {
    document.getElementById("sec-ref-time").textContent = fmtClock(panel.reference.timeMs);
    document.getElementById("sec-ref-name").textContent = panel.reference.name;
    ref.classList.remove("hidden");
  } else {
    ref.classList.add("hidden");
  }

  // Sector segments: gray / yellow / green / purple (see _sector_color server-side).
  const segs = el.querySelectorAll(".seg");
  panel.sectors.forEach((c, i) => { if (segs[i]) segs[i].className = `seg ${c}`; });
}

function render(state) {
  renderSectorPanel(state.sectorPanel);
}

// Design footprint (px) the OBS browser source should match: the 360-wide panel
// (~117 tall) plus a little allowance.
const SIZE = { w: 376, h: 132 };

registerBlock({ name: "quali_lap_sectors", template: TEMPLATE, size: SIZE, render });
