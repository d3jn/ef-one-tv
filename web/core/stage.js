/* Stage scaling: the overlay lives on a fixed design surface (#stage) sized to
 * the active block's footprint, so an OBS browser source can be made just big
 * enough for the block instead of a full 1920×1080. mountStage fixes the stage
 * to the block's design size and scales it to fit the current window (preserving
 * aspect), so any source resolution with that aspect renders the block crisp. */

export function mountStage(size) {
  const stage = document.getElementById("stage");
  stage.style.width = `${size.w}px`;
  stage.style.height = `${size.h}px`;

  const fit = () => {
    const scale = Math.min(window.innerWidth / size.w, window.innerHeight / size.h);
    stage.style.setProperty("--scale", scale);
  };
  window.addEventListener("resize", fit);
  fit();
}
