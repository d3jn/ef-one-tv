/* Shared time formatters used across blocks. Pure functions — no DOM. */

// Lap clock as M:SS.mmm — minutes only once past 60s (a lap is normally ~90s),
// full millisecond precision either way (e.g. 46.300, 1:23.456).
export function fmtClock(ms) {
  ms = Math.max(0, ms | 0);
  const mins = Math.floor(ms / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  const frac = String(ms % 1000).padStart(3, "0");
  return mins > 0 ? `${mins}:${String(secs).padStart(2, "0")}.${frac}` : `${secs}.${frac}`;
}
