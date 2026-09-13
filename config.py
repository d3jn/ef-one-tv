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
    retransmit_to  list of "host:port" strings to mirror raw incoming telemetry
               to (default []). Every UDP datagram is forwarded verbatim, as it
               arrives — unthrottled, independent of push_hz — so another tool
               on the network can read the same feed. IPv4 host:port; malformed
               entries are skipped with a warning.
    pit_time_lost  per-track pit-loss overrides for the info overlay's pit
               projection (default {}), keyed by track slug:
               { "monza": {"green": 25, "sc": 17}, … }

Driver name overrides live in their own file, driver_names.json (next to
settings.json): a list of {source_name, source_number, target_name} objects used
to swap displayed driver names. A missing file means no overrides.

Overlay placement is not configurable: the overlay is served on its own
endpoint (/info) and pinned top-left in CSS.
"""

import json
import os
import sys

DEFAULTS = {
    "udp_port": 20777,
    "http_host": "127.0.0.1",
    "http_port": 5000,
    "push_hz": 20,
    # No rebroadcasting by default. Each entry is an "host:port" UDP destination
    # to mirror raw incoming telemetry to.
    "retransmit_to": [],
    # Per-track pit-loss overrides for the info overlay's pit projection, keyed by
    # track slug: { "monza": {"green": 25, "sc": 17}, ... }. Empty = built-in
    # estimates (see info.PITSTOP_TIMES).
    "pit_time_lost": {},
}

# Track slug -> game track_id (2026 appendix "Track IDs"), for the pit_time_lost
# setting.
TRACK_SLUG_TO_ID = {
    "melbourne": 0, "shanghai": 2, "sakhir": 3, "catalunya": 4,
    "monaco": 5, "montreal": 6, "silverstone": 7, "hungaroring": 9,
    "spa_francorchamps": 10, "monza": 11, "marina_bay": 12, "suzuka": 13,
    "yas_marina": 14, "austin": 15, "interlagos": 16, "red_bull_ring": 17,
    "mexico_city": 19, "baku": 20, "zandvoort": 26, "imola": 27, "jeddah": 29,
    "miami": 30, "las_vegas": 31, "losail": 32, "silverstone_reverse": 39,
    "red_bull_ring_reverse": 40, "zandvoort_reverse": 41, "madrid": 42,
}


def app_dir():
    """Directory to resolve config and resources from, cross-platform:

    - Frozen build (PyInstaller / py2exe / cx_Freeze, detected via sys.frozen):
      the directory containing the executable itself — so web/, settings.json,
      driver_names.json sit next to the .exe and stay user-editable, instead of
      the per-run temp/AppData extraction dir PyInstaller would otherwise use.
    - Running from source: this module's directory (the project root).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts):
    """Absolute path to a bundled resource (e.g. resource_path("web")).

    Prefers a copy sitting next to the executable / source (app_dir), so a loose
    web/ folder shipped beside the .exe always wins and can be edited. For a
    PyInstaller one-file build that embeds resources via --add-data, falls back
    to the extraction dir (sys._MEIPASS) when nothing is found next to the exe.
    """
    candidate = os.path.join(app_dir(), *parts)
    if os.path.exists(candidate):
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = os.path.join(meipass, *parts)
        if os.path.exists(bundled):
            return bundled
    return candidate  # fall through to the next-to-exe path for a clear error


def load():
    path = os.path.join(app_dir(), "settings.json")
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
    path = os.path.join(app_dir(), "driver_names.json")
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
DRIVER_NAME_OVERRIDES = load_driver_names()


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


def _normalize_pit_times(raw):
    """Parse the pit_time_lost map (track slug -> {green, sc} seconds) into
    {track_id: {green, sc}} for the pit projection. Bad slugs/values are skipped
    with a warning so one typo can't break startup."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for slug, vals in raw.items():
        tid = TRACK_SLUG_TO_ID.get(slug)
        if tid is None or not isinstance(vals, dict):
            sys.stderr.write(f"settings.json: ignoring pit_time_lost entry {slug!r}\n")
            continue
        entry = {}
        for key in ("green", "sc"):
            if isinstance(vals.get(key), (int, float)):
                entry[key] = vals[key]
        if entry:
            out[tid] = entry
    return out


PIT_TIME_OVERRIDES = _normalize_pit_times(_settings["pit_time_lost"])


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
