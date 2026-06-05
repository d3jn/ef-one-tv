"""Live session state, merged from the F1 25 packet stream.

Different packet types arrive independently (telemetry ~60Hz, lap data, status,
participants once or twice a second). We keep the latest of each per car and
build a single sorted "broadcast view" on demand for the web client.
"""

import f1_packets as fp


def _fmt_lap_time(ms):
    """Milliseconds → "M:SS.mmm" (or "" when no time set)."""
    if not ms:
        return ""
    minutes, rem = divmod(ms, 60000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _fmt_gap(ms):
    """Milliseconds → "+S.mmm" for the timing tower (blank when zero)."""
    if not ms:
        return ""
    return f"+{ms / 1000:.3f}"


class GameState:
    def __init__(self):
        self.player_car_index = 0
        self.session = {
            "track_name": "—",
            "session_type_name": "—",
            "total_laps": 0,
            "session_type": 0,
            "session_time_left": 0,
        }
        # Per-car latest packet fragments, indexed by car index (0..21).
        self.participants = [None] * fp.NUM_CARS
        self.lap = [None] * fp.NUM_CARS
        self.telemetry = [None] * fp.NUM_CARS
        self.status = [None] * fp.NUM_CARS
        self.num_active_cars = 0

    def update(self, data):
        """Feed one raw UDP datagram in. Unknown/short packets are ignored."""
        if len(data) < fp.HEADER_SIZE:
            return
        try:
            header = fp.parse_header(data)
        except Exception:
            return
        pid = header["packet_id"]
        self.player_car_index = header["player_car_index"]

        try:
            if pid == fp.PACKET_PARTICIPANTS:
                self.participants = fp.parse_participants(data)
                self.num_active_cars = fp.parse_num_active_cars(data)
            elif pid == fp.PACKET_LAP:
                self.lap = fp.parse_lap(data)
            elif pid == fp.PACKET_CAR_TELEMETRY:
                self.telemetry = fp.parse_car_telemetry(data)
            elif pid == fp.PACKET_CAR_STATUS:
                self.status = fp.parse_car_status(data)
            elif pid == fp.PACKET_SESSION:
                self.session = fp.parse_session(data)
        except Exception:
            # A malformed packet shouldn't take the server down.
            return

    def snapshot(self):
        """Build the JSON-serialisable broadcast view, sorted by position."""
        rows = []
        leader_lap = max(
            (l["lap_num"] for l in self.lap if l and l["position"] > 0),
            default=0,
        )
        for idx in range(fp.NUM_CARS):
            lap = self.lap[idx]
            if not lap or lap["position"] <= 0:
                continue
            part = self.participants[idx] or {}
            stat = self.status[idx] or {}
            tele = self.telemetry[idx] or {}

            team_id = part.get("team_id")
            team_name, team_colour = fp.team_info(team_id)
            tyre_label, tyre_colour = fp.tyre_info(stat.get("visual_tyre"))
            name = part.get("name") or ""
            code = _driver_code(name, part.get("race_number"))
            status_label = fp.result_label(lap["result_status"])  # None if racing

            rows.append({
                "carIndex": idx,
                "position": lap["position"],
                "name": name or code,
                "code": code,
                "raceNumber": part.get("race_number", 0),
                "team": team_name,
                "teamColour": team_colour,
                "gapToLeader": _fmt_gap(lap["gap_to_leader_ms"]),
                "interval": _fmt_gap(lap["interval_to_front_ms"]),
                "lastLap": _fmt_lap_time(lap["last_lap_ms"]),
                "lapNum": lap["lap_num"],
                "pitting": lap["pit_status"] != 0,
                "statusLabel": status_label,        # "DNF"/"DSQ"/"DNS"/"NC" or None
                "retired": status_label is not None,  # greys out the row
                "tyre": tyre_label,
                "tyreColour": tyre_colour,
                "tyreAge": stat.get("tyre_age_laps", 0),
                "drs": bool(tele.get("drs", 0)),
                "speed": tele.get("speed", 0),
                "isPlayer": idx == self.player_car_index,
            })

        rows.sort(key=lambda r: r["position"])
        return {
            "session": {
                "track": self.session.get("track_name", "—"),
                "type": self.session.get("session_type_name", "—"),
                "totalLaps": self.session.get("total_laps", 0),
                "currentLap": leader_lap,
                "timeLeft": self.session.get("session_time_left", 0),
                "infoKind": fp.session_info_kind(self.session.get("session_type", 0)),
            },
            "cars": rows,
        }


def _driver_code(name, race_number):
    """A 3-letter broadcast code from the driver name (fallback: car number)."""
    parts = [p for p in name.replace("_", " ").split() if p]
    if parts:
        return parts[-1][:3].upper()
    if race_number:
        return f"#{race_number}"
    return "—"
