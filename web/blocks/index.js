/* Block manifest: importing this registers every overlay block. To add a new
 * block, create blocks/<name>.js (which calls registerBlock) and add one import
 * line below — then add a matching route in server.py. */

import "./standings.js";
import "./sectors.js";
import "./inputs.js";
