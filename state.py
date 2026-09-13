"""Live session state, merged from the 2026-format UDP packet stream.

Different packet types arrive independently (telemetry ~60Hz, lap data, status,
participants once or twice a second). We keep the latest of each per car and
build the info overlay's snapshot on demand for the web client.
"""

import sys

import f1_packets as fp
import info


class GameState:
    def __init__(self):
        self.player_car_index = 0
        self.session = {}
        # Per-car latest packet fragments, indexed by car index (0..23).
        self.participants = [None] * fp.NUM_CARS
        self.lap = [None] * fp.NUM_CARS
        self.telemetry2 = [None] * fp.NUM_CARS   # overtake mode (Car Telemetry 2)
        self.status = [None] * fp.NUM_CARS
        # Car-damage fragments (tyre wear + front wing) for the info overlay.
        self.damage = [None] * fp.NUM_CARS
        # Info overlay: trackers + server-side page state (driven by UDP actions).
        self.info = info.InfoState()
        # Packet formats already warned about, so a mis-set game logs once.
        self._warned_formats = set()

    def update(self, data):
        """Feed one raw UDP datagram in. Unknown/short packets are ignored."""
        if len(data) < fp.HEADER_SIZE:
            return
        try:
            header = fp.parse_header(data)
        except Exception:
            return
        # Reject other formats outright: packet sizes overlap between years, so a
        # game set to UDP Format 2025 would otherwise parse as plausible garbage.
        fmt = header["packet_format"]
        if fmt != fp.PACKET_FORMAT:
            if fmt not in self._warned_formats:
                self._warned_formats.add(fmt)
                sys.stderr.write(
                    f"Ignoring UDP packets in format {fmt}; set the game's "
                    f"UDP Format to {fp.PACKET_FORMAT}\n"
                )
            return
        pid = header["packet_id"]
        self.player_car_index = header["player_car_index"]

        try:
            if pid == fp.PACKET_PARTICIPANTS:
                self.participants = fp.parse_participants(data)
            elif pid == fp.PACKET_LAP:
                self.lap = fp.parse_lap(data)
                self.info.observe(self)   # feed the info trackers per lap packet
            elif pid == fp.PACKET_CAR_TELEMETRY2:
                self.telemetry2 = fp.parse_car_telemetry2(data)
            elif pid == fp.PACKET_CAR_STATUS:
                self.status = fp.parse_car_status(data)
            elif pid == fp.PACKET_CAR_DAMAGE:
                self.damage = fp.parse_car_damage(data)
            elif pid == fp.PACKET_EVENT:
                ev = fp.parse_event(data)
                if ev.get("code") == "BUTN":
                    self.info.on_button(ev.get("buttons", 0))   # info page switch
            elif pid == fp.PACKET_SESSION:
                self.session = fp.parse_session(data)
        except Exception:
            # A malformed packet shouldn't take the server down.
            return

    def snapshot(self):
        """Build the JSON-serialisable view pushed to the browser."""
        # The "active" car: the spectated car while spectating, otherwise the
        # player's own car (the header's m_playerCarIndex, which is meaningless
        # during spectating). 255 = none.
        if self.session.get("is_spectating"):
            active_idx = self.session.get("spectator_car_index", 255)
        else:
            active_idx = self.player_car_index
        return {
            "info": self.info.build(self, active_idx),  # driver-info pages + header
        }
