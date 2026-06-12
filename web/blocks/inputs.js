/* Inputs block: a scrolling throttle/brake trace for the active driver.
 *
 * A 4:1 rectangle. "Now" is the right edge; the waveform scrolls left over a
 * ~5s window. Each input maps value -> height: 0% draws at the bottom edge,
 * 100% at the top, proportional in between. Throttle (green) and brake (red)
 * are overlaid as filled areas — a vibrant top line over a desaturated,
 * translucent fill so they read against gameplay capture.
 *
 * Rendering: Canvas 2D, full redraw from a fixed-step ring buffer each animation
 * frame. The data is tiny (one column per pixel of width), so a clean full
 * redraw is both the fastest-to-correct and the smoothest option — no blit/
 * smear tricks needed. The buffer advances on a fixed time accumulator so scroll
 * speed stays constant regardless of frame rate or the 20Hz snapshot jitter.
 */

import { registerBlock } from "../core/registry.js";

const TEMPLATE = `
  <!-- Throttle/brake trace for the active driver, with a status-pill row on top.
       The canvas backing store is sized to the device pixel ratio in init();
       CSS sets its display size. -->
  <div id="inputs-panel">
    <div class="inputs-pills">
      <span id="pill-bb" class="pill pill-bb">BB 0%</span>
      <span id="pill-diff" class="pill pill-diff">DIFF 0%</span>
      <span id="pill-ers" class="pill pill-ers ers-none">ERS NONE</span>
      <span id="pill-rpm" class="pill pill-rpm">RPM 0</span>
    </div>
    <canvas id="inputs-canvas"></canvas>
  </div>`;

// Design footprint (px) the OBS browser source should match: a 4:1 trace plus
// the pill row above it and the panel's own padding/frame.
const SIZE = { w: 480, h: 150 };

// ERS deploy mode (m_ersDeployMode) -> pill label/colour class (see CSS).
const ERS_MODES = ["none", "medium", "hotlap", "overtake"];

// Drawing surface inside the panel (the canvas), and how much wall-clock it
// spans left-to-right. One ring-buffer column per pixel of width.
const CW = 460;            // canvas CSS width  (4:1-ish inside the padded panel)
const CH = 100;            // canvas CSS height
const WINDOW_MS = 5000;    // visible time across the full width (~5s)
const STEP_MS = WINDOW_MS / CW;  // wall-clock each column represents (~10.9ms)
const DRAW_HZ = 30;        // redraw cap — smooth for a trace, half the paint cost
const DRAW_MS = 1000 / DRAW_HZ;

const THROTTLE = { line: "#27e06a", fill: "rgba(40, 150, 78, 0.30)" };
const BRAKE = { line: "#ff3b3b", fill: "rgba(165, 44, 44, 0.32)" };

// Ring buffer of {throttle, brake} columns, oldest at head, newest at the right.
const cols = new Array(CW).fill(null).map(() => ({ t: 0, b: 0 }));
let latest = { throttle: 0, brake: 0 };  // most recent snapshot value
let canvas, ctx;
let lastTs = 0;
let acc = 0;        // time accumulator for fixed-step buffer advance
let drawAcc = 0;    // time accumulator for the redraw cap

// Linear interpolation between successive snapshots: snapshots arrive ~5x slower
// than columns advance, so writing each snapshot value verbatim repeats ~5
// identical columns and stair-steps the trace. Instead we ramp the written value
// straight from the previous sample to the latest over the columns that span the
// interval — so the line angles from one point to the next. segLen (columns per
// interval) is re-estimated from the last interval, so this tracks any push_hz.
let curT = 0, curB = 0;        // value currently written into the buffer
let segFromT = 0, segFromB = 0; // sample value at the start of the current segment
let segCols = 0;               // columns pushed since the last snapshot
let segLen = 5;                // estimated columns per snapshot interval

function init() {
  canvas = document.getElementById("inputs-canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = `${CW}px`;
  canvas.style.height = `${CH}px`;
  canvas.width = Math.round(CW * dpr);
  canvas.height = Math.round(CH * dpr);
  ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);  // draw in CSS pixels; backing store is crisp at any DPR
  requestAnimationFrame(frame);
}

// Per-snapshot hook: stash the latest pedal values for the rAF draw loop (which
// owns the smooth scroll), and refresh the status pills (cheap, 4 text nodes).
function render(state) {
  const inp = state.inputs;
  // Begin a new interpolation segment from wherever the trace is now toward the
  // freshly-arrived sample. The previous segment's column count estimates this
  // one's length, so the line reaches each sample just as the next arrives.
  if (segCols > 0) segLen = segCols;
  segFromT = curT;
  segFromB = curB;
  segCols = 0;
  latest = inp || { throttle: 0, brake: 0 };
  updatePills(inp);
}

function updatePills(inp) {
  document.getElementById("pill-rpm").textContent =
    `RPM ${inp ? Math.round(inp.rpm || 0) : 0}`;

  const mode = ERS_MODES[inp ? inp.ersMode || 0 : 0] || "none";
  const ers = document.getElementById("pill-ers");
  ers.textContent = `ERS ${mode.toUpperCase()}`;
  ers.className = `pill pill-ers ers-${mode}`;  // swap the colour class

  document.getElementById("pill-bb").textContent =
    `BB ${inp ? Math.round(inp.brakeBias || 0) : 0}%`;
  document.getElementById("pill-diff").textContent =
    `DIFF ${inp ? Math.round(inp.diff || 0) : 0}%`;
}

function frame(ts) {
  if (!lastTs) lastTs = ts;
  const dt = ts - lastTs;
  lastTs = ts;
  acc += dt;
  drawAcc += dt;
  // Advance the buffer one column per elapsed STEP_MS. Cap the catch-up so a
  // long stall (e.g. a backgrounded tab) doesn't spin the loop.
  let steps = Math.min(Math.floor(acc / STEP_MS), CW);
  acc -= steps * STEP_MS;
  while (steps-- > 0) {
    // Advance along the straight line from the previous sample to the latest.
    segCols++;
    const f = Math.min(1, segCols / segLen);
    curT = segFromT + ((latest.throttle || 0) - segFromT) * f;
    curB = segFromB + ((latest.brake || 0) - segFromB) * f;
    cols.shift();
    cols.push({ t: curT, b: curB });
  }
  // Paint at DRAW_HZ, not the full rAF rate: the buffer scroll above is driven
  // by its own clock, so a lower paint rate costs CPU without changing motion.
  if (drawAcc >= DRAW_MS) {
    drawAcc %= DRAW_MS;
    draw();
  }
  requestAnimationFrame(frame);
}

// Filled area from the baseline up to each column's value, then a vibrant line
// along the top. value 0 -> baseline (CH), 1 -> top (0).
function trace(get, colour) {
  ctx.beginPath();
  ctx.moveTo(0, CH);
  for (let x = 0; x < CW; x++) ctx.lineTo(x, CH - get(cols[x]) * CH);
  ctx.lineTo(CW - 1, CH);
  ctx.closePath();
  ctx.fillStyle = colour.fill;
  ctx.fill();

  ctx.beginPath();
  for (let x = 0; x < CW; x++) {
    const y = CH - get(cols[x]) * CH;
    x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.strokeStyle = colour.line;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();
}

function draw() {
  ctx.clearRect(0, 0, CW, CH);
  trace((c) => c.t, THROTTLE);  // throttle underneath
  trace((c) => c.b, BRAKE);     // brake on top (translucent, so both stay visible)
}

registerBlock({ name: "inputs", template: TEMPLATE, size: SIZE, init, render });
