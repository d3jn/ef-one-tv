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
    driver_name_overrides  list of {source_name, source_number, target_name}
               objects used to swap displayed driver names (default [])
    mode_rotation  standings-mode pools + auto-rotation, passed to the browser:
               { "enabled": bool,                       # auto-advance on/off
                 "pools":     { "race"|"quali"|"other": [mode, …] },  # ordered
                 "durations": { mode: seconds } }        # missing -> 5s
               Pools define the available modes per session kind (and their
               manual-cycle order) regardless of "enabled"; durations only
               matter while rotating. Valid modes: gap, interval, tyre (race),
               gap_quali (quali).
    position   on-screen placement of overlay windows, in pixels from the
               top-left of the 1920×1080 stage:
               { "standings": { "x": px-from-left, "y": px-from-top } }
               Extend with more windows later. (default x=48, y=36)
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
    "driver_name_overrides": [],
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
    # Overlay placement, px from the stage's top-left (matches the CSS defaults).
    "position": {
        "standings": {"x": 48, "y": 36},
    },
}

# Default offsets for each overlay window, used to fill gaps in user config.
_POSITION_DEFAULTS = {"standings": {"x": 48, "y": 36}}


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


_settings = load()
UDP_PORT = int(_settings["udp_port"])
HTTP_HOST = str(_settings["http_host"])
HTTP_PORT = int(_settings["http_port"])
PUSH_HZ = int(_settings["push_hz"])
BRAND_MARK = str(_settings["brand_mark"])
DRIVER_NAME_OVERRIDES = _settings["driver_name_overrides"] or []


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


def _normalize_position(raw):
    """Coerce the position block into {window: {x, y}} with numeric coords,
    filling any missing window/axis from _POSITION_DEFAULTS so the client always
    gets a usable placement."""
    raw = raw if isinstance(raw, dict) else {}

    def _num(v, default):
        # bool is an int subclass — reject it so true/false don't become 1/0.
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default

    out = {}
    for window, default in _POSITION_DEFAULTS.items():
        win = raw.get(window) if isinstance(raw.get(window), dict) else {}
        out[window] = {
            "x": _num(win.get("x"), default["x"]),
            "y": _num(win.get("y"), default["y"]),
        }
    return out


POSITION = _normalize_position(_settings["position"])


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
