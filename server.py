"""ef-one-tv server: reads F1 25 UDP telemetry and serves broadcast graphics.

One asyncio loop does three jobs:
  1. Listens for F1 25 UDP packets on UDP_PORT and folds them into GameState.
  2. Serves the static web/ page on http://localhost:HTTP_PORT.
  3. Pushes the latest broadcast snapshot to every connected browser over a
     WebSocket at PUSH_HZ — decoupled from the (much faster) packet rate so we
     never flood the client.

Run:  python server.py
"""

import asyncio
import contextlib
import json
import socket
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from state import GameState

# All configurable from settings.json (see config.py).
UDP_PORT = config.UDP_PORT      # F1 25 telemetry port to listen on
HTTP_HOST = config.HTTP_HOST    # interface the page binds to
HTTP_PORT = config.HTTP_PORT    # http://localhost:<HTTP_PORT>
PUSH_HZ = config.PUSH_HZ        # snapshots/sec pushed to the browser
RETRANSMIT_TO = config.RETRANSMIT_TO  # [(host, port), …] to mirror raw packets to

# Resolve web/ next to the executable (frozen) or the source tree, NOT via
# __file__ — under PyInstaller that points into the temp extraction dir.
WEB_DIR = Path(config.resource_path("web"))

game = GameState()
clients: set[WebSocket] = set()
forward_sock: socket.socket | None = None  # set in lifespan when retransmitting


class TelemetryProtocol(asyncio.DatagramProtocol):
    """Folds each inbound UDP datagram into the shared GameState, and — when
    retransmit_to is configured — mirrors the raw datagram on to each
    destination, verbatim and unthrottled (no push_hz gating)."""

    def datagram_received(self, data, addr):
        # Mirror first so a parser hiccup can't stop the passthrough.
        if forward_sock is not None:
            for dest in RETRANSMIT_TO:
                try:
                    forward_sock.sendto(data, dest)
                except OSError:
                    pass  # a dead/unreachable destination must never stall ingest
        game.update(data)

    def error_received(self, exc):
        # Stray ICMP "port unreachable" etc. — never fatal for a UDP listener.
        pass


async def broadcaster():
    """Push the latest snapshot to all clients at a fixed cadence."""
    interval = 1 / PUSH_HZ
    while True:
        await asyncio.sleep(interval)
        if not clients:
            continue
        payload = json.dumps(game.snapshot())
        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                clients.discard(ws)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global forward_sock
    loop = asyncio.get_running_loop()
    # SO_REUSEADDR + a generous receive buffer so we don't drop packets.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("0.0.0.0", UDP_PORT))
    transport, _ = await loop.create_datagram_endpoint(
        TelemetryProtocol, sock=sock
    )
    # One non-blocking sender for all retransmit destinations: fire-and-forget so
    # a slow/dead target can never back up the ingest path.
    if RETRANSMIT_TO:
        forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        forward_sock.setblocking(False)
    task = asyncio.create_task(broadcaster())
    print(f"Listening for F1 25 telemetry on UDP {UDP_PORT}")
    print(f"Open the graphics at http://{HTTP_HOST}:{HTTP_PORT}")
    if RETRANSMIT_TO:
        print("Retransmitting raw telemetry to "
              + ", ".join(f"{h}:{p}" for h, p in RETRANSMIT_TO))
    try:
        yield
    finally:
        task.cancel()
        transport.close()
        if forward_sock is not None:
            forward_sock.close()
            forward_sock = None


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    # Send an immediate snapshot so a fresh client isn't blank for up to 1 tick.
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps(game.snapshot()))
    try:
        while True:
            await ws.receive_text()  # we don't expect inbound messages; keepalive
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


# Overlay blocks: each name is both the route (/<name>) and the client-side
# block id (see web/blocks/). They all serve the same generic shell, which
# resolves the route to a block and mounts it. Add a block by adding its name
# here and a matching web/blocks/<name>.js module.
OVERLAY_VIEWS = ["standings", "quali_lap_sectors"]


@app.get("/")
async def index():
    # Landing page linking to the overlays (each its own OBS browser source).
    return FileResponse(WEB_DIR / "index.html")


def _make_overlay_route():
    async def overlay():
        return FileResponse(WEB_DIR / "overlay.html")
    return overlay


for _view in OVERLAY_VIEWS:
    app.add_api_route(f"/{_view}", _make_overlay_route(), methods=["GET"])


app.mount("/", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=HTTP_HOST, port=HTTP_PORT, log_level="warning")
