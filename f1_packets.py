"""F1 UDP packet parsers for the 2026 format — pure stdlib, no third-party
telemetry libraries.

Targets the "2026" UDP format shipped with the F1 25 2026 Season Pack (in-game
UDP Format: 2026). Packets in any other format are rejected by PACKET_FORMAT.

Format strings and tuple indices are hand-derived from the spec in
specs/f1_2026_telemetry_structures.txt; the appendices (team / track / session
type ids, button flags) are only in specs/f1_2026_data_output.pdf. Indices are
POSITIONAL against those formats: adding/removing a field shifts every later
index, so reverify against the spec when bumping the format.

Wheel/tyre arrays in the F1 spec are ordered RL, RR, FL, FR.
"""

import struct

# m_packetFormat value this module parses (header field 0).
PACKET_FORMAT = 2026

# --- Header (29 bytes) -------------------------------------------------------
# uint16 packetFormat, uint8 gameYear, uint8 majorVer, uint8 minorVer,
# uint8 packetVersion, uint8 packetId, uint64 sessionUID, float sessionTime,
# uint32 frameId, uint32 overallFrameId, uint8 playerCarIndex,
# uint8 secondaryPlayerCarIndex.
HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 29

# --- Per-car blocks ----------------------------------------------------------
# Lap Data: see spec struct LapData (line 241). 57 bytes per car, unchanged
# from 2025. Packet: 29 + 24 * 57 + 2 = 1399 bytes.
LAP_DATA_FMT = "<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB"
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)  # 57

# Car Status: see spec struct CarStatusData (line 568). 59 bytes per car — 2026
# inserted m_ersHarvestLimitPerLap (float) after m_ersHarvestedThisLapMGUH, past
# every field we read. Packet: 29 + 24 * 59 = 1445 bytes.
CAR_STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBffffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)  # 59

# Car Telemetry 2 (new in 2026): see spec struct CarTelemetry2Data (line 900).
# activeAeroMode(B) activeAeroAvailable(B) activeAeroActivationDistance(H)
# overtakeAvailable(B) overtakeActive(B) overtakeActivationDistance(H)
# 2026Regulations(B) drivingWrongWay(B). 10 bytes per car.
# Packet: 29 + 24 * 10 = 269 bytes.
CAR_TELEMETRY2_FMT = "<BBHBBHBB"
CAR_TELEMETRY2_SIZE = struct.calcsize(CAR_TELEMETRY2_FMT)  # 10

# Participants: aiControlled(B), driverId/networkId/teamId (H each — widened
# from uint8 in 2026), myTeam/raceNumber/nationality (B), 32s name,
# yourTelemetry/showOnlineNames (B), techLevel(H), platform/numColours (B),
# 12 uint8 livery. 60 bytes. Packet: 29 + 1 + 24 * 60 = 1470 bytes.
PARTICIPANT_DATA_FMT = "<B3H3B32s2BH2B12B"
PARTICIPANT_DATA_SIZE = struct.calcsize(PARTICIPANT_DATA_FMT)  # 60

# Session packet, pre-marshal-zone block only (where total laps / type / track
# id live). weather(B) trackTemp(b) airTemp(b) totalLaps(B) trackLength(H)
# sessionType(B) trackId(b) formula(B) timeLeft(H) duration(H) pitLimit(B)
# paused(B) spectating(B) spectatorIdx(B) sliPro(B) numMarshalZones(B).
# 2026 grew the packet to 926 bytes, but only by appending the active-aero/DRS
# zones and assist settings at the end — every offset we read is unchanged.
SESSION_PRE_FMT = "<BbbBHBbBHHBBBBBB"
SESSION_PACKET_SIZE = 926

# After the pre-marshal block comes a fixed array of 21 MarshalZones (each a
# float zoneStart + int8 zoneFlag), then m_safetyCarStatus (0=none, 1=full,
# 2=virtual, 3=formation lap). We skip the zones; their size is needed to find
# the SC status.
MARSHAL_ZONE_FMT = "<fb"
MARSHAL_ZONE_SIZE = struct.calcsize(MARSHAL_ZONE_FMT)  # 5
MAX_MARSHAL_ZONES = 21

# After the marshal zones + safetyCarStatus comes networkGame(B),
# numWeatherForecastSamples(B), then a fixed array of 64 WeatherForecastSamples
# (the first numWeatherForecastSamples are valid). Each is
# sessionType(B) timeOffset(B) weather(B) trackTemp(b) trackTempChange(b)
# airTemp(b) airTempChange(b) rainPct(B) = 8 bytes.
WEATHER_SAMPLE_FMT = "<BBBbbbbB"
WEATHER_SAMPLE_SIZE = struct.calcsize(WEATHER_SAMPLE_FMT)  # 8
MAX_WEATHER_SAMPLES = 64
WEATHER_TYPES = {0: "Clear", 1: "Light cloud", 2: "Overcast",
                 3: "Light rain", 4: "Heavy rain", 5: "Storm"}

# Car Damage (packet 10): m_tyresWear[4] (float %) then 30 uint8 damage fields.
# We read tyre wear (0-3) and the two front-wing values (16, 17). Unchanged
# from 2025. Packet: 29 + 24 * 46 = 1133 bytes.
CAR_DAMAGE_FMT = "<4f30B"
CAR_DAMAGE_SIZE = struct.calcsize(CAR_DAMAGE_FMT)  # 46

# Packet IDs (subset we consume).
PACKET_SESSION = 1
PACKET_LAP = 2
PACKET_EVENT = 3
PACKET_PARTICIPANTS = 4
PACKET_CAR_STATUS = 7
PACKET_CAR_DAMAGE = 10
PACKET_CAR_TELEMETRY2 = 16

NUM_CARS = 24  # cs_maxNumCarsInUDPData (22 -> 24 in 2026 for the eleventh team)

# --- Reference data ----------------------------------------------------------
# Session-type ids (per the appendix; unchanged in 2026). Note this differs from
# the pre-2023 layout: Sprint-shootout/qualifying sits at 10-14 and Race moved to
# 15-17, with Time Trial at 18.
SESSION_TYPES = {
    0: "Unknown", 1: "P1", 2: "P2", 3: "P3", 4: "Short Practice",
    5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Quali", 9: "One-Shot Quali",
    10: "SQ1", 11: "SQ2", 12: "SQ3", 13: "Short SQ", 14: "One-Shot SQ",
    15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial",
}

# ERS energy store is capped at 4 MJ (F1 regs); m_ersStoreEnergy is in Joules,
# so battery % = energy / ERS_MAX_J * 100.
ERS_MAX_J = 4_000_000.0


def session_type_name(session_type):
    return SESSION_TYPES.get(session_type, f"Session {session_type}")


def parse_header(data):
    h = struct.unpack_from(HEADER_FMT, data, 0)
    return {
        "packet_format": h[0],
        "packet_id": h[5],
        "session_uid": h[6],
        "session_time": h[7],
        "player_car_index": h[10],
    }


def parse_car_telemetry2(data):
    """Per-car 2026-regulation state. Overtake Mode lives here — NOT in Car
    Status m_ersDeployMode, whose value 3 is the unrelated "boost" deploy mode."""
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        t = struct.unpack_from(CAR_TELEMETRY2_FMT, data, offset)
        out.append({
            "overtake_available": t[3],   # m_overtakeAvailable: 0 = no, 1 = yes
            "overtake_active": t[4],      # m_overtakeActive: 0 = no, 1 = yes
        })
        offset += CAR_TELEMETRY2_SIZE
    return out


def parse_lap(data):
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        l = struct.unpack_from(LAP_DATA_FMT, data, offset)
        out.append({
            "last_lap_ms": l[0],
            # Sector/delta times split as (msPart:H, minutesPart:B) to allow
            # values over 65s. Recombine to a single ms figure.
            "sector1_ms": l[3] * 60000 + l[2],   # this lap's S1 (0 until crossed)
            "sector2_ms": l[5] * 60000 + l[4],   # this lap's S2 (0 until crossed)
            "gap_to_leader_ms": l[9] * 60000 + l[8],
            "lap_distance": l[10],
            "position": l[13],
            "lap_num": l[14],
            "pit_status": l[15],          # 0 none, 1 pitting, 2 in pit area
            "sector": l[17],             # 0 = S1, 1 = S2, 2 = S3
            "penalties_sec": l[19],       # accumulated time penalty (seconds)
            "drive_through": l[22],       # unserved drive-through penalties
            "unserved_sg": l[23],         # unserved stop-go penalties
            "total_distance": l[11],      # total race distance (m), for lap-down math
            "should_serve_pen": l[30],    # m_pitStopShouldServePen (pit projection)
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
            "visual_tyre": s[14],
            "tyre_age_laps": s[15],
            "ers_energy_j": s[19],        # ERS store in Joules (max ERS_MAX_J)
            "ers_deploy_mode": s[20],     # 0 none, 1 medium, 2 hotlap, 3 boost
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
            "race_number": p[5],
            "name": name,
        })
        offset += PARTICIPANT_DATA_SIZE
    return out


def parse_session(data):
    s = struct.unpack_from(SESSION_PRE_FMT, data, HEADER_SIZE)
    zones_off = HEADER_SIZE + struct.calcsize(SESSION_PRE_FMT)
    sc_off = zones_off + MAX_MARSHAL_ZONES * MARSHAL_ZONE_SIZE
    safety_car_status = struct.unpack_from("<B", data, sc_off)[0]
    # safetyCarStatus(B), networkGame(B), numWeatherForecastSamples(B), then the
    # forecast samples. Used by the info block's page-2 weather panel.
    weather_forecast = []
    try:
        num_samples = struct.unpack_from("<B", data, sc_off + 2)[0]
        w_off = sc_off + 3
        for _ in range(min(num_samples, MAX_WEATHER_SAMPLES)):
            if w_off + WEATHER_SAMPLE_SIZE > len(data):
                break
            w = struct.unpack_from(WEATHER_SAMPLE_FMT, data, w_off)
            weather_forecast.append({
                "time_offset": w[1],   # minutes ahead
                "weather": w[2],       # 0-5, see WEATHER_TYPES
                "rain_pct": w[7],      # 0-100
            })
            w_off += WEATHER_SAMPLE_SIZE
    except struct.error:
        pass
    return {
        "total_laps": s[3],
        "track_length_m": s[4],
        "session_type": s[5],
        "track_id": s[6],
        "safety_car_status": safety_car_status,  # 0 none, 1 full SC, 2 VSC, 3 formation lap
        "weather_forecast": weather_forecast,
        # Spectator state: who's being watched. m_playerCarIndex is meaningless
        # while spectating, so the spectated index is the authoritative "active
        # car" then (see GameState.snapshot).
        "is_spectating": bool(s[12]),         # m_isSpectating
        "spectator_car_index": s[13],         # m_spectatorCarIndex
    }


def parse_event(data):
    """Event packet: a 4-char code plus a type-specific detail union. We only
    decode the button-status event ("BUTN")."""
    code = bytes(data[HEADER_SIZE:HEADER_SIZE + 4]).decode("ascii", errors="replace")
    out = {"code": code}
    if code == "BUTN":
        # Button-status event: a 32-bit bitmask of currently-pressed buttons.
        # Used to flip the info overlay's pages via bound UDP Actions.
        out["buttons"] = struct.unpack_from("<I", data, HEADER_SIZE + 4)[0]
    return out


# UDP Action button masks (the info block uses Action 1/2 to page next/prev).
UDP_ACTION_1_MASK = 0x00100000
UDP_ACTION_2_MASK = 0x00200000


def parse_car_damage(data):
    """Per-car tyre wear (worst corner drives the info block) and front-wing
    damage. tyre_wear is four floats (RL, RR, FL, FR) in percent."""
    out = []
    offset = HEADER_SIZE
    for _ in range(NUM_CARS):
        d = struct.unpack_from(CAR_DAMAGE_FMT, data, offset)
        out.append({
            "tyre_wear": [d[0], d[1], d[2], d[3]],
            "fw_left": d[16],
            "fw_right": d[17],
        })
        offset += CAR_DAMAGE_SIZE
    return out
