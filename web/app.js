/* ef-one-tv client.
 *
 * Connects to the server WebSocket, then renders the timing tower. Rows are
 * keyed by carIndex and positioned absolutely; on each update we set each row's
 * translateY to its rank. The CSS `transition: transform` does the rest, so
 * cars swapping places slide past each other the way they do on a TV feed.
 */

const ROW_H = 40;
const rowsEl = document.getElementById("tower-rows");
const rows = new Map(); // carIndex -> { el, refs }

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

const poolFor = (kind) => MODE_POOLS[kind] || DEFAULT_POOL;

let modes = DEFAULT_POOL; // active pool; re-selected from the session each update
let modeIndex = 0;        // index into `modes`
let lastState = null;     // re-rendered on mode change so new values appear at once
let switching = false;

const norm = (i) => ((i % modes.length) + modes.length) % modes.length;

// Re-select the active pool when the session kind changes, keeping the current
// mode if it's still offered, otherwise falling back to the pool's first mode.
function setModePool(kind) {
  const next = poolFor(kind);
  if (next === modes) return; // same pool — nothing to do
  const current = modes[modeIndex];
  modes = next;
  const keep = modes.indexOf(current);
  modeIndex = keep >= 0 ? keep : 0;
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

window.addEventListener("keydown", (e) => {
  // Number keys pick a mode within the current pool; out-of-range keys are
  // ignored (e.g. "2"/"3" do nothing in a single-mode quali).
  if (e.key >= "1" && e.key <= String(modes.length)) switchMode(Number(e.key) - 1);
  else if (e.key.toLowerCase() === "m") switchMode(modeIndex + 1);
});

// Optional deep-link: open #gap / #interval / #tyre to start in that mode (only
// if that mode is in the current pool).
function modeFromHash() {
  const i = modes.indexOf(location.hash.slice(1).toLowerCase());
  return i >= 0 ? i : 0;
}
window.addEventListener("hashchange", () => switchMode(modeFromHash()));
applyMode(modeFromHash());

/* --- Stage scaling: fit the fixed 1920×1080 surface to the window --- */
function fitStage() {
  const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
  document.getElementById("stage").style.setProperty("--scale", scale);
}
window.addEventListener("resize", fitStage);
fitStage();

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
  el.classList.toggle("top", rank === 0);
  el.classList.toggle("bottom", rank === total - 1);
  el.style.setProperty("--team", car.teamColour);

  refs.pos.textContent = car.position;
  refs.code.textContent = car.code;

  // Team logo (hidden if we have no file for this team).
  const logoSrc = car.teamLogo ? `teams/${car.teamLogo}` : "";
  if (refs.logo.getAttribute("src") !== logoSrc) refs.logo.setAttribute("src", logoSrc);
  refs.logo.classList.toggle("hidden", !car.teamLogo);

  refs.drs.classList.toggle("on", car.drs);

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
      main.textContent = "Gap";
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

// Seconds → "M:SS" (minutes unpadded, seconds zero-padded): 923 → "15:23".
function fmtCountdown(sec) {
  sec = Math.max(0, sec | 0);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function render(state) {
  lastState = state;
  // Pick the mode pool for this session before rendering rows, so the right
  // column only offers modes valid for the session type.
  setModePool(state.session.infoKind);
  renderHeader(state.session);

  const seen = new Set();
  state.cars.forEach((car, rank) => {
    seen.add(car.carIndex);
    let row = rows.get(car.carIndex);
    if (!row) {
      row = createRow(car);
      rows.set(car.carIndex, row);
    }
    updateRow(row, car, rank, state.cars.length);
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

/* --- WebSocket with auto-reconnect --- */
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onmessage = (ev) => {
    try {
      render(JSON.parse(ev.data));
    } catch (e) {
      console.error("bad payload", e);
    }
  };
  ws.onclose = () => setTimeout(connect, 1000); // retry until the server is back
  ws.onerror = () => ws.close();
}
connect();
