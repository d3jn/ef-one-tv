"""F1 25 UDP packet parsers — pure stdlib, no third-party telemetry libraries.

Format strings and tuple indices are hand-derived from the F1 25 UDP spec in
specs/f1_25_telemetry_structures.txt. Indices are POSITIONAL against those
formats: adding/removing a field shifts every later index, so reverify against
the spec when bumping the game year.

Wheel/tyre arrays in the F1 spec are ordered RL, RR, FL, FR.
"""

import struct

# --- Header (29 bytes) -------------------------------------------------------
# uint16 packetFormat, uint8 gameYear, uint8 majorVer, uint8 minorVer,
# uint8 packetVersion, uint8 packetId, uint64 sessionUID, float sessionTime,
# uint32 frameId, uint32 overallFrameId, uint8 playerCarIndex,
# uint8 secondaryPlayerCarIndex.
HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 29

# --- Per-car blocks ----------------------------------------------------------
# Car Telemetry: speed(H) throttle(f) steer(f) brake(f) clutch(B) gear(b)
# rpm(H) drs(B) revPct(B) revBits(H) brakeTemps[4](H) tyreSurf[4](B)
# tyreInner[4](B) engineTemp(H) tyrePressure[4](f) surfaceType[4](B).
CAR_TELEMETRY_FMT = "<HfffBbHBBH4H4B4BH4f4B"
CAR_TELEMETRY_SIZE = struct.calcsize(CAR_TELEMETRY_FMT)  # 60

# Lap Data: see spec lines 210-245. 57 bytes per car.
LAP_DATA_FMT = "<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB"
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)  # 57

# Car Status: see spec lines 536-568. 55 bytes per car.
CAR_STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBfffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)  # 55

# Participants: 7 uint8 + 32s name + 2 uint8 + uint16 + 2 uint8 + 12 uint8.
PARTICIPANT_DATA_FMT = "<7B32s2BH2B12B"
PARTICIPANT_DATA_SIZE = struct.calcsize(PARTICIPANT_DATA_FMT)  # 57

# Session packet, pre-marshal-zone block only (where total laps / type / track
# live). weather(B) trackTemp(b) airTemp(b) totalLaps(B) trackLength(H)
# sessionType(B) trackId(b) formula(B) timeLeft(H) duration(H) pitLimit(B)
# paused(B) spectating(B) spectatorIdx(B) sliPro(B) numMarshalZones(B).
SESSION_PRE_FMT = "<BbbBHBbBHHBBBBBB"

# After the pre-marshal block comes a fixed array of 21 MarshalZones (each a
# float zoneStart + int8 zoneFlag), then m_safetyCarStatus. zoneFlag: -1=invalid,
# 0=none, 1=green, 2=blue, 3=yellow. safetyCarStatus: 0=none, 1=full, 2=virtual.
MARSHAL_ZONE_FMT = "<fb"
MARSHAL_ZONE_SIZE = struct.calcsize(MARSHAL_ZONE_FMT)  # 5
MAX_MARSHAL_ZONES = 21

# Session History (packet 11, one car per packet, 1460 bytes). Fixed-size arrays:
# 100 lap-history entries followed by 8 tyre-stint slots. We only need the
# best-lap lap number and the tyre stints, so we skip over the lap array.
SESSION_HISTORY_HEAD_FMT = "<7B"  # carIdx, numLaps, numTyreStints, best{Lap,S1,S2,S3}LapNum
LAP_HISTORY_FMT = "<IHBHBHBB"     # one lap entry
LAP_HISTORY_SIZE = struct.calcsize(LAP_HISTORY_FMT)  # 14
NUM_LAPS_IN_HISTORY = 100
TYRE_STINT_FMT = "<3B"            # endLap (255=current), actualCompound, visualCompound
TYRE_STINT_SIZE = struct.calcsize(TYRE_STINT_FMT)  # 3
MAX_TYRE_STINTS = 8

# Packet IDs (subset we consume).
PACKET_SESSION = 1
PACKET_LAP = 2
PACKET_EVENT = 3
PACKET_PARTICIPANTS = 4
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_STATUS = 7
PACKET_SESSION_HISTORY = 11

NUM_CARS = 22

# --- Reference data ----------------------------------------------------------
# F1 2025 team ids (0-9) → name + broadcast livery colour.
TEAMS = {
    0: ("Mercedes", "#27F4D2"),
    1: ("Ferrari", "#E8002D"),
    2: ("Red Bull", "#3671C6"),
    3: ("Williams", "#64C4FF"),
    4: ("Aston Martin", "#229971"),
    5: ("Alpine", "#00A1E8"),
    6: ("Racing Bulls", "#6692FF"),
    7: ("Haas", "#B6BABD"),
    8: ("McLaren", "#FF8000"),
    9: ("Kick Sauber", "#52E252"),
}
DEFAULT_TEAM = ("F1", "#999999")

# Visual tyre compound → (short label, colour).
TYRES = {
    16: ("S", "#E8002D"),   # soft
    17: ("M", "#FFD12E"),   # medium
    18: ("H", "#EBEBEB"),   # hard
    7: ("I", "#43B02A"),    # intermediate
    8: ("W", "#0067AD"),    # wet
}
DEFAULT_TYRE = ("?", "#666666")

# track id → display name (unknown ids fall back to "Track <id>"). Ids verified
# against the sibling F1 25 parsers; 33 (Madrid) is provisional.
TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Bahrain",
    4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "COTA",
    16: "Interlagos", 17: "Red Bull Ring", 18: "Sochi", 19: "Mexico",
    20: "Baku", 21: "Sakhir Short", 22: "Silverstone Short", 23: "COTA Short",
    24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort", 27: "Imola",
    28: "Portimao", 29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Losail",
    33: "Madrid",
}

# F1 25 session-type ids (per the appendix). Note this differs from the pre-2023
# layout: Sprint-shootout/qualifying sits at 10-14 and Race moved to 15-17, with
# Time Trial at 18. Verified against the sibling F1 25 parsers.
SESSION_TYPES = {
    0: "Unknown", 1: "P1", 2: "P2", 3: "P3", 4: "Short Practice",
    5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Quali", 9: "One-Shot Quali",
    10: "SQ1", 11: "SQ2", 12: "SQ3", 13: "Short SQ", 14: "One-Shot SQ",
    15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial",
}

# LapData.m_resultStatus → broadcast label for cars out of the race. Per the
# F1 25 spec: 0=invalid, 1=inactive, 2=active, 3=finished, 4=didnotfinish,
# 5=disqualified, 6=not classified, 7=retired. Active/finished race normally
# (no label); the rest get a status label and are greyed out in the tower.
RESULT_LABELS = {
    1: "DNS",   # inactive — took no part
    4: "DNF",   # did not finish
    5: "DSQ",   # disqualified
    6: "NC",    # not classified
    7: "DNF",   # retired
}

RESULT_FINISHED = 3  # m_resultStatus: completed the race (gets a finish flag)

# m_driverStatus values (0=in garage, 1=flying lap, 2=in lap, 3=out lap,
# 4=on track). The out-lap state drives the quali "Out lap" label.
DRIVER_STATUS_OUT_LAP = 3


def result_label(result_status):
    """Out-of-race label (DNF/DSQ/DNS/NC), or None when racing/finished."""
    return RESULT_LABELS.get(result_status)


def team_info(team_id):
    return TEAMS.get(team_id, DEFAULT_TEAM)


def team_logo(team_id):
    """Logo filename under web/teams/ for a team, or None when unknown.
    The slug is the in-game team name lower-cased with spaces removed
    (e.g. "Aston Martin" -> "astonmartin", "Kick Sauber" -> "kicksauber"),
    matching the renamed files in web/teams/."""
    if team_id not in TEAMS:
        return None
    return TEAMS[team_id][0].lower().replace(" ", "") + ".png"


def tyre_info(visual_compound):
    return TYRES.get(visual_compound, DEFAULT_TYRE)


def track_name(track_id):
    return TRACKS.get(track_id, f"Track {track_id}")


def session_type_name(session_type):
    return SESSION_TYPES.get(session_type, f"Session {session_type}")


# Which header info to show per session type: races show a lap counter; the
# timed knock-out sessions (normal qualifying 5-9 and sprint qualifying 10-14)
# show a countdown; everything else (practice, time trial…) shows nothing.
RACE_SESSIONS = {15, 16, 17}
QUALI_SESSIONS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14}


def session_info_kind(session_type):
    if session_type in RACE_SESSIONS:
        return "race"
    if session_type in QUALI_SESSIONS:
        return "quali"
    return "none"


def parse_header(data):
    h = struct.unpack_from(HEADER_FMT, data, 0)
    return {
        "packet_format": h[0],
        "packet_id": h[5],
        "session_uid": h[6],
        "session_time": h[7],
        "player_car_index": h[10],
    }


def parse_car_telemetry(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        t = struct.unpack_from(CAR_TELEMETRY_FMT, data, offset)
        out.append({
            "speed": t[0],
            "gear": t[5],
            "rpm": t[6],
            "drs": t[7],  # 0 = off, 1 = on
        })
        offset += CAR_TELEMETRY_SIZE
    return out


def parse_lap(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        l = struct.unpack_from(LAP_DATA_FMT, data, offset)
        out.append({
            "last_lap_ms": l[0],
            "current_lap_ms": l[1],
            # Sector/delta times split as (msPart:H, minutesPart:B) to allow
            # values over 65s. Recombine to a single ms figure.
            "interval_to_front_ms": l[7] * 60000 + l[6],
            "gap_to_leader_ms": l[9] * 60000 + l[8],
            "lap_distance": l[10],
            "position": l[13],
            "lap_num": l[14],
            "pit_status": l[15],          # 0 none, 1 pitting, 2 in pit area
            "sector": l[17],             # 0 = S1, 1 = S2, 2 = S3
            "penalties_sec": l[19],       # accumulated time penalty (seconds)
            "drive_through": l[22],       # unserved drive-through penalties
            "driver_status": l[25],       # 3 = out lap (see DRIVER_STATUS_OUT_LAP)
            "result_status": l[26],
        })
        offset += LAP_DATA_SIZE
    return out


def parse_car_status(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        s = struct.unpack_from(CAR_STATUS_FMT, data, offset)
        out.append({
            "fuel_in_tank": s[5],
            "drs_allowed": s[11],
            "visual_tyre": s[14],
            "tyre_age_laps": s[15],
        })
        offset += CAR_STATUS_SIZE
    return out


def parse_participants(data):
    out = []
    offset = HEADER_SIZE + 1  # skip m_numActiveCars
    for _ in range(NUM_CARS):
        p = struct.unpack_from(PARTICIPANT_DATA_FMT, data, offset)
        name = p[7].split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        out.append({
            "ai_controlled": p[0],   # 1 = AI bot, 0 = human (multiplayer) player
            "team_id": p[3],
            "race_number": p[5],
            "name": name,
        })
        offset += PARTICIPANT_DATA_SIZE
    return out


def parse_num_active_cars(data):
    return struct.unpack_from("<B", data, HEADER_SIZE)[0]


def parse_session(data):
    s = struct.unpack_from(SESSION_PRE_FMT, data, HEADER_SIZE)
    num_zones = min(s[15], MAX_MARSHAL_ZONES)
    zones_off = HEADER_SIZE + struct.calcsize(SESSION_PRE_FMT)
    marshal_yellow = False
    for i in range(num_zones):
        _, zflag = struct.unpack_from(MARSHAL_ZONE_FMT, data, zones_off + i * MARSHAL_ZONE_SIZE)
        if zflag == 3:  # yellow
            marshal_yellow = True
            break
    sc_off = zones_off + MAX_MARSHAL_ZONES * MARSHAL_ZONE_SIZE
    safety_car_status = struct.unpack_from("<B", data, sc_off)[0]
    return {
        "total_laps": s[3],
        "session_type": s[5],
        "session_type_name": session_type_name(s[5]),
        "track_id": s[6],
        "track_name": track_name(s[6]),
        "session_time_left": s[8],   # seconds remaining (quali countdown)
        "marshal_yellow": marshal_yellow,
        "safety_car_status": safety_car_status,  # 0 none, 1 full SC, 2 VSC
        # Spectator state: who's being watched. m_playerCarIndex is meaningless
        # while spectating, so the spectated index is the authoritative "active
        # car" then (see GameState.snapshot).
        "is_spectating": bool(s[12]),         # m_isSpectating
        "spectator_car_index": s[13],         # m_spectatorCarIndex
    }


def parse_event(data):
    """Event packet: a 4-char code plus a type-specific detail union. We only
    decode the safety-car event ("SCAR"), whose detail is safetyCarType +
    eventType (0 Deployed, 1 Returning, 2 Returned, 3 Resume Race)."""
    code = bytes(data[HEADER_SIZE:HEADER_SIZE + 4]).decode("ascii", errors="replace")
    out = {"code": code}
    if code == "SCAR":
        sc_type, event_type = struct.unpack_from("<BB", data, HEADER_SIZE + 4)
        out["safety_car_type"] = sc_type
        out["safety_car_event"] = event_type
    return out


def parse_session_history(data):
    """One car's lap/tyre history. We keep the best-lap lap number and the tyre
    stints (enough to find the compound used on the fastest lap)."""
    h = struct.unpack_from(SESSION_HISTORY_HEAD_FMT, data, HEADER_SIZE)
    laps_off = HEADER_SIZE + 7
    num_stints = min(h[2], MAX_TYRE_STINTS)
    stints_off = laps_off + NUM_LAPS_IN_HISTORY * LAP_HISTORY_SIZE
    stints = []
    for i in range(num_stints):
        s = struct.unpack_from(TYRE_STINT_FMT, data, stints_off + i * TYRE_STINT_SIZE)
        stints.append({"end_lap": s[0], "visual": s[2]})
    # Time (ms) of the fastest lap — the first field of its lap-history entry.
    best_lap_num = h[3]   # 0 if no lap set yet; otherwise 1-based
    best_lap_time_ms = 0
    if 0 < best_lap_num <= NUM_LAPS_IN_HISTORY:
        lap = struct.unpack_from(LAP_HISTORY_FMT, data, laps_off + (best_lap_num - 1) * LAP_HISTORY_SIZE)
        best_lap_time_ms = lap[0]
    return {
        "car_idx": h[0],
        "best_lap_num": best_lap_num,
        "best_lap_time_ms": best_lap_time_ms,
        "tyre_stints": stints,
    }


def fastest_lap_tyre(best_lap_num, tyre_stints):
    """Visual compound id the fastest lap was set on, or None if unknown. The
    stint covering the best lap is the first whose end_lap reaches it (the
    current stint uses end_lap 255, so it covers any lap)."""
    if not best_lap_num:
        return None
    for stint in tyre_stints:
        if stint["end_lap"] >= best_lap_num:
            return stint["visual"]
    return None
