"""Emit synthetic F1 UDP packets so you can see the info overlay move without
the game. It builds REAL packets in the 2026 UDP wire format (parsed by the same
f1_packets.py the server uses) — a 22-car 2026 grid that swaps places, pits,
carries penalties, wears its tyres, and toggles Overtake Mode.

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
NUM_ACTIVE = 22   # 11 teams x 2 in 2026 (packets still carry fp.NUM_CARS slots)

# --mode name → session type id sent in the session packet. Add more here to
# emulate other session types (ids per f1_packets.SESSION_TYPES).
SESSION_MODES = {
    "race": 15,      # Race
    "quali": 5,      # Qualifying (Q1)
    "practice": 1,   # Practice (P1)
}

# Car index → resultStatus, to demo cars out of the race (they drop out of the
# info standings). 1=inactive, 4=didnotfinish, 5=disqualified, 7=retired.
OUT_STATES = {15: 4, 16: 1, 17: 5, 18: 4, 19: 7}

# Car index → (time-penalty seconds, unserved drive-through count), to demo the
# info standings' penalty column: "+5s", "DT", "+3s DT", "+13s".
PENALTIES = {2: (5, 0), 3: (0, 1), 4: (3, 1), 9: (13, 0)}

# Car index → (online handle, race number) for human (multiplayer) players.
# Everyone else is an AI bot. Single-name handles (no first/last) on purpose,
# to exercise the name-swap overrides in driver_names.json.
HUMANS = {
    0: ("Cool Racer 7", 99),   # no override
    1: ("lando_gamer", 4),     # override by source_name -> "LANDO"
    2: ("ScuderiaFan", 16),    # override by source_number 16 -> "CHARLES"
}

# 2026 team ids (appendix "Team IDs"): the '26 cars are 476-486.
MERCEDES, FERRARI, RED_BULL, WILLIAMS, ASTON_MARTIN, ALPINE = 476, 477, 478, 479, 480, 481
RB, HAAS, MCLAREN, AUDI, CADILLAC = 482, 483, 484, 485, 486

# (name, race number, team id) per car index. Mock data — numbers illustrative.
DRIVERS = [
    ("M VERSTAPPEN", 1, RED_BULL), ("L NORRIS", 4, MCLAREN), ("C LECLERC", 16, FERRARI),
    ("O PIASTRI", 81, MCLAREN), ("C SAINZ", 55, WILLIAMS), ("G RUSSELL", 63, MERCEDES),
    ("L HAMILTON", 44, FERRARI), ("I HADJAR", 6, RED_BULL), ("F ALONSO", 14, ASTON_MARTIN),
    ("L STROLL", 18, ASTON_MARTIN), ("P GASLY", 10, ALPINE), ("F COLAPINTO", 43, ALPINE),
    ("A ALBON", 23, WILLIAMS), ("L LAWSON", 30, RB), ("A LINDBLAD", 41, RB),
    ("N HULKENBERG", 27, AUDI), ("G BORTOLETO", 5, AUDI), ("E OCON", 31, HAAS),
    ("O BEARMAN", 87, HAAS), ("K ANTONELLI", 12, MERCEDES), ("S PEREZ", 11, CADILLAC),
    ("V BOTTAS", 77, CADILLAC),
]

PLAYER_ERS_J = 3_200_000.0   # car 0 (the player) at 80% battery


def header(packet_id, frame):
    # packetFormat=2026, gameYear=26, major=1, minor=0, packetVersion=1
    return struct.pack(
        fp.HEADER_FMT, fp.PACKET_FORMAT, 26, 1, 0, 1, packet_id,
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
            ai, driver_id = 0, 65535             # 65535 = network human
        else:
            ai, driver_id = 1, i
        body += struct.pack(
            fp.PARTICIPANT_DATA_FMT,
            ai, driver_id, 0, team,               # ai, driverId, networkId, teamId (H)
            0, number, 0,                         # myTeam, raceNumber, nationality
            name.encode("utf-8")[:31],            # 32s name (null-padded by pack)
            1, 1,                                 # yourTelemetry, showOnlineNames
            0,                                    # techLevel
            1, 0,                                 # platform, numColours
            *([0] * 12),                          # livery colours
        )
    return header(fp.PACKET_PARTICIPANTS, frame) + body


def session_packet(frame, session_type=15, safety_car=0):
    # weather, trackTemp, airTemp, totalLaps, trackLength, sessionType (15=Race,
    # 5=Q1, …), trackId=10(Spa), …, numMarshalZones=21. Followed by the 21
    # MarshalZones (all flag-free) and m_safetyCarStatus.
    pre = struct.pack(
        fp.SESSION_PRE_FMT,
        1, 30, 24, 44, 7004, session_type, 10, 0, 3600, 3600, 80, 0, 0, 0, 0, 21,
    )
    zones = b"".join(
        struct.pack(fp.MARSHAL_ZONE_FMT, i / fp.MAX_MARSHAL_ZONES, 0)
        for i in range(fp.MAX_MARSHAL_ZONES)
    )
    # safetyCarStatus, networkGame, numWeatherForecastSamples, then the samples.
    tail = struct.pack("<BBB", safety_car, 1, 4)
    for off, wx, rain in [(0, 1, 0), (5, 2, 10), (15, 3, 45), (30, 4, 70)]:
        tail += struct.pack(fp.WEATHER_SAMPLE_FMT, session_type, off, wx, 30, 2, 24, 2, rain)
    packet = header(fp.PACKET_SESSION, frame) + pre + zones + tail
    # Zero-fill the rest of the fixed 64-sample array and the trailing settings /
    # active-aero block, so the datagram has the real 926-byte size.
    return packet + bytes(fp.SESSION_PACKET_SIZE - len(packet))


def car_damage_packet(frame, t):
    """Tyre wear (grows with tyre age) + front-wing damage on car 4, for the
    info overlay's wear columns and FW indicators."""
    body = b""
    for i in range(fp.NUM_CARS):
        if i < NUM_ACTIVE:
            age = (int(t) // 3 + i) % 25
            base = min(78.0, 6.0 + age * 2.0 + i * 0.5)
            wear = [base, base - 1, base - 2, base - 3]
            dmg = [0] * 30
            if i == 4:
                dmg[12], dmg[13] = 28, 5   # FL/FR wing (overall indices 16/17)
        else:
            wear = [0.0] * 4
            dmg = [0] * 30
        body += struct.pack(fp.CAR_DAMAGE_FMT, *wear, *dmg)
    return header(fp.PACKET_CAR_DAMAGE, frame) + body


def butn_event(frame, mask):
    """A BUTN event carrying a 32-bit button bitmask (UDP Actions)."""
    return header(fp.PACKET_EVENT, frame) + b"BUTN" + struct.pack("<I", mask)


def safety_car_demo(t):
    """Cycle the safety-car status for the pit projection's SC pit loss:
    none → VSC → full SC → none."""
    phase = int(t) % 32
    if phase < 14:  return 0   # normal racing
    if phase < 20:  return 2   # VSC
    if phase < 28:  return 1   # full SC
    return 0                   # racing resumes


def race_order(t):
    """Active cars ranked into a clean position permutation (1..NUM_ACTIVE) by a
    slowly-wobbling per-car score, so positions stay a true ordering (cars swap,
    none share or gap). The player (car 0) is biased to mid-pack so it always has
    cars both ahead and behind — for the info header, pit projection, and the
    ±5 standings window. Returns {car_idx: position}."""
    def score(i):
        base = NUM_ACTIVE / 2 if i == 0 else i
        return base + math.sin(t * 0.25 + i) * 1.5
    order = sorted(range(NUM_ACTIVE), key=lambda i: (score(i), i))
    return {idx: pos + 1 for pos, idx in enumerate(order)}


def ahead_behind(t):
    """(ahead_idx, behind_idx) relative to the player (car 0), by race position."""
    pos = race_order(t)
    by_pos = {p: idx for idx, p in pos.items()}
    return by_pos.get(pos[0] - 1), by_pos.get(pos[0] + 1)


def lap_packet(frame, t):
    body = b""
    cur_lap_ms = int(t * 1000) % 95000
    order = race_order(t)   # clean position permutation
    for i in range(fp.NUM_CARS):
        if i < NUM_ACTIVE:
            # Positions shuffle slowly; gaps grow with position (monotonic) so the
            # leader is 0 and the info header's ahead/behind deltas carry the
            # right sign.
            position = order[i]
            step = 1100 + math.sin(t * 0.3) * 150
            gap_ms = int((position - 1) * step)
            last_lap_ms = int(92000 + i * 120 + math.sin(t * 0.1 + i) * 300)
            lap_num = 12
            pit = 1 if (i == 7 and int(t) % 40 < 4) else 0
            result = OUT_STATES.get(i, 2)
            pen_sec, drive_through = PENALTIES.get(i, (0, 0))
        else:
            position = lap_num = pit = result = 0
            gap_ms = last_lap_ms = 0
            pen_sec = drive_through = 0

        gap_min, gap_rem = divmod(gap_ms, 60000)
        body += struct.pack(
            fp.LAP_DATA_FMT,
            last_lap_ms, cur_lap_ms,               # lastLap, currentLap
            0, 0,                                  # sector 1 split (msPart, minPart)
            0, 0,                                  # sector 2 split (msPart, minPart)
            0, 0,                                  # delta to car in front
            gap_rem, gap_min,                      # delta to race leader
            i * 250.0, i * 250.0, 0.0,             # lapDistance, totalDistance, scDelta
            position, lap_num, pit, 1, 0, 0,       # pos, lapNum, pit, numStops, sector, invalid
            pen_sec, 0, 0,                         # penalties, totalWarn, cornerCutWarn
            drive_through, 0, 0, 4,                # unservedDT, unservedSG, grid, driverStatus
            result,                                # resultStatus
            0, 0, 0, 0,                            # pitLane timer fields
            0.0, 255,                              # speedTrap fastest speed/lap
        )
    body += struct.pack("<BB", 255, 255)           # time-trial car indices
    return header(fp.PACKET_LAP, frame) + body


def telemetry2_packet(frame, t):
    """Car Telemetry 2: every car runs 2026 regulations; Overtake Mode becomes
    available and is engaged on a per-car wobble, so the info header's OVR pill
    toggles for the cars ahead/behind."""
    body = b""
    for i in range(fp.NUM_CARS):
        active_car = i < NUM_ACTIVE
        wave = math.sin(t + i)
        ot_available = 1 if (active_car and wave > 0.0) else 0
        ot_active = 1 if (active_car and wave > 0.5) else 0
        straight = 1 if (active_car and math.sin(t * 0.5 + i) > 0.0) else 0
        body += struct.pack(
            fp.CAR_TELEMETRY2_FMT,
            straight, straight, 0,                 # activeAero mode, available, activation dist
            ot_available, ot_active, 0,            # overtake available, active, activation dist
            1 if active_car else 0, 0,             # 2026Regulations, drivingWrongWay
        )
    return header(fp.PACKET_CAR_TELEMETRY2, frame) + body


def status_packet(frame, t):
    compounds = [16, 17, 18]  # soft, medium, hard
    # The cars directly ahead of / behind the player get distinct random ERS
    # modes, re-rolled every 2s, so the info header's ERS column visibly changes.
    ahead_idx, behind_idx = ahead_behind(t)
    seg = int(t // 2)
    ahead_mode = random.Random(f"ers-ahead-{seg}").randrange(4)
    behind_mode = random.Random(f"ers-behind-{seg}").randrange(4)
    if behind_mode == ahead_mode:
        behind_mode = (behind_mode + 1) % 4   # keep ahead and behind different
    body = b""
    for i in range(fp.NUM_CARS):
        visual = compounds[i % 3] if i < NUM_ACTIVE else 0
        age = (int(t) // 3 + i) % 25 if i < NUM_ACTIVE else 0
        ers = PLAYER_ERS_J if i == 0 else 2_000_000.0
        # Player (0): cycle the ERS mode through none/medium/hotlap/boost so
        # the info ERS row visibly changes.
        if i == 0:
            ers_mode = int(t // 4) % 4
        elif i == ahead_idx:
            ers_mode = ahead_mode
        elif i == behind_idx:
            ers_mode = behind_mode
        else:
            ers_mode = 1
        body += struct.pack(
            fp.CAR_STATUS_FMT,
            2, 1, 1, 50, 0, 100.0, 110.0, 18.0, 13000, 4000,
            8, 0, 0, visual, visual, age, 0,       # maxGears, drsAllowed (0: 2026 car), …
            0.0, 0.0, ers, ers_mode,
            0.0, 0.0, 0.0, 0.0,                    # harvested MGU-K/H, harvest limit, deployed
            0,                                     # networkPaused
        )
    return header(fp.PACKET_CAR_STATUS, frame) + body


def main(session_type=SESSION_MODES["race"]):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    name = fp.session_type_name(session_type)
    print(f"Sending mock {fp.PACKET_FORMAT}-format telemetry to {HOST}:{PORT} as {name} (Ctrl+C to stop)")
    start = time.monotonic()
    frame = 0
    while True:
        t = time.monotonic() - start
        frame += 1
        # Lower-frequency packets every ~1s; lap+telemetry+status every tick.
        if frame % 20 == 1:
            sock.sendto(participants_packet(frame), (HOST, PORT))
            sock.sendto(session_packet(frame, session_type, safety_car_demo(t)), (HOST, PORT))
        sock.sendto(lap_packet(frame, t), (HOST, PORT))
        sock.sendto(telemetry2_packet(frame, t), (HOST, PORT))
        sock.sendto(status_packet(frame, t), (HOST, PORT))
        sock.sendto(car_damage_packet(frame, t), (HOST, PORT))
        # Cycle the info overlay's pages every ~8s via a UDP Action 1 edge
        # (press one tick, release the next) so page switching is demoable.
        if frame % 160 == 0:
            sock.sendto(butn_event(frame, fp.UDP_ACTION_1_MASK), (HOST, PORT))
        elif frame % 160 == 5:
            sock.sendto(butn_event(frame, 0), (HOST, PORT))
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
    parser = argparse.ArgumentParser(description="Emit synthetic 2026-format F1 telemetry.")
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
