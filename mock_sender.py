"""Emit synthetic F1 25 UDP packets so you can see the graphics move without
the game. It builds REAL packets in the F1 25 wire format (parsed by the same
f1_packets.py the server uses) — a 20-car grid that races, swaps places, pits,
and toggles DRS.

Run the server in one terminal, then:  python mock_sender.py
Pick the session with --mode:        python mock_sender.py --mode quali
"""

import argparse
import math
import os
import random
import socket
import struct
import sys
import time

import config
import f1_packets as fp
from recorder import RECORD_FMT, RECORD_HEADER_SIZE

# Send to localhost on the same UDP port the server listens on (settings.json).
HOST, PORT = "127.0.0.1", config.UDP_PORT
NUM_ACTIVE = 20

# --mode name → session type id sent in the session packet. Add more here to
# emulate other session types (ids per f1_packets.SESSION_TYPES).
SESSION_MODES = {
    "race": 15,      # Race
    "quali": 5,      # Qualifying (Q1)
    "practice": 1,   # Practice (P1)
}

# Car index → resultStatus, to demo out-of-race labels (see lap_packet).
# 1=inactive(DNS), 5=disqualified(DSQ), 4=didnotfinish(DNF), 7=retired(DNF).
# Car 15 is also in NO_TIME_CARS: a driver who retired without setting a time,
# to exercise the quali rule that sinks them to the very bottom.
OUT_STATES = {15: 4, 16: 1, 17: 5, 18: 4, 19: 7}

# Car indices that have finished the race (resultStatus 3 -> finish flag). Car 4
# also carries penalties, to show the flag takes priority over the penalty board.
FINISHED = {4, 5, 6}

# Quali demo (visible when session_packet is sent with a quali type): cars on an
# out lap (driverStatus 3 -> "Out lap") and cars with no lap set (lastLap 0 ->
# "No time"). Car 12 is both, to show "Out lap" takes priority over "No time".
OUT_LAP_CARS = {3, 12}
NO_TIME_CARS = {12, 14, 15}

# Car index → (time-penalty seconds, unserved drive-through count), to demo the
# penalty board: "+5", "DT", "+3 DT", "+13".
PENALTIES = {2: (5, 0), 3: (0, 1), 4: (3, 1), 9: (13, 0)}

# Car index → (online handle, race number) for human (multiplayer) players.
# Everyone else is an AI bot. Single-name handles (no first/last) on purpose,
# to exercise the multiplayer name handling and the name-swap overrides.
HUMANS = {
    0: ("Cool Racer 7", 99),   # no override -> shown whole as "COOL RACER 7"
    1: ("lando_gamer", 4),     # override by source_name -> "LANDO"
    2: ("ScuderiaFan", 16),    # override by source_number 16 -> "CHARLES"
}

DRIVERS = [
    ("M VERSTAPPEN", 1, 2), ("L NORRIS", 4, 8), ("C LECLERC", 16, 1),
    ("O PIASTRI", 81, 8), ("C SAINZ", 55, 3), ("G RUSSELL", 63, 0),
    ("L HAMILTON", 44, 1), ("S PEREZ", 11, 2), ("F ALONSO", 14, 4),
    ("L STROLL", 18, 4), ("P GASLY", 10, 5), ("E OCON", 31, 5),
    ("A ALBON", 23, 3), ("Y TSUNODA", 22, 6), ("D RICCIARDO", 3, 6),
    ("N HULKENBERG", 27, 7), ("K MAGNUSSEN", 20, 7), ("V BOTTAS", 77, 9),
    ("G ZHOU", 24, 9), ("J DORUKHAN", 30, 0),
]


# --- Sector-panel demo (quali only) -----------------------------------------
# The player's car (index 0) loops a continuous ~95s flying lap: the clock runs
# up, m_sector steps 0->1->2, and the lap number ticks over at the line. Its
# session history reports the splits below so the sector panel lights up — S1
# and S3 are the fastest of anyone (purple), S2 is its own best but a hair off
# car 8's, which holds the best S2 (green). Car 0 also holds the fastest lap, so
# it is its own reference time. Other cars stay slower in every sector.
LAP_PERIOD_MS = 95000        # full flying-lap length before the line
SECTOR1_END_MS = 31000       # S1 -> S2 boundary
SECTOR2_END_MS = 63000       # S2 -> S3 boundary
PLAYER_SECTORS = (27500, 29800, 30700)   # car 0 best splits (sum = fastest lap)
BEST_S2_CAR = 8                          # holds the best S2 -> car 0's S2 is green
BEST_S2_SECTORS = (28200, 29000, 31400)  # only S2 (29000) beats car 0
# Car 0's *live* lap, distinct from its fastest lap above, so the delta flash has
# something to compare: S1 a touch slower (+0.150, yellow), S1+S2 ahead (-0.250,
# green), the lap a touch slower (+0.100, yellow).
CUR_LAP_SECTORS = (27650, 29400, 31050)
CUR_LAP_TIME = sum(CUR_LAP_SECTORS)      # 88100
PLAYER_ERS_J = 3_200_000.0               # car 0 at 80% — clears the >50% show gate
# Start a few seconds before the line so the first lap-start (which arms the
# panel) lands quickly, instead of after a full ~95s lap.
LAP_START_OFFSET_MS = LAP_PERIOD_MS - 6000


def flying_lap(t):
    """(lap_num, lap_time_ms, sector) for the looping flying lap. lap_num cycles
    inside the 100-entry history array so the best-sector lookups stay valid."""
    total = int(t * 1000) + LAP_START_OFFSET_MS
    lap_time = total % LAP_PERIOD_MS
    lap_num = total // LAP_PERIOD_MS % 90 + 2   # 2..91
    sector = 0 if lap_time < SECTOR1_END_MS else 1 if lap_time < SECTOR2_END_MS else 2
    return lap_num, lap_time, sector


def header(packet_id, frame):
    # packetFormat=2025, gameYear=25, major=1, minor=0, packetVersion=1
    return struct.pack(
        fp.HEADER_FMT, 2025, 25, 1, 0, 1, packet_id,
        0x1234ABCD, time.monotonic() % 1000, frame, frame, 0, 255,
    )


def participants_packet(frame):
    body = struct.pack("<B", NUM_ACTIVE)
    for i in range(fp.NUM_CARS):
        if i < NUM_ACTIVE:
            name, number, team = DRIVERS[i]
        else:
            name, number, team = "", 0, 0
        if i in HUMANS:                          # human player overrides the bot
            name, number = HUMANS[i]
            ai = 0
        else:
            ai = 1
        body += struct.pack(
            fp.PARTICIPANT_DATA_FMT,
            ai, 255, 0, team, 0, number, 0,      # ai..nationality
            name.encode("utf-8")[:31],            # 32s name (null-padded by pack)
            1, 1,                                 # yourTelemetry, showOnlineNames
            0,                                    # techLevel
            1, 0,                                 # platform, numColours
            *([0] * 12),                          # livery colours
        )
    return header(fp.PACKET_PARTICIPANTS, frame) + body


def session_packet(frame, session_type=15, yellow=False, safety_car=0):
    # weather, trackTemp, airTemp, totalLaps, trackLength, sessionType (15=Race,
    # 5=Q1, …), trackId=10(Spa), …, numMarshalZones=21. Followed by the 21
    # MarshalZones (zone 0 yellow if `yellow`) and m_safetyCarStatus.
    pre = struct.pack(
        fp.SESSION_PRE_FMT,
        1, 30, 24, 44, 7004, session_type, 10, 0, 3600, 3600, 80, 0, 0, 0, 0, 21,
    )
    zones = b"".join(
        struct.pack(fp.MARSHAL_ZONE_FMT, i / fp.MAX_MARSHAL_ZONES,
                    3 if (yellow and i == 0) else 0)
        for i in range(fp.MAX_MARSHAL_ZONES)
    )
    return header(fp.PACKET_SESSION, frame) + pre + zones + struct.pack("<B", safety_car)


def scar_event(frame, sc_type, event_type):
    # Safety-car event ("SCAR"): safetyCarType + eventType (3 = Resume Race).
    body = b"SCAR" + struct.pack("<BB", sc_type, event_type)
    return header(fp.PACKET_EVENT, frame) + body


def flag_demo(t):
    """Cycle flag states for the demo: clear → yellow → VSC → SC → resume."""
    phase = int(t) % 32
    if phase < 6:   return (False, 0)   # normal racing (no indicator)
    if phase < 14:  return (True, 0)    # yellow flag
    if phase < 20:  return (False, 2)   # VSC
    if phase < 28:  return (False, 1)   # full SC
    return (False, 0)                   # racing resumes (resume event on entry)


def lap_packet(frame, t, quali=False):
    body = b""
    # In quali, every car shares one looping flying lap so the active car's clock
    # runs, its sectors advance, and the lap ticks over at the line. In a race the
    # lap counter stays put (the old steady-state demo).
    flap_num, flap_time, fsector = flying_lap(t)
    cur_lap_ms = flap_time if quali else int(t * 1000) % 95000
    cur_sector = fsector if quali else 0
    for i in range(fp.NUM_CARS):
        cur_s1 = cur_s2 = 0   # this lap's S1/S2 splits (filled for the active car)
        if i < NUM_ACTIVE:
            # Positions shuffle slowly so rows visibly reorder on the tower; the
            # active car holds P1 in quali so its panel number doesn't jitter.
            wobble = math.sin(t * 0.25 + i) * 1.5
            position = max(1, min(NUM_ACTIVE, round(i + 1 + wobble)))
            # Leader has no gap to itself; others are always positive deltas.
            gap_ms = 0 if i == 0 else max(0, int(i * 1100 + math.sin(t + i) * 400))
            interval_ms = 0 if i == 0 else int(900 + math.sin(t * 0.7 + i) * 350)
            last_lap_ms = int(92000 + i * 120 + math.sin(t * 0.1 + i) * 300)
            if quali and i == 0:
                position = 1
                # Reveal this lap's splits as the player crosses each sector, and
                # report the finished lap so the line-crossing delta has a value.
                cur_s1 = CUR_LAP_SECTORS[0] if cur_sector >= 1 else 0
                cur_s2 = CUR_LAP_SECTORS[1] if cur_sector >= 2 else 0
                last_lap_ms = CUR_LAP_TIME
            if i in NO_TIME_CARS:
                last_lap_ms = 0           # never crossed the line -> "No time"
            lap_num = flap_num if quali else 12
            pit = 1 if (i == 7 and int(t) % 40 < 4) else 0
            # 3 = out lap, 4 = on track (default for a circulating car).
            driver_status = 3 if i in OUT_LAP_CARS else 4
            # A few cars out of the race, to exercise the status labels:
            # resultStatus 1=inactive(DNS), 4=DNF, 5=DSQ, 7=retired(DNF).
            result = 3 if i in FINISHED else OUT_STATES.get(i, 2)
            pen_sec, drive_through = PENALTIES.get(i, (0, 0))
        else:
            position = lap_num = pit = result = driver_status = 0
            gap_ms = interval_ms = last_lap_ms = 0
            pen_sec = drive_through = 0

        gap_min, gap_rem = divmod(gap_ms, 60000)
        int_min, int_rem = divmod(interval_ms, 60000)
        body += struct.pack(
            fp.LAP_DATA_FMT,
            last_lap_ms, cur_lap_ms,               # lastLap, currentLap
            cur_s1 % 60000, cur_s1 // 60000,       # sector 1 split (msPart, minPart)
            cur_s2 % 60000, cur_s2 // 60000,       # sector 2 split (msPart, minPart)
            int_rem, int_min,                      # delta to car in front
            gap_rem, gap_min,                      # delta to race leader
            i * 250.0, i * 250.0, 0.0,             # lapDistance, totalDistance, scDelta
            position, lap_num, pit, 1, cur_sector, 0,  # pos, lapNum, pit, numStops, sector, invalid
            pen_sec, 0, 0,                         # penalties, totalWarn, cornerCutWarn
            drive_through, 0, 0, driver_status,    # unservedDT, unservedSG, grid, driverStatus
            result,                                # resultStatus
            0, 0, 0, 0,                            # pitLane timer fields
            0.0, 255,                              # speedTrap fastest speed/lap
        )
    body += struct.pack("<BB", 255, 255)           # time-trial car indices
    return header(fp.PACKET_LAP, frame) + body


# --- Pedal-input demo (the active car, index 0) -----------------------------
# Simulate a driver alternating pedals so the /inputs trace has something to
# show: press the throttle to a random level, hold it for a few seconds, lift
# off, then press the brake to a (different) random level, hold, lift off, and
# repeat. Each press/lift takes a ~1s ramp rather than snapping, and one pedal is
# fully released before the other goes down.
PEDAL_RAMP_S = 1.0     # transition time to press / release a pedal
PEDAL_HOLD_S = 3.0     # how long a pedal is held at its random level
PEDAL_PHASE_S = 2 * PEDAL_RAMP_S + PEDAL_HOLD_S   # one pedal: ramp up, hold, ramp down
PEDAL_CYCLE_S = 2 * PEDAL_PHASE_S                 # throttle phase, then brake phase


def _pedal_target(kind, idx):
    """Stable random level (0..1, i.e. 0–100%) for the `idx`-th press of `kind`
    ("t"/"b"). Seeded by the segment so it's constant for a whole hold (no
    per-tick flicker) yet differs each time the pedal is pressed."""
    return random.Random(f"pedal-{kind}-{idx}").random()


def _ramp_hold(x, target):
    """Trapezoid level over one PEDAL_PHASE_S window: ramp 0->target over RAMP,
    hold at target, then ramp back to 0 over the final RAMP."""
    if x < PEDAL_RAMP_S:
        return target * (x / PEDAL_RAMP_S)            # pressing down
    if x < PEDAL_RAMP_S + PEDAL_HOLD_S:
        return target                                 # held
    down = x - (PEDAL_RAMP_S + PEDAL_HOLD_S)
    return target * max(0.0, 1.0 - down / PEDAL_RAMP_S)  # lifting off


def pedal_inputs(t):
    """(throttle, brake) in 0..1 for the simulated driver at time t."""
    cycle = int(t // PEDAL_CYCLE_S)
    local = t % PEDAL_CYCLE_S
    if local < PEDAL_PHASE_S:                          # throttle half of the cycle
        return _ramp_hold(local, _pedal_target("t", cycle)), 0.0
    return 0.0, _ramp_hold(local - PEDAL_PHASE_S, _pedal_target("b", cycle))


def telemetry_packet(frame, t):
    body = b""
    throttle0, brake0 = pedal_inputs(t)   # the active car (index 0) alternates pedals
    for i in range(fp.NUM_CARS):
        speed = 280 + int(math.sin(t * 2 + i) * 40) if i < NUM_ACTIVE else 0
        drs = 1 if (i < NUM_ACTIVE and math.sin(t + i) > 0.5) else 0
        throttle, brake = (throttle0, brake0) if i == 0 else (1.0, 0.0)
        body += struct.pack(
            fp.CAR_TELEMETRY_FMT,
            speed, throttle, 0.0, brake, 0, 7, 11000, drs, 80, 0,
            *([0] * 4), *([90] * 4), *([95] * 4), 100,
            *([23.0] * 4), *([0] * 4),
        )
    body += struct.pack("<BBb", 255, 255, 0)       # mfd panels + suggested gear
    return header(fp.PACKET_CAR_TELEMETRY, frame) + body


def status_packet(frame, t):
    compounds = [16, 17, 18]  # soft, medium, hard
    body = b""
    for i in range(fp.NUM_CARS):
        visual = compounds[i % 3] if i < NUM_ACTIVE else 0
        age = (int(t) // 3 + i) % 25 if i < NUM_ACTIVE else 0
        ers = PLAYER_ERS_J if i == 0 else 2_000_000.0  # player has enough to show
        # Active car (0): cycle the ERS mode through none/medium/hotlap/overtake
        # and drift the brake bias, so the /inputs pills visibly change.
        if i == 0:
            ers_mode = int(t // 4) % 4
            brake_bias = int(54 + math.sin(t * 0.15) * 4)   # ~50–58 %, live
        else:
            ers_mode, brake_bias = 1, 50
        body += struct.pack(
            fp.CAR_STATUS_FMT,
            2, 1, 1, brake_bias, 0, 100.0, 110.0, 18.0, 13000, 4000,
            8, 1, 0, visual, visual, age, 0,
            0.0, 0.0, ers, ers_mode, 0.0, 0.0, 0.0, 0,
        )
    return header(fp.PACKET_CAR_STATUS, frame) + body


def setups_packet(frame, t):
    body = b""
    for i in range(fp.NUM_CARS):
        # Active car (0) drifts its on-throttle differential so the DIFF pill
        # moves; everyone else sits fully locked.
        on_throttle = int(85 + math.sin(t * 0.1) * 15) if i == 0 else 100
        body += struct.pack(
            fp.CAR_SETUP_FMT,
            20, 25, on_throttle, 50,           # frontWing, rearWing, onThrottle, offThrottle
            0.0, 0.0, 0.0, 0.0,                # cambers, toes
            5, 5, 5, 5, 3, 3, 100, 54, 50,     # susp/ARB/heights, brakePressure, brakeBias, engineBraking
            23.0, 23.0, 23.0, 23.0,            # tyre pressures
            0,                                 # ballast
            100.0,                             # fuelLoad
        )
    body += struct.pack("<f", 0.0)             # m_nextFrontWingValue (player only)
    return header(fp.PACKET_CAR_SETUPS, frame) + body


def _lap_entry(lap_time, sectors, valid=0x0F):
    s1, s2, s3 = sectors
    return struct.pack(fp.LAP_HISTORY_FMT, lap_time,
                       s1 % 60000, s1 // 60000, s2 % 60000, s2 // 60000,
                       s3 % 60000, s3 // 60000, valid)


def session_history_packet(frame, car_idx, t, quali=False):
    stints = struct.pack(fp.TYRE_STINT_FMT, 3, 11, 17)      # medium, ended lap 3
    stints += struct.pack(fp.TYRE_STINT_FMT, 255, 16, 16)   # soft, current
    stints += b"\x00" * (fp.TYRE_STINT_SIZE * (fp.MAX_TYRE_STINTS - 2))

    if quali:
        lap_num, _, sector = flying_lap(t)
        if car_idx in NO_TIME_CARS:
            best_lap, sectors, lap_time = 0, (0, 0, 0), 0
            best_sector_laps = (0, 0, 0)
        else:
            if car_idx == 0:
                sectors, lap_time = PLAYER_SECTORS, sum(PLAYER_SECTORS)
                # Point each sector's "best" at the lap it was last completed on,
                # so it matches the lap the panel is currently showing (including
                # the brief window after the line where the finished lap holds).
                best_sector_laps = (
                    lap_num if sector >= 1 else lap_num - 1,
                    lap_num if sector >= 2 else lap_num - 1,
                    lap_num - 1,
                )
            elif car_idx == BEST_S2_CAR:
                sectors, lap_time = BEST_S2_SECTORS, sum(BEST_S2_SECTORS)
                best_sector_laps = (lap_num - 1,) * 3
            else:
                off = 800 + (car_idx * 53) % 1500   # always slower than car 0 / car 8
                sectors = (30000 + off, 31000 + off, 32000 + off)
                lap_time = sum(sectors)
                best_sector_laps = (lap_num - 1,) * 3
            best_lap = lap_num - 1
        num_laps = min(lap_num, fp.NUM_LAPS_IN_HISTORY)
        head = struct.pack("<7B", car_idx, num_laps, 2, best_lap, *best_sector_laps)
        # Every lap entry carries the same splits, so any best-lap lookup resolves.
        laps = _lap_entry(lap_time, sectors) * fp.NUM_LAPS_IN_HISTORY
        return header(fp.PACKET_SESSION_HISTORY, frame) + head + laps + stints

    # Race / other: steady-state history (fastest lap on lap 5, scattered times).
    best_lap = 0 if car_idx in NO_TIME_CARS else 5
    best_time_ms = 90000 + (car_idx * 37) % 41 * 100
    head = struct.pack("<7B", car_idx, 6, 2, best_lap, best_lap, best_lap, best_lap)
    laps = b"".join(
        _lap_entry(best_time_ms if (ln + 1) == best_lap else 0, (0, 0, 0), valid=1)
        for ln in range(fp.NUM_LAPS_IN_HISTORY)
    )
    return header(fp.PACKET_SESSION_HISTORY, frame) + head + laps + stints


def main(session_type=SESSION_MODES["race"]):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    name = fp.session_type_name(session_type)
    print(f"Sending mock F1 25 telemetry to {HOST}:{PORT} as {name} (Ctrl+C to stop)")
    quali = fp.session_info_kind(session_type) == "quali"
    start = time.monotonic()
    frame = 0
    prev_sc = 0
    while True:
        t = time.monotonic() - start
        frame += 1
        yellow, safety_car = flag_demo(t)
        # When a safety car (full/VSC) just ended, fire a "Resume Race" event.
        if prev_sc != 0 and safety_car == 0:
            sock.sendto(scar_event(frame, prev_sc, 3), (HOST, PORT))
        prev_sc = safety_car
        # Lower-frequency packets every ~1s; lap+telemetry+status every tick.
        if frame % 20 == 1:
            sock.sendto(participants_packet(frame), (HOST, PORT))
            sock.sendto(session_packet(frame, session_type, yellow, safety_car), (HOST, PORT))
            sock.sendto(setups_packet(frame, t), (HOST, PORT))
        sock.sendto(lap_packet(frame, t, quali), (HOST, PORT))
        sock.sendto(telemetry_packet(frame, t), (HOST, PORT))
        sock.sendto(status_packet(frame, t), (HOST, PORT))
        # Session history is one car per packet. The active car (0) goes out every
        # tick so its sector colours stay crisp; the rest cycle through the grid
        # (~1s for all 20), like the game does.
        sock.sendto(session_history_packet(frame, 0, t, quali), (HOST, PORT))
        other = frame % NUM_ACTIVE
        if other != 0:
            sock.sendto(session_history_packet(frame, other, t, quali), (HOST, PORT))
        time.sleep(0.05)  # 20 Hz


def replay_file(sock, path):
    """Replay a recorder.py capture to (HOST, PORT) forever, reproducing the
    original inter-packet timing. Loops back to the start at end-of-file."""
    while True:
        sent = 0
        with open(path, "rb") as f:
            wall_start = time.monotonic()
            t0 = None
            while True:
                head = f.read(RECORD_HEADER_SIZE)
                if len(head) < RECORD_HEADER_SIZE:
                    break  # clean EOF -> loop
                t, n = struct.unpack(RECORD_FMT, head)
                data = f.read(n)
                if len(data) < n:
                    break  # truncated trailing record -> loop
                if t0 is None:
                    t0 = t  # first packet defines the timeline origin
                delay = (wall_start + (t - t0)) - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                sock.sendto(data, (HOST, PORT))
                sent += 1
        if sent == 0:
            sys.exit(f"{path}: no telemetry records found (is this a recorder.py .f1rec file?)")
        print(f"  …looped ({sent} packets)")


def replay_main(path):
    if not os.path.exists(path):
        sys.exit(f"{path}: no such file")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Replaying {path} to {HOST}:{PORT} on loop (Ctrl+C to stop)")
    replay_file(sock, path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Emit synthetic F1 25 telemetry.")
    parser.add_argument(
        "--mode", choices=SESSION_MODES, default="race",
        help="session type to emulate (default: race)",
    )
    parser.add_argument(
        "--from-file", metavar="PATH",
        help="replay a recorder.py capture (recordings/*.f1rec) on loop, "
             "ignoring --mode and the synthetic data",
    )
    args = parser.parse_args()
    try:
        if args.from_file:
            replay_main(args.from_file)
        else:
            main(SESSION_MODES[args.mode])
    except KeyboardInterrupt:
        pass
