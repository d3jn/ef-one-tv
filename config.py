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
    driver_name_overrides  list of {source_name, source_number, target_name}
               objects used to swap displayed driver names (default [])
"""

import json
import os
import sys

DEFAULTS = {
    "udp_port": 20777,
    "http_host": "127.0.0.1",
    "http_port": 5000,
    "push_hz": 20,
    "driver_name_overrides": [],
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


_settings = load()
UDP_PORT = int(_settings["udp_port"])
HTTP_HOST = str(_settings["http_host"])
HTTP_PORT = int(_settings["http_port"])
PUSH_HZ = int(_settings["push_hz"])
DRIVER_NAME_OVERRIDES = _settings["driver_name_overrides"] or []


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
