# ef-one-tv

Reads **F1 25 (2026 Season Pack)** UDP telemetry in the **2026** format and
renders a driver-info overlay in the browser.
A Python server listens for the game's telemetry packets, folds them into live
session state, and pushes it to a web page over a WebSocket. The overlay is
plain HTML/CSS/JS.

```
F1 game ─────UDP :20777──> server.py ──WebSocket──> browser :5000
                           parse + merge            HTML/CSS/JS overlay
```

The 2026-format packets are parsed from scratch in `f1_packets.py` using only Python's
`struct` module against the official spec in `specs/` — **no third-party
telemetry library**. Only the 2026 UDP format and 2026-regulation cars are
supported; packets in any other format (e.g. 2025) are ignored with a warning.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Then open <http://localhost:5000>.

### Try it without the game (Linux/Mac/any machine)

In a second terminal, send synthetic-but-real 2026-format packets:

```bash
python mock_sender.py
```

Open <http://localhost:5000/info>: a 22-car 2026 field swaps positions, pits,
carries penalties, wears its tyres and toggles Overtake Mode, and the overlay
cycles its three pages every ~8s.

### Record a real session and replay it

To debug against real telemetry without the game running, capture a session once
and replay it as many times as you like.

```bash
# 1. Capture: point the game at this machine and drive. Writes a new file under
#    recordings/ named <YYYY-MM-DD>-<n>.f1rec (n auto-increments per day).
python recorder.py            # Ctrl+C to stop

# 2. Replay any capture into the server, looping forever, with the original
#    packet timing preserved. Ignores --mode and the synthetic data.
python mock_sender.py --from-file recordings/2026-06-13-1.f1rec
```

A `.f1rec` file is just length-prefixed, timestamped raw datagrams, so replay is
byte-for-byte what the game sent — ideal for reproducing session-specific issues.
Captures made with the game on UDP Format 2025 won't replay: the server only
accepts 2026-format packets.

### With the real game

In F1 25 with the 2026 Season Pack: **Settings → Telemetry Settings**
- UDP Telemetry: **On**
- UDP Broadcast Mode: Off (or On if the game is on another PC)
- IP Address: `127.0.0.1` (same machine) or this machine's LAN IP
- Port: `20777`
- UDP Send Rate: 20–60 Hz
- UDP Format: **2026**

## Overlay (OBS browser source)

The overlay is served on its own route and sized to just its own block, so you
can add it to **OBS** as a Browser Source without a full-screen canvas. Set the
source to the size below (or any size with the same aspect — the block scales to
fit); the background is transparent and the block is pinned top-left.

| Route | Browser source size | Shows |
|-------|--------------------|-------|
| `/info` | **600 × 640** | The driver-info companion: 3 race pages (live / pit & weather / pace), switched in-game via UDP Actions. See [Info overlay](#info-overlay-driver-companion). |

<http://localhost:5000> is a landing page linking to it.

### Info overlay (driver companion)

`/info` is a compact in-race companion (ported from the F1 Racing Companion
overlay) with three pages, plus a persistent ahead/behind header (gap, tyre,
wear, battery, ERS deploy mode, and an **OVR** pill lit while that car has
Overtake Mode engaged) on every page. It's race-focused — most of it stays
hidden in practice/qualifying.

- **Page 1 — Live:** your tyre wear with a laps-left projection (extrapolated to
  the 80% wear cap from your last clean lap, shown against the race laps left),
  ERS state with per-lap / since-SF battery deltas, and standings ±5 around you
  with gaps relative to your car, tyre/wear, front-wing damage and penalties.
- **Page 2 — Pit & weather:** a pit projection (where you'd rejoin, gaps to the
  cars ahead/behind after the stop, expected pit loss for green vs SC, and any
  penalties owed) plus the weather forecast.
- **Page 3 — Pace:** fastest S1/S2/S3 per tyre compound across all cars' last 3
  laps (with `*` stars for the compound winning each sector and a hot `!` marker
  for a still-standing time), and a leader/ahead/you/behind pace-and-wear table
  (avg lap + falloff vs the stint's frozen optimal-pace baseline).

**Switching pages:** bind **UDP Action 1** (next) and **UDP Action 2** (previous)
in the game's controls — they aren't bound by default. The active page is tracked
server-side, so every viewer of `/info` shows the same page.

Per-track pit losses for the projection default to built-in estimates; override
them in `settings.json` under `pit_time_lost` (keyed by track slug), e.g.
`{"monza": {"green": 25, "sc": 17}}`.

## Settings

Ports and rates live in **`settings.json`** next to the code (loaded by
`config.py`). Edit it and restart `python server.py`:

```json
{
  "udp_port": 20777,
  "http_host": "127.0.0.1",
  "http_port": 5000,
  "push_hz": 20
}
```

| Key | Meaning | Default |
|-----|---------|---------|
| `udp_port` | Telemetry UDP port to listen on (must match the game) | `20777` |
| `http_host` | interface the page binds to (`0.0.0.0` to expose on the LAN) | `127.0.0.1` |
| `http_port` | port the graphics page is served on | `5000` |
| `push_hz` | snapshots/sec pushed to the browser | `20` |

Every key is optional — missing keys (or a missing file) fall back to these
defaults. `server.py` and `mock_sender.py` both read `udp_port` from here, so
they always agree.

## Build a standalone executable

The app resolves `web/`, `settings.json` and `driver_names.json` **next to the
executable** when frozen (it checks `sys.frozen`; see `config.app_dir` /
`config.resource_path`), so a PyInstaller build won't go hunting in the temp
extraction dir.

```bash
pip install pyinstaller

# One-file build. --collect-all pulls in modules uvicorn loads dynamically that
# PyInstaller's static analysis otherwise misses — including the websockets
# implementation behind the /ws endpoint.
pyinstaller --onefile --name server \
  --collect-all uvicorn --collect-all websockets \
  server.py
```

Then ship the data files **next to the produced executable**:

```
server(.exe)
web/                 ← the overlay (HTML/CSS/JS)
settings.json        ← optional; defaults apply if absent
driver_names.json    ← optional; no overrides if absent
```

`settings.json` and `driver_names.json` are always read from beside the
executable, so they stay editable after building. `web/` can either sit beside
the executable (above) or be embedded in a one-file build with
`--add-data "web:web"` (`web;web` on Windows) — `resource_path` prefers a loose
copy next to the exe and falls back to the embedded one.

## Files

| File | Role |
|------|------|
| `f1_packets.py` | Packet parsers + reference data (session types, weather). Pure `struct`. |
| `state.py` | Merges packet types into live per-car state and builds the snapshot. |
| `info.py` | Driver-info overlay calc layer: wear/ERS/sector/stint trackers + pit projection. |
| `server.py` | Async UDP listener + FastAPI WebSocket/static server. |
| `mock_sender.py` | Emits fake 2026-format packets for offline testing; `--from-file` replays a recording instead. |
| `recorder.py` | Captures raw incoming telemetry to `recordings/*.f1rec` for later replay. |
| `config.py` | Loads `settings.json` (shared by server + mock sender). |
| `settings.json` | Ports and push rate. |
| `driver_names.json` | Driver name overrides (source name/number → display name). |
| `web/` | The overlay (HTML/CSS/JS). |
| `specs/` | Official 2026 UDP spec: struct layouts (`.txt`) and the full document with ID appendices (`.pdf`). |

## Notes & next steps

- The overlay is a transparent surface sized to its own block (see
  [Overlay](#overlay-obs-browser-source)), so it drops straight into **OBS** as
  a Browser Source over gameplay capture.
- The server pushes snapshots at a fixed rate (`push_hz` in `settings.json`,
  default 20 Hz), decoupled from the much faster packet rate, so the browser is
  never flooded.
- Adding another overlay: write `web/blocks/<name>.js` (it calls
  `registerBlock`), import it from `web/blocks/index.js`, and add `<name>` to
  `OVERLAY_VIEWS` in `server.py`.
