/* Block registry.
 *
 * Every overlay is a self-contained "block" module that registers itself here at
 * import time. A block is a plain object:
 *
 *   {
 *     name:     "standings",          // matches the URL path / HUD_VIEW
 *     template: "<div id=…>…</div>",  // markup injected into #stage on mount
 *     init?:    () => void,           // one-time setup after the template is in the DOM
 *     render:   (state) => void,      // called with each broadcast snapshot
 *   }
 *
 * main.js resolves the active view, looks the block up here, mounts it, and pipes
 * the WebSocket feed into its render(). Adding a new overlay therefore needs no
 * change to the core: write blocks/<name>.js, register it, import it from
 * blocks/index.js, and add the matching route in server.py.
 */

const blocks = new Map();

export function registerBlock(block) {
  if (!block || !block.name) throw new Error("registerBlock: block needs a name");
  blocks.set(block.name, block);
}

export function getBlock(name) {
  return blocks.get(name);
}

export function blockNames() {
  return [...blocks.keys()];
}
