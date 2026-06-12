/* ef-one-tv client entry point.
 *
 * Every overlay route (/standings, /quali_lap_sectors, …) serves the same shell
 * (overlay.html). This boot resolves which block the page is for, mounts that
 * block's markup into #stage, and pipes the WebSocket feed into its render().
 *
 * Adding an overlay never touches this file: write blocks/<name>.js, import it
 * from blocks/index.js, and add the route in server.py.
 */

import { mountStage } from "./core/stage.js";
import { startFeed } from "./core/feed.js";
import { getBlock, blockNames } from "./core/registry.js";
import "./blocks/index.js"; // side-effect: every block self-registers

// The view is the URL path (/standings -> "standings"). window.HUD_VIEW lets a
// static test harness force a view when there's no server route to read.
function resolveView() {
  if (window.HUD_VIEW) return window.HUD_VIEW;
  const seg = location.pathname.replace(/^\/+|\/+$/g, "");
  return seg || "standings";
}

const view = resolveView();
const block = getBlock(view);

if (!block) {
  console.error(`unknown overlay "${view}"; known: ${blockNames().join(", ")}`);
} else {
  // Mount: size the stage to the block's footprint, inject the block's template,
  // run its one-time setup, then start feeding it snapshots.
  document.getElementById("stage").insertAdjacentHTML("beforeend", block.template);
  block.init?.();
  mountStage(block.size || { w: 1920, h: 1080 });
  startFeed((state) => block.render(state));
}
