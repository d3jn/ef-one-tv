/* Standings block: the broadcast timing tower.
 *
 * Rows are keyed by carIndex and positioned absolutely; on each update we set
 * each row's translateY to its rank. The CSS `transition: transform` does the
 * rest, so cars swapping places slide past each other the way they do on a TV
 * feed. The right column shows a mode-dependent metric (gap / interval / tyre /
 * quali gap), switchable by keyboard and auto-rotatable from settings.json.
 */

import { registerBlock } from "../core/registry.js";
import { fmtCountdown } from "../core/format.js";

const ROW_H = 40;
let rowsEl = null;             // #tower-rows, resolved in init() once mounted
const rows = new Map();        // carIndex -> { el, refs }

const TEMPLATE = `
  <!-- Header sits directly above the tower, same width, small gap between. -->
  <div id="panel">
    <header id="topbar">
      <span class="f1-mark" id="brand-mark">F1</span>
      <div class="session-meta">
        <span id="track">—</span>
        <span class="sep">/</span>
        <span id="session-type">—</span>
      </div>
      <div id="session-info"></div>
      <div id="flag-block"></div>
    </header>

    <section id="tower">
      <ol id="tower-rows"></ol>
    </section>
  </div>`;

/* --- Right-column modes ---
 * The right column can show several metrics, but which ones are available
 * depends on the session: a race offers all of them, other sessions just "gap".
 *
 * To change what a session type offers, edit MODE_POOLS: each key is a session
 * kind (as sent in session.infoKind) mapping to the ordered list of modes for
 * that kind; anything not listed falls back to DEFAULT_POOL. Add a new mode by
 * (1) listing it here and (2) handling it in renderRightColumn. Switch between
 * the active pool's modes with number keys 1/2/3…, or cycle with "m". */
const MODE_POOLS = {
  race: ["gap", "interval", "tyre"],
  quali: ["gap_quali"], // qualifying gap, with "No time" / "Out lap" labels
};
const DEFAULT_POOL = ["gap"]; // practice, time trial, unknown, …
// Modes the client knows how to render (see renderRightColumn). Pools coming
// from settings.json are filtered to these, so a typo there can't break the UI.
const KNOWN_MODES = ["gap", "interval", "tyre", "gap_quali"];

/* --- Auto mode rotation ---
 * Refreshed from each snapshot's session.modeRotation (configured in
 * settings.json). `pools` overrides the built-in MODE_POOLS per session kind
 * (and sets the manual-cycle order); `durations` maps a mode to its on-screen
 * seconds; `enabled` toggles only the auto-advance — manual switching always
 * uses the pool. Backend kind "none" maps to the config's "other". */
const ROTATION_DEFAULT_SECONDS = 5;
let rotation = { enabled: false, pools: {}, durations: {} };

// The active pool for a session kind: a configured (and known) pool wins,
// otherwise the built-in fallback.
function poolFor(kind) {
  const key = kind === "none" ? "other" : kind;
  const configured = (rotation.pools[key] || []).filter((m) => KNOWN_MODES.includes(m));
  if (configured.length) return configured;
  return MODE_POOLS[kind] || DEFAULT_POOL;
}

const samePool = (a, b) => a.length === b.length && a.every((m, i) => m === b[i]);

/* --- Row ordering ---
 * By default rows follow the order the server sends (sorted by game position).
 * A mode can override that by registering an orderer here: it receives the cars
 * array and returns a new array in display order, optionally stamping each car
 * with a `displayPos` (the number shown in the position cell). This is how a
 * mode can sort the table differently from the live race order. */
const MODE_ORDER = {
  gap_quali: orderQuali,
};

const orderFor = (mode) => MODE_ORDER[mode];

// Qualifying order, immune to the game shuffling timeless drivers around:
//   0) drivers with a valid lap time — fastest first (a driver who set a time
//      then retired keeps their place here; the time still counts)
//   1) the rest, on an out lap — alphabetical
//   2) the rest, idle with no time — alphabetical
//   3) drivers who retired without ever setting a time — very bottom, alphabetical
// Positions are renumbered 1..N so the shown number is stable too. carIndex is
// the final tiebreaker, so equal keys never swap frame to frame.
function orderQuali(cars) {
  const tier = (c) =>
    c.bestLapMs > 0 ? 0 : c.retired ? 3 : c.onOutLap ? 1 : 2;
  const ordered = cars.slice().sort((a, b) => {
    const ta = tier(a), tb = tier(b);
    if (ta !== tb) return ta - tb;
    if (ta === 0 && a.bestLapMs !== b.bestLapMs) return a.bestLapMs - b.bestLapMs;
    if (ta !== 0) {
      const byName = a.code.localeCompare(b.code);
      if (byName) return byName;
    }
    return a.carIndex - b.carIndex;
  });
  ordered.forEach((c, i) => { c.displayPos = i + 1; });
  return ordered;
}

let modes = DEFAULT_POOL; // active pool; re-selected from the session each update
let modeIndex = 0;        // index into `modes`
let lastState = null;     // re-rendered on mode change so new values appear at once
let switching = false;

const norm = (i) => ((i % modes.length) + modes.length) % modes.length;

// Re-select the active pool when the session kind (or its configured pool)
// changes, keeping the current mode if it's still offered, otherwise falling
// back to the pool's first mode. Compared by value (the configured pool is a
// fresh array each call) and returns whether the pool actually changed.
function setModePool(kind) {
  const next = poolFor(kind);
  if (samePool(next, modes)) return false; // same pool — nothing to do
  const current = modes[modeIndex];
  modes = next;
  const keep = modes.indexOf(current);
  modeIndex = keep >= 0 ? keep : 0;
  return true;
}

// --- Rotation timer ---------------------------------------------------------
// A single self-rescheduling timeout walks the pool. We track the shown index
// explicitly (rather than reading modeIndex, which switchMode updates a frame
// late) so each mode gets its own duration even when they differ.
let rotationTimer = null;
let rotationEnabled = false; // last applied state, to detect on/off transitions

const durationMs = (mode) => {
  const s = rotation.durations[mode];
  return (typeof s === "number" && s > 0 ? s : ROTATION_DEFAULT_SECONDS) * 1000;
};

function stopRotation() {
  if (rotationTimer !== null) { clearTimeout(rotationTimer); rotationTimer = null; }
}

// (Re)start rotation, dwelling on `fromIdx` first. No-op (just stops) when
// disabled or the pool has fewer than two modes.
function restartRotation(fromIdx = modeIndex) {
  stopRotation();
  if (!rotation.enabled || modes.length < 2) return;
  const tick = (idx) => {
    rotationTimer = setTimeout(() => {
      const next = norm(idx + 1);
      switchMode(next);
      tick(next);
    }, durationMs(modes[idx]));
  };
  tick(norm(fromIdx));
}

// Refresh the rotation config from a snapshot. Called BEFORE setModePool, since
// pool selection reads rotation.pools.
function updateRotationConfig(modeRotation) {
  rotation = {
    enabled: !!(modeRotation && modeRotation.enabled),
    pools: (modeRotation && modeRotation.pools) || {},
    durations: (modeRotation && modeRotation.durations) || {},
  };
}

// (Re)start the timer only when the pool or the enabled flag actually changed —
// not every frame, or it would never get to advance.
function applyRotationTimer(poolChanged) {
  if (poolChanged || rotation.enabled !== rotationEnabled) restartRotation();
  rotationEnabled = rotation.enabled;
}

// Instantly apply a mode (no animation) — used at startup.
function applyMode(i) {
  modeIndex = norm(i);
  if (lastState) render(lastState);
}

// Animated mode switch: slide the current values out (right + fade), swap to the
// new mode, then slide the new values in from the left (+ fade).
function switchMode(i) {
  const next = norm(i);
  if (switching || next === modeIndex) return;
  switching = true;

  const OUT = 150, IN = 230, SHIFT = 20;
  // Only the metric animates; the tyre letter stays anchored on the right.
  const vals = () => rowsEl.querySelectorAll(".val-main");

  vals().forEach((v) =>
    v.animate(
      [{ transform: "translateX(0)", opacity: 1 },
       { transform: `translateX(${SHIFT}px)`, opacity: 0 }],
      { duration: OUT, easing: "ease-in", fill: "forwards" }
    )
  );

  setTimeout(() => {
    applyMode(next); // swap content while values are hidden
    vals().forEach((v) => {
      v.getAnimations().forEach((a) => a.cancel()); // clear the out-state fill
      v.animate(
        [{ transform: `translateX(-${SHIFT}px)`, opacity: 0 },
         { transform: "translateX(0)", opacity: 1 }],
        { duration: IN, easing: "ease-out" }
      );
    });
    switching = false;
  }, OUT);
}

// A manual mode pick switches and then resets the rotation dwell, so the chosen
// mode gets its full duration before auto-advance resumes.
function manualSwitch(i) {
  const target = norm(i);
  switchMode(target);
  restartRotation(target);
}

// Optional deep-link: open #gap / #interval / #tyre to start in that mode (only
// if that mode is in the current pool).
function modeFromHash() {
  const i = modes.indexOf(location.hash.slice(1).toLowerCase());
  return i >= 0 ? i : 0;
}

/* --- Row construction --- */
function createRow(car) {
  const el = document.createElement("li");
  el.className = "row entering";
  el.innerHTML = `
    <div class="pos"></div>
    <div class="driver">
      <img class="logo" alt="" />
      <span class="code"></span>
      <span class="drs">DRS</span>
    </div>
    <div class="gap">
      <span class="val">
        <span class="val-main"></span>
        <span class="tyre-letter"></span>
      </span>
    </div>
    <div class="penalty"></div>`;
  const refs = {
    pos: el.querySelector(".pos"),
    logo: el.querySelector(".logo"),
    code: el.querySelector(".code"),
    drs: el.querySelector(".drs"),
    gap: el.querySelector(".gap"),
    valMain: el.querySelector(".val-main"),   // metric; slides/fades on mode switch
    tyreLetter: el.querySelector(".tyre-letter"), // compound; persists across modes
    penalty: el.querySelector(".penalty"),    // unserved-penalty tab sticking out right
  };
  rowsEl.appendChild(el);
  // Drop the entering state next frame so the fade-in transition runs.
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("entering")));
  return { el, refs };
}

function updateRow(row, car, rank, total) {
  const { el, refs } = row;
  el.style.transform = `translateY(${rank * ROW_H}px)`;
  el.classList.toggle("player", car.isPlayer);
  el.classList.toggle("retired", car.retired);
  // Holder of the race's fastest lap (race sessions only; backend sends false
  // otherwise). Purple, the way broadcast graphics mark it.
  el.classList.toggle("fastest", !!car.fastestLap);
  el.classList.toggle("top", rank === 0);
  el.classList.toggle("bottom", rank === total - 1);
  el.style.setProperty("--team", car.teamColour);

  // A mode-supplied order may renumber positions (quali); else use the game's.
  refs.pos.textContent = car.displayPos ?? car.position;
  refs.code.textContent = car.code;

  // Team logo (hidden if we have no file for this team).
  const logoSrc = car.teamLogo ? `teams/${car.teamLogo}` : "";
  if (refs.logo.getAttribute("src") !== logoSrc) refs.logo.setAttribute("src", logoSrc);
  refs.logo.classList.toggle("hidden", !car.teamLogo);

  // DRS is a race concept; hide the badge entirely in qualifying.
  const quali = modes[modeIndex] === "gap_quali";
  refs.drs.classList.toggle("hidden", quali);
  refs.drs.classList.toggle("on", !quali && car.drs);

  renderRightColumn(refs, car, rank);
  renderStatusBlock(refs, car);
}

// The black tab that pokes out past the row's right edge. One shared, animated
// container shows either the finish flag (when a driver has finished — this
// takes priority) or unserved penalties ("+3", DT). Empty otherwise, so it
// slides away. Suppressed for out-of-race drivers.
function renderStatusBlock(refs, car) {
  const el = refs.penalty;
  let html = "";
  if (car.finished) {
    // Finish flag wins even if penalties remain unresolved.
    html = `<img class="finish-flag" src="other/finish.png" alt="finished" />`;
  } else if (!car.retired) {
    if (car.penaltySec > 0) html += `<span class="pen-time">+${car.penaltySec}</span>`;
    if (car.driveThrough) html += `<span class="pen-tag">DT</span>`;
  }
  const want = html !== "";
  // Only rewrite when the content actually changes: keeps the last content
  // during the slide-out, and avoids re-fetching the flag <img> every frame.
  if (want && el.dataset.html !== html) {
    el.innerHTML = html;
    el.dataset.html = html;
  }
  el.classList.toggle("show", want);
}

// The right-most column: a metric (mode-dependent) followed by the tyre
// compound letter, which is colour-coded, bold, and shown in every mode.
// Out-of-race (DNF/DSQ/DNS/NC) and PIT states replace BOTH the metric and tyre.
function renderRightColumn(refs, car, rank) {
  const mode = modes[modeIndex];
  const main = refs.valMain;

  // Out of the race: a single status label, no metric, no tyre.
  if (car.statusLabel) {
    main.innerHTML = `<span class="status-out">${car.statusLabel}</span>`;
    refs.tyreLetter.textContent = "";
    refs.gap.classList.remove("leader");
    return;
  }

  // Pitting: PIT replaces both the metric and the tyre.
  if (car.pitting) {
    main.innerHTML = `<span class="pit">PIT</span>`;
    refs.tyreLetter.textContent = "";
    refs.gap.classList.remove("leader");
    return;
  }

  // Racing normally: tyre letter + the mode's metric.
  refs.tyreLetter.textContent = car.tyre;
  refs.tyreLetter.style.color = car.tyreColour;

  if (mode === "tyre") {
    // Stint age as a lap count, with correct plurality (0 laps, 1 lap, 2 laps…).
    const age = car.tyreAge;
    main.textContent = `${age} ${age === 1 ? "lap" : "laps"}`;
    refs.gap.classList.remove("leader");
  } else if (mode === "gap_quali") {
    // Qualifying: the tyre shown is the compound the fastest lap was set on (from
    // session history), falling back to the live tyre until history arrives.
    if (car.bestTyre) {
      refs.tyreLetter.textContent = car.bestTyre;
      refs.tyreLetter.style.color = car.bestTyreColour;
    }
    // Qualifying gap. "Out lap" takes priority (a driver leaving the pits has no
    // representative gap yet); then "No time" before a first lap is set.
    refs.gap.classList.remove("leader");
    if (car.onOutLap) {
      main.innerHTML = `<span class="val-note">Out lap</span>`;
    } else if (car.noTime) {
      main.innerHTML = `<span class="val-note">No time</span>`;
    } else if (rank === 0) {
      // Leader: show their actual fastest-lap time, not a gap.
      main.textContent = car.bestLap || "—";
      refs.gap.classList.add("leader");
    } else {
      main.textContent = car.gapToLeader || "—";
    }
  } else if (rank === 0) {
    main.textContent = mode === "gap" ? "Gap" : "Interval";
    refs.gap.classList.add("leader");
  } else {
    main.textContent = (mode === "gap" ? car.gapToLeader : car.interval) || "—";
    refs.gap.classList.remove("leader");
  }
}

// Header: track / session type, plus session info — a lap counter in races,
// a mm:ss countdown in qualifying, hidden for other session types.
function renderHeader(s) {
  if (s.brandMark != null) document.getElementById("brand-mark").textContent = s.brandMark;
  document.getElementById("track").textContent = s.track;
  document.getElementById("session-type").textContent = s.type;
  renderFlag(s.flag);

  const info = document.getElementById("session-info");
  if (s.infoKind === "race") {
    info.innerHTML = `LAP <span class="n">${s.currentLap}</span>/<span class="n">${s.totalLaps}</span>`;
    info.classList.remove("hidden");
  } else if (s.infoKind === "quali") {
    info.textContent = fmtCountdown(s.timeLeft);
    info.classList.remove("hidden");
  } else {
    info.classList.add("hidden");
  }
}

// Session-flag tab. `flag` is {text, kind:"yellow"|"green"} or null. While
// hidden we keep the last text/colour so it slides out with its content intact.
function renderFlag(flag) {
  const el = document.getElementById("flag-block");
  if (flag) {
    if (el.textContent !== flag.text) el.textContent = flag.text;
    el.classList.toggle("green", flag.kind === "green");
    el.classList.toggle("yellow", flag.kind !== "green");
    el.classList.add("show");
  } else {
    el.classList.remove("show");
  }
}

function render(state) {
  lastState = state;

  // Refresh rotation config first (pool selection reads it), then pick the mode
  // pool for this session before rendering rows, so the right column only offers
  // modes valid for the session type.
  updateRotationConfig(state.session.modeRotation);
  const poolChanged = setModePool(state.session.infoKind);
  applyRotationTimer(poolChanged);
  renderHeader(state.session);

  // Some modes (e.g. quali) impose their own stable row order; otherwise we
  // keep the server's position sort.
  const orderer = orderFor(modes[modeIndex]);
  const cars = orderer ? orderer(state.cars) : state.cars;

  const seen = new Set();
  cars.forEach((car, rank) => {
    seen.add(car.carIndex);
    let row = rows.get(car.carIndex);
    if (!row) {
      row = createRow(car);
      rows.set(car.carIndex, row);
    }
    updateRow(row, car, rank, cars.length);
  });

  // Remove rows for cars no longer in the field.
  for (const [idx, row] of rows) {
    if (!seen.has(idx)) {
      row.el.classList.add("entering"); // reuse fade for exit
      setTimeout(() => row.el.remove(), 350);
      rows.delete(idx);
    }
  }

  // Size the tower body to the number of rows.
  rowsEl.style.height = `${state.cars.length * ROW_H}px`;
}

function init() {
  rowsEl = document.getElementById("tower-rows");

  // Number keys 1–9 select strictly by position in the active pool (1 = first
  // mode, 2 = second, …) — whatever modes the config put there, in that order.
  // Nothing here is tied to a specific mode, so growing the pool needs no change
  // (key 4 hits the 4th mode, etc.). Keys past the pool's length do nothing.
  // "m" cycles forward through the pool.
  window.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() === "m") { manualSwitch(modeIndex + 1); return; }
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= Math.min(modes.length, 9)) {
      manualSwitch(n - 1);
    }
  });
  window.addEventListener("hashchange", () => manualSwitch(modeFromHash()));
  applyMode(modeFromHash());
}

registerBlock({ name: "standings", template: TEMPLATE, init, render });
