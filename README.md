# ef-one-tv

Reads **F1 25** UDP telemetry and renders it as TV-style broadcast graphics in
the browser. A Python server listens for the game's telemetry packets, folds
them into live session state, and pushes it to a web page over a WebSocket. The
graphics (a broadcast timing tower) are plain HTML/CSS/JS.

```
F1 25 game ──UDP :20777──> server.py ──WebSocket──> browser :5000
                           parse + merge            HTML/CSS/JS tower
```

The F1 25 packets are parsed from scratch in `f1_packets.py` using only Python's
`struct` module against the official spec in `specs/` — **no third-party
telemetry library**.

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

In a second terminal, send synthetic-but-real F1 25 packets:

```bash
python mock_sender.py
```

You should see a 20-car timing tower that races, swaps positions (with smooth
sliding rows), pits, and toggles DRS.

### With the real game

In F1 25: **Settings → Telemetry Settings**
- UDP Telemetry: **On**
- UDP Broadcast Mode: Off (or On if the game is on another PC)
- IP Address: `127.0.0.1` (same machine) or this machine's LAN IP
- Port: `20777`
- UDP Send Rate: 20–60 Hz
- UDP Format: **2025**

## Overlays (OBS browser sources)

Each overlay is served on its own route and sized to just its own block, so you
can add them to **OBS** as separate Browser Sources without a full-screen canvas.
Set each source to the size below (or any size with the same aspect — the block
scales to fit); the background is transparent and each block is pinned top-left.

| Route | Browser source size | Shows |
|-------|--------------------|-------|
| `/standings` | **660 × 960** | The timing tower (incl. the session-flag tab and penalty/finish tabs that extend right of it; height covers the 22-car maximum). |
| `/quali_lap_sectors` | **376 × 132** | The live qualifying lap-sector block for the active driver. |

<http://localhost:5000> is a landing page linking to both.

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
| `udp_port` | F1 25 telemetry UDP port to listen on (must match the game) | `20777` |
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
web/                 ← the graphics (HTML/CSS/JS, teams/, other/)
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
| `f1_packets.py` | Packet parsers + reference data (teams, tyres, tracks). Pure `struct`. |
| `state.py` | Merges packet types into one sorted broadcast snapshot. |
| `server.py` | Async UDP listener + FastAPI WebSocket/static server. |
| `mock_sender.py` | Emits fake F1 25 packets for offline testing. |
| `config.py` | Loads `settings.json` (shared by server + mock sender). |
| `settings.json` | Ports and push rate. |
| `driver_names.json` | Driver name overrides (source name/number → display name). |
| `web/` | The broadcast graphics (HTML/CSS/JS). |
| `specs/` | Official F1 25 UDP structure reference. |

## Notes & next steps

- Each overlay is a transparent surface sized to its own block (see
  [Overlays](#overlays-obs-browser-sources)), so it drops straight into **OBS**
  as a Browser Source over gameplay capture.
- The server pushes snapshots at a fixed **20 Hz** (`PUSH_HZ` in `server.py`),
  decoupled from the much faster packet rate, so the browser is never flooded.
- Ports: `UDP_PORT` and `HTTP_PORT` are constants at the top of `server.py`.
- Easy additions: fastest-lap banner, lower-third driver focus card, mini
  sector times, track map (from the Motion packet), tyre/fuel widgets. The
  parsers for most of this data are already in `f1_packets.py`.
- For richer, timeline-sequenced motion (broadcast wipes, staggered reveals),
  drop in **GSAP**; the current reorder uses a CSS-transform technique and needs
  no dependencies.
