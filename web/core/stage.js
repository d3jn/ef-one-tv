/* Stage scaling: the whole overlay lives on a fixed 1920×1080 #stage so the
 * layout is resolution-independent and OBS-ready. fitStage scales that surface
 * to fit the current window. mountStage wires it to the resize event and runs it
 * once. */

export function fitStage() {
  const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
  document.getElementById("stage").style.setProperty("--scale", scale);
}

export function mountStage() {
  window.addEventListener("resize", fitStage);
  fitStage();
}
