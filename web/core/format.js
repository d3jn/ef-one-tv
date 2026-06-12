/* Shared time/score formatters used across blocks. Pure functions — no DOM. */

// Seconds → "M:SS" (minutes unpadded, seconds zero-padded): 923 → "15:23".
export function fmtCountdown(sec) {
  sec = Math.max(0, sec | 0);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

// Lap clock as M:SS.mmm — minutes only once past 60s (a lap is normally ~90s),
// full millisecond precision either way (e.g. 46.300, 1:23.456). Used for the
// reference split and the crossing delta.
export function fmtClock(ms) {
  ms = Math.max(0, ms | 0);
  const mins = Math.floor(ms / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  const frac = String(ms % 1000).padStart(3, "0");
  return mins > 0 ? `${mins}:${String(secs).padStart(2, "0")}.${frac}` : `${secs}.${frac}`;
}

// Same format but to a tenth of a second (e.g. 43.2, 1:23.4) — for the running
// live clock, where millisecond digits churn too fast to read.
export function fmtClockTenths(ms) {
  ms = Math.max(0, ms | 0);
  const mins = Math.floor(ms / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  const tenth = Math.floor((ms % 1000) / 100);
  return mins > 0 ? `${mins}:${String(secs).padStart(2, "0")}.${tenth}` : `${secs}.${tenth}`;
}

// A signed gap like "-0.250" / "+1.500"; magnitude reuses the lap-clock format.
export function fmtDelta(ms) {
  return (ms < 0 ? "-" : "+") + fmtClock(Math.abs(ms));
}
