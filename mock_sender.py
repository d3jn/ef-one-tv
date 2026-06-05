"""Emit synthetic F1 25 UDP packets so you can see the graphics move without
the game. It builds REAL packets in the F1 25 wire format (parsed by the same
f1_packets.py the server uses) — a 20-car grid that races, swaps places, pits,
and toggles DRS.

Run the server in one terminal, then:  python mock_sender.py
"""

import math
import socket
import struct
import time

import config
import f1_packets as fp

# Send to localhost on the same UDP port the server listens on (settings.json).
HOST, PORT = "127.0.0.1", config.UDP_PORT
NUM_ACTIVE = 20

# Car index → resultStatus, to demo out-of-race labels (see lap_packet).
# 1=inactive(DNS), 5=disqualified(DSQ), 4=didnotfinish(DNF), 7=retired(DNF).
OUT_STATES = {16: 1, 17: 5, 18: 4, 19: 7}

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


def session_packet(frame):
    # weather, trackTemp, airTemp, totalLaps, trackLength, sessionType=10(Race),
    # trackId=10(Spa), then enough trailing fields to satisfy SESSION_PRE_FMT.
    body = struct.pack(
        fp.SESSION_PRE_FMT,
        1, 30, 24, 44, 7004, 10, 10, 0, 3600, 3600, 80, 0, 0, 0, 0, 0,
    )
    return header(fp.PACKET_SESSION, frame) + body


def lap_packet(frame, t):
    body = b""
    for i in range(fp.NUM_CARS):
        if i < NUM_ACTIVE:
            # Positions shuffle slowly so rows visibly reorder on the tower.
            wobble = math.sin(t * 0.25 + i) * 1.5
            position = max(1, min(NUM_ACTIVE, round(i + 1 + wobble)))
            # Leader has no gap to itself; others are always positive deltas.
            gap_ms = 0 if i == 0 else max(0, int(i * 1100 + math.sin(t + i) * 400))
            interval_ms = 0 if i == 0 else int(900 + math.sin(t * 0.7 + i) * 350)
            last_lap_ms = int(92000 + i * 120 + math.sin(t * 0.1 + i) * 300)
            lap_num = 12
            pit = 1 if (i == 7 and int(t) % 40 < 4) else 0
            # A few cars out of the race, to exercise the status labels:
            # resultStatus 1=inactive(DNS), 4=DNF, 5=DSQ, 7=retired(DNF).
            result = OUT_STATES.get(i, 2)
            pen_sec, drive_through = PENALTIES.get(i, (0, 0))
        else:
            position = 0
            gap_ms = interval_ms = last_lap_ms = lap_num = pit = result = 0
            pen_sec = drive_through = 0

        gap_min, gap_rem = divmod(gap_ms, 60000)
        int_min, int_rem = divmod(interval_ms, 60000)
        body += struct.pack(
            fp.LAP_DATA_FMT,
            last_lap_ms, int(t * 1000) % 95000,   # lastLap, currentLap
            0, 0, 0, 0,                            # sector 1/2 split times
            int_rem, int_min,                      # delta to car in front
            gap_rem, gap_min,                      # delta to race leader
            i * 250.0, i * 250.0, 0.0,             # lapDistance, totalDistance, scDelta
            position, lap_num, pit, 1, 0, 0,       # pos, lapNum, pit, numStops, sector, invalid
            pen_sec, 0, 0,                         # penalties, totalWarn, cornerCutWarn
            drive_through, 0, 0, 0,                # unservedDT, unservedSG, grid, driverStatus
            result,                                # resultStatus
            0, 0, 0, 0,                            # pitLane timer fields
            0.0, 255,                              # speedTrap fastest speed/lap
        )
    body += struct.pack("<BB", 255, 255)           # time-trial car indices
    return header(fp.PACKET_LAP, frame) + body


def telemetry_packet(frame, t):
    body = b""
    for i in range(fp.NUM_CARS):
        speed = 280 + int(math.sin(t * 2 + i) * 40) if i < NUM_ACTIVE else 0
        drs = 1 if (i < NUM_ACTIVE and math.sin(t + i) > 0.5) else 0
        body += struct.pack(
            fp.CAR_TELEMETRY_FMT,
            speed, 1.0, 0.0, 0.0, 0, 7, 11000, drs, 80, 0,
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
        body += struct.pack(
            fp.CAR_STATUS_FMT,
            2, 1, 1, 50, 0, 100.0, 110.0, 18.0, 13000, 4000,
            8, 1, 0, visual, visual, age, 0,
            0.0, 0.0, 2_000_000.0, 1, 0.0, 0.0, 0.0, 0,
        )
    return header(fp.PACKET_CAR_STATUS, frame) + body


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"Sending mock F1 25 telemetry to {HOST}:{PORT} (Ctrl+C to stop)")
    start = time.monotonic()
    frame = 0
    while True:
        t = time.monotonic() - start
        frame += 1
        # Lower-frequency packets every ~1s; lap+telemetry+status every tick.
        if frame % 20 == 1:
            sock.sendto(participants_packet(frame), (HOST, PORT))
            sock.sendto(session_packet(frame), (HOST, PORT))
        sock.sendto(lap_packet(frame, t), (HOST, PORT))
        sock.sendto(telemetry_packet(frame, t), (HOST, PORT))
        sock.sendto(status_packet(frame, t), (HOST, PORT))
        time.sleep(0.05)  # 20 Hz


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
