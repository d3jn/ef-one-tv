"""recorder.py — capture raw F1 25 UDP telemetry to a file for later replay.

Binds the telemetry UDP port from settings.json and writes every datagram it
receives, verbatim, to a fresh timestamped file under recordings/. Replay it with:

    python mock_sender.py --from-file recordings/<file>

File format (.f1rec): a flat sequence of length-prefixed, timestamped records.
Each record is RECORD_FMT — the datagram's arrival time (seconds since the first
packet, as a little-endian double) and its length (uint32) — followed by the raw
datagram bytes. The timestamps let the replayer reproduce the original packet
timing exactly; the lengths preserve datagram boundaries.

Run:  python recorder.py
(Point the game — or another sender — at this machine's telemetry port first.)
"""

import os
import socket
import struct
import time
from datetime import date

import config

# Per-packet framing: arrival time (s since first packet, double) + length (uint32).
# Shared with mock_sender.py's --from-file replayer, which imports these.
RECORD_FMT = "<dI"
RECORD_HEADER_SIZE = struct.calcsize(RECORD_FMT)  # 12
REC_EXT = ".f1rec"

UDP_PORT = config.UDP_PORT
RECORDINGS_DIR = os.path.join(config.app_dir(), "recordings")


def next_path():
    """A fresh recordings/<YYYY-MM-DD>-<n><ext> path. <n> auto-increments past any
    existing files for today, so repeated runs on the same date never collide."""
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    prefix = f"{date.today().isoformat()}-"   # e.g. "2026-06-13-"
    highest = 0
    for name in os.listdir(RECORDINGS_DIR):
        if name.startswith(prefix) and name.endswith(REC_EXT):
            stem = name[len(prefix):-len(REC_EXT)]
            if stem.isdigit():
                highest = max(highest, int(stem))
    return os.path.join(RECORDINGS_DIR, f"{prefix}{highest + 1}{REC_EXT}")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # A generous receive buffer so a burst of packets isn't dropped while we write.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    except OSError:
        pass
    sock.bind(("0.0.0.0", UDP_PORT))

    path = next_path()
    print(f"Recording F1 25 telemetry from UDP {UDP_PORT} to {path}")
    print("Point the game at this machine's telemetry port and drive. Ctrl+C to stop.")

    packets = 0
    start = None
    with open(path, "wb") as f:
        try:
            while True:
                data, _ = sock.recvfrom(65535)
                now = time.monotonic()
                if start is None:
                    start = now
                f.write(struct.pack(RECORD_FMT, now - start, len(data)))
                f.write(data)
                packets += 1
                if packets % 200 == 0:
                    f.flush()
                    print(f"\r{packets} packets recorded ({now - start:.0f}s)",
                          end="", flush=True)
        except KeyboardInterrupt:
            pass
    print(f"\nSaved {packets} packets to {path}")


if __name__ == "__main__":
    main()
