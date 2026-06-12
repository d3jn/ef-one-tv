"""Loads settings.json — shared by server.py and mock_sender.py so they always
agree on the UDP port.

The file lives next to this module (or next to the frozen exe when packaged
with PyInstaller), matching the convention used by the sibling apps. Every key
is optional; missing keys fall back to DEFAULTS, and a missing file is fine.

Keys:
    udp_port   F1 25 telemetry UDP port to listen on   (default 20777)
    http_host  interface the graphics page binds to     (default 127.0.0.1)
    http_port  port the graphics page is served on       (default 5000)
    push_hz    snapshots/sec pushed to the browser       (default 20)
    brand_mark text shown in the red header badge         (default "F1")
    mode_rotation  standings-mode pools + auto-rotation, passed to the browser:
               { "enabled": bool,                       # auto-advance on/off
                 "pools":     { "race"|"quali"|"other": [mode, …] },  # ordered
                 "durations": { mode: seconds } }        # missing -> 5s
               Pools define the available modes per session kind (and their
               manual-cycle order) regardless of "enabled"; durations only
               matter while rotating. Valid modes: gap, interval, tyre (race),
               gap_quali (quali).
    retransmit_to  list of "host:port" strings to mirror raw incoming telemetry
               to (default []). Every UDP datagram is forwarded verbatim, as it
               arrives — unthrottled, independent of push_hz — so another tool
               on the network can read the same feed. IPv4 host:port; malformed
               entries are skipped with a warning.

Driver name overrides live in their own file, driver_names.json (next to
settings.json): a list of {source_name, source_number, target_name} objects used
to swap displayed driver names. A missing file means no overrides.

Overlay placement is no longer configurable: each overlay is served on its own
endpoint (/standings, /quali_lap_sectors) and pinned top-left in CSS.
"""

import json
import os
import sys

DEFAULTS = {
    "udp_port": 20777,
    "http_host": "127.0.0.1",
    "http_port": 5000,
    "push_hz": 20,
    "brand_mark": "F1",
    # Off by default: same pools as the built-in client fallback, no rotation.
    "mode_rotation": {
        "enabled": False,
        "pools": {
            "race": ["gap", "interval", "tyre"],
            "quali": ["gap_quali"],
            "other": ["gap"],
        },
        "durations": {},
    },
    # No rebroadcasting by default. Each entry is an "host:port" UDP destination
    # to mirror raw incoming telemetry to.
    "retransmit_to": [],
}


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load():
    path = os.path.join(_base_dir(), "settings.json")
    settings = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except FileNotFoundError:
        return settings  # defaults are fine when there's no file
    except json.JSONDecodeError as e:
        raise SystemExit(f"settings.json: invalid JSON ({e})")
    except OSError as e:
        raise SystemExit(f"settings.json: read failed ({e})")
    settings.update({k: user[k] for k in DEFAULTS if k in user})
    return settings


def load_driver_names():
    """Load driver name overrides from driver_names.json (next to settings.json):
    a list of {source_name, source_number, target_name} objects. A missing file
    means no overrides; invalid JSON / read errors are fatal, matching load()."""
    path = os.path.join(_base_dir(), "driver_names.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []  # no overrides is fine when there's no file
    except json.JSONDecodeError as e:
        raise SystemExit(f"driver_names.json: invalid JSON ({e})")
    except OSError as e:
        raise SystemExit(f"driver_names.json: read failed ({e})")
    return data if isinstance(data, list) else []


_settings = load()
UDP_PORT = int(_settings["udp_port"])
HTTP_HOST = str(_settings["http_host"])
HTTP_PORT = int(_settings["http_port"])
PUSH_HZ = int(_settings["push_hz"])
BRAND_MARK = str(_settings["brand_mark"])
DRIVER_NAME_OVERRIDES = load_driver_names()


def _normalize_rotation(raw):
    """Coerce the mode_rotation block into a predictable shape for the client:
    a dict with bool `enabled` and dict `pools`/`durations`. Bad types collapse
    to empty so the client falls back to its built-in pools / 5s defaults."""
    raw = raw if isinstance(raw, dict) else {}
    pools = raw.get("pools")
    durations = raw.get("durations")
    return {
        "enabled": bool(raw.get("enabled", False)),
        "pools": pools if isinstance(pools, dict) else {},
        "durations": durations if isinstance(durations, dict) else {},
    }


MODE_ROTATION = _normalize_rotation(_settings["mode_rotation"])


def _normalize_retransmit(raw):
    """Parse the retransmit_to list of "host:port" strings into (host, port)
    tuples for socket.sendto. Non-strings and malformed entries (missing colon,
    empty host, non-numeric/out-of-range port) are skipped with a warning so one
    typo can't sink startup. Bracketed IPv6 ("[::1]:20777") parses too, though
    the forwarding socket is IPv4."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        host, sep, port = (item.rpartition(":") if isinstance(item, str) else ("", "", ""))
        host = host.strip().strip("[]")
        if sep and host and port.isdigit() and 0 < int(port) <= 65535:
            out.append((host, int(port)))
        else:
            sys.stderr.write(
                f"settings.json: ignoring retransmit_to entry {item!r} "
                f"(want \"host:port\")\n"
            )
    return out


RETRANSMIT_TO = _normalize_retransmit(_settings["retransmit_to"])


def resolve_driver_name(name, number):
    """Map a telemetry driver to an override target name, or None if no rule
    applies. Match priority (per project decision): first by source_name, then
    by source_number; otherwise the caller falls back to the telemetry name.
    Name matching is case-insensitive and whitespace-trimmed."""
    name_key = (name or "").strip().casefold()
    # Tier 1: match by source name.
    if name_key:
        for o in DRIVER_NAME_OVERRIDES:
            src = str(o.get("source_name", "")).strip().casefold()
            if src and src == name_key:
                return o.get("target_name")
    # Tier 2: match by source number.
    if number is not None:
        for o in DRIVER_NAME_OVERRIDES:
            if o.get("source_number") == number:
                return o.get("target_name")
    return None
