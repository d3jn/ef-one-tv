"""Live session state, merged from the F1 25 packet stream.

Different packet types arrive independently (telemetry ~60Hz, lap data, status,
participants once or twice a second). We keep the latest of each per car and
build a single sorted "broadcast view" on demand for the web client.
"""

import time

import config
import f1_packets as fp

GREEN_FLAG_SECONDS = 3.0  # how long "GREEN FLAG" stays after a resume-race event

# --- Sector-panel visibility rules (quali) ---
ERS_SHOW_THRESHOLD = 50.0     # % battery required at lap start to show the panel
PANEL_DELTA_HIDE_MS = 2000    # hide if a crossing is >2s slower than the fastest lap
PANEL_FLASH_HOLD = 2.2        # s to keep the panel alive after a shown lap's last
                              # crossing, so the delta flash (2s) always completes
SECTOR_RESET_HOLD = 2.5       # s to hold a finished lap's coloured sectors before
                              # resetting them to gray for the new lap (which then
                              # lights its sectors only as they're crossed)


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
            "marshal_yellow": False,
            "safety_car_status": 0,
        }
        # Monotonic deadline until which "GREEN FLAG" shows after a resume event.
        self.resume_racing_until = 0.0
        # Per-car latest packet fragments, indexed by car index (0..21).
        self.participants = [None] * fp.NUM_CARS
        self.lap = [None] * fp.NUM_CARS
        self.telemetry = [None] * fp.NUM_CARS
        self.status = [None] * fp.NUM_CARS
        self.setups = [None] * fp.NUM_CARS
        self.history = [None] * fp.NUM_CARS  # session-history (per car), for best-lap tyre
        # Which sectors of the lap currently shown in the sector panel are done,
        # per car: {"lap": int, "done": [s1, s2, s3]}. Advanced from live lap
        # data (see _update_sector_view) so S3 can light at the line.
        self.sector_view = [None] * fp.NUM_CARS
        # Per-car monotonic deadline to hold a just-finished lap's coloured
        # sectors before the panel resets to gray for the new lap. 0 = no hold.
        self.sector_hold_until = [0.0] * fp.NUM_CARS
        # Per-car (lap, sector) last seen, to detect S1/S2/line crossings, and
        # the most recent crossing's split (for the panel's delta flash).
        self.prev_sector = [None] * fp.NUM_CARS
        self.delta_event = [None] * fp.NUM_CARS
        # Per-car sector-panel visibility (quali): whether it's shown, the lap it
        # was shown for, and a force-show deadline that lets a crossing's delta
        # flash finish even when the panel would otherwise hide.
        self.panel_shown = [False] * fp.NUM_CARS
        self.panel_shown_lap = [0] * fp.NUM_CARS
        self.panel_hold_until = [0.0] * fp.NUM_CARS
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
                self._update_sector_view()
            elif pid == fp.PACKET_CAR_TELEMETRY:
                self.telemetry = fp.parse_car_telemetry(data)
            elif pid == fp.PACKET_CAR_STATUS:
                self.status = fp.parse_car_status(data)
            elif pid == fp.PACKET_CAR_SETUPS:
                self.setups = fp.parse_car_setups(data)
            elif pid == fp.PACKET_SESSION_HISTORY:
                hist = fp.parse_session_history(data)  # one car per packet
                self.history[hist["car_idx"]] = hist
            elif pid == fp.PACKET_EVENT:
                ev = fp.parse_event(data)
                if ev.get("code") == "SCAR" and ev.get("safety_car_event") == 3:
                    self.resume_racing_until = time.monotonic() + GREEN_FLAG_SECONDS
            elif pid == fp.PACKET_SESSION:
                self.session = fp.parse_session(data)
        except Exception:
            # A malformed packet shouldn't take the server down.
            return

    def _flag_state(self):
        """Current session flag for the header indicator, or None. Priority
        (most important first): GREEN FLAG (resume window) > SC > VSC > yellow."""
        if time.monotonic() < self.resume_racing_until:
            return {"text": "GREEN FLAG", "kind": "green"}
        sc = self.session.get("safety_car_status", 0)
        if sc == 1:
            return {"text": "SC", "kind": "yellow"}
        if sc == 2:
            return {"text": "VSC", "kind": "yellow"}
        if self.session.get("marshal_yellow", False):
            return {"text": "YELLOW FLAG", "kind": "yellow"}
        return None

    def _update_sector_view(self):
        """Advance each car's sector-panel state from the latest lap data. The
        three boxes track the lap a car is currently running: S1 lights when it
        enters S2, S2 when it enters S3, and S3 at the line (lap completion).
        The completed row stays up until the car crosses S1 of the next lap,
        which then becomes the displayed lap (mirrors how a broadcast holds your
        just-set sectors as you start the next lap)."""
        for idx in range(fp.NUM_CARS):
            l = self.lap[idx]
            if not l or l["position"] <= 0:
                self.sector_view[idx] = None
                self.prev_sector[idx] = None
                self.panel_shown[idx] = False
                self.panel_hold_until[idx] = 0.0
                self.sector_hold_until[idx] = 0.0
                continue
            lap_num, sector = l["lap_num"], l["sector"]  # sector: 0=S1, 1=S2, 2=S3

            # Detect S1/S2/line crossings (for the delta flash + visibility) from
            # raw (lap, sector) transitions — independent of the display state.
            prev = self.prev_sector[idx]
            if prev is not None:
                plap, psec = prev
                if lap_num == plap and sector > psec:
                    if psec == 0 and sector >= 1:
                        self._record_delta(idx, l, lap_num, 0)   # crossed S1
                    if psec <= 1 and sector >= 2:
                        self._record_delta(idx, l, lap_num, 1)   # crossed S2
                elif lap_num == plap + 1:
                    self._record_delta(idx, l, plap, 2)          # crossed the line
                    self._eval_show(idx, l)                      # arm the new lap
                    if sector >= 1:                              # already into the new S1
                        self._record_delta(idx, l, lap_num, 0)
                elif lap_num != plap:
                    self.panel_shown[idx] = False                # jumped/flashback
            self.prev_sector[idx] = (lap_num, sector)

            # Continuous hides while shown: in the pits, on an out lap, or the lap
            # was invalidated. Sticky — stays hidden until the next lap start.
            if self.panel_shown[idx] and lap_num == self.panel_shown_lap[idx]:
                if (l["pit_status"] != 0
                        or l["driver_status"] == fp.DRIVER_STATUS_OUT_LAP
                        or l["lap_invalid"]):
                    self.panel_shown[idx] = False

            now = time.monotonic()
            sv = self.sector_view[idx]
            if sv is None:
                self.sector_view[idx] = {"lap": lap_num,
                                         "done": [sector >= 1, sector >= 2, False]}
            elif lap_num == sv["lap"]:
                # Still on the displayed lap: mark the sectors crossed so far.
                if sector >= 1:
                    sv["done"][0] = True
                if sector >= 2:
                    sv["done"][1] = True
            elif lap_num == sv["lap"] + 1:
                # The displayed lap just finished — light S3, then hold the full
                # coloured row for SECTOR_RESET_HOLD seconds (broadcast-style). On
                # expiry, switch to the new lap reset to gray, so its sectors light
                # only as they're crossed (not for the whole first sector as before).
                sv["done"][2] = True
                if self.sector_hold_until[idx] == 0.0:
                    self.sector_hold_until[idx] = now + SECTOR_RESET_HOLD
                if now >= self.sector_hold_until[idx]:
                    self.sector_view[idx] = {"lap": lap_num,
                                             "done": [sector >= 1, sector >= 2, False]}
                    self.sector_hold_until[idx] = 0.0
            else:
                # Jumped (missed laps, or a flashback): resync to the live lap.
                self.sector_view[idx] = {"lap": lap_num,
                                         "done": [sector >= 1, sector >= 2, False]}
                self.sector_hold_until[idx] = 0.0

    def _record_delta(self, idx, l, lap, boundary):
        """Capture the split a car just set at a crossing, for the panel's delta
        flash. boundary: 0=end of S1, 1=end of S2, 2=lap. We store the player's
        cumulative time to that point; the comparison to the fastest lap happens
        at snapshot time (the fastest lap can change between crossings). The
        `shown` flag records whether the panel was showing at the crossing, so we
        never flash a delta from a lap that wasn't on screen (e.g. an out lap).

        For a crossing on a shown lap we also (a) extend the force-show hold so
        its flash always completes, and (b) apply rule 4 — if the running time is
        already >2s off the fastest lap at this point, hide the panel."""
        if boundary == 0:
            player_ms = l["sector1_ms"]
        elif boundary == 1:
            player_ms = l["sector1_ms"] + l["sector2_ms"]
        else:
            player_ms = l["last_lap_ms"]
        if player_ms <= 0:
            return
        shown = self.panel_shown[idx]
        self.delta_event[idx] = {"lap": lap, "boundary": boundary,
                                 "player_ms": player_ms, "shown": shown}
        if not shown:
            return
        # Rule 4 (this lap's pace) applies only at the S1/S2 crossings — when
        # >2s off the fastest lap there, hide at once (no flash). At the line we
        # instead let the lap-delta flash play and re-evaluate the next lap.
        if boundary < 2:
            holder = self._fastest_lap_holder()
            if holder is not None:
                target = self._cumulative(self.history[holder])[boundary]
                if target > 0 and player_ms - target > PANEL_DELTA_HIDE_MS:
                    self.panel_shown[idx] = False
                    return
        # Otherwise keep the panel alive long enough for this crossing's flash.
        self.panel_hold_until[idx] = time.monotonic() + PANEL_FLASH_HOLD

    def _eval_show(self, idx, l):
        """At a lap start, decide whether to show the panel for the new lap:
        not an out lap, not in the pits, lap still valid, and ERS over 50%."""
        outlap = l["driver_status"] == fp.DRIVER_STATUS_OUT_LAP
        self.panel_shown[idx] = (not outlap and l["pit_status"] == 0
                                 and not l["lap_invalid"]
                                 and self._ers_pct(idx) > ERS_SHOW_THRESHOLD)
        self.panel_shown_lap[idx] = l["lap_num"]

    def _ers_pct(self, idx):
        st = self.status[idx]
        return st["ers_energy_j"] / fp.ERS_MAX_J * 100.0 if st else 0.0

    def _fastest_lap_holder(self):
        """Index of the car holding the session's fastest lap, or None."""
        holder, best = None, 0
        for h in self.history:
            if h and h["best_lap_time_ms"] > 0 and (best == 0 or h["best_lap_time_ms"] < best):
                best, holder = h["best_lap_time_ms"], h["car_idx"]
        return holder

    @staticmethod
    def _cumulative(hist):
        """A lap's cumulative split times: (S1, S1+S2, full lap)."""
        s1, s2, s3 = hist["best_lap_sectors_ms"]
        return (s1, s1 + s2, s1 + s2 + s3)

    def _sector_color(self, idx, i, overall_best):
        """Colour for sector i (0-2) of a car's displayed lap: gray (not done),
        purple (fastest of anyone), green (this driver's session best), else
        yellow. Bests come from the game's per-sector tracking, which records
        only valid sectors — so invalid laps never colour green/purple."""
        sv = self.sector_view[idx]
        if not sv or not sv["done"][i]:
            return "gray"
        hist = self.history[idx]
        if not hist:
            return "yellow"  # completed but history hasn't arrived yet
        my_best = hist["best_sectors_ms"][i]
        # The personal best for this sector was set on the lap we're showing ->
        # this lap improved it (or matched the field's best).
        if my_best > 0 and hist["best_sector_laps"][i] == sv["lap"]:
            if overall_best[i] and my_best <= overall_best[i]:
                return "purple"
            return "green"
        return "yellow"

    def _sector_panel(self, active_idx, rows_by_idx):
        """The live sector-timing block for the active/spectated driver, or None
        when there's no such car on track. Quali-only (gated by the caller)."""
        row = rows_by_idx.get(active_idx)
        lap = self.lap[active_idx] if active_idx is not None else None
        if row is None or lap is None:
            return None

        # Fastest sector of anyone in the field, per sector (the purple bar).
        overall_best = [0, 0, 0]
        for h in self.history:
            if not h:
                continue
            for i in range(3):
                v = h["best_sectors_ms"][i]
                if v > 0 and (overall_best[i] == 0 or v < overall_best[i]):
                    overall_best[i] = v
        colours = [self._sector_color(active_idx, i, overall_best) for i in range(3)]

        # Reference (right side): the session's fastest-lap holder's cumulative
        # split at the boundary of the sector our driver is currently in, plus
        # that holder's surname. Hidden until someone has set a lap.
        cur_sector = lap["sector"]
        holder = self._fastest_lap_holder()
        reference = None
        cumulative = None
        if holder is not None:
            cumulative = self._cumulative(self.history[holder])
            href = rows_by_idx.get(holder)
            reference = {
                "timeMs": cumulative[min(cur_sector, 2)],
                "name": href["code"] if href else "",
            }

        # Delta flash: the gap of the driver's latest crossing (S1/S2/lap) versus
        # the fastest lap at the same point. The client swaps it in for the clock
        # for ~2s. Keyed by (lap, boundary) so the client flashes each once. Only
        # crossings made while the panel was shown flash (not e.g. an out lap).
        delta = None
        ev = self.delta_event[active_idx]
        if ev and ev["shown"] and cumulative is not None:
            target = cumulative[ev["boundary"]]
            if target > 0:
                delta = {
                    "key": f"{ev['lap']}-{ev['boundary']}",
                    "playerMs": ev["player_ms"],          # frozen time at the crossing
                    "ms": ev["player_ms"] - target,       # gap vs the fastest lap (<0 faster)
                }

        return {
            "position": row["position"],
            "code": row["code"],
            "teamColour": row["teamColour"],
            "teamLogo": row["teamLogo"],
            "tyre": row["tyre"],
            "tyreColour": row["tyreColour"],
            "currentLapMs": lap["current_lap_ms"],
            "sectors": colours,          # ["gray"|"yellow"|"green"|"purple"] x3
            "reference": reference,      # {timeMs, name} or None
            "delta": delta,              # {key, ms} or None (ms<0 = faster)
        }

    def snapshot(self):
        """Build the JSON-serialisable broadcast view, sorted by position."""
        rows = []
        leader_lap = max(
            (l["lap_num"] for l in self.lap if l and l["position"] > 0),
            default=0,
        )
        # The "active" car to highlight: the spectated car while spectating,
        # otherwise the player's own car (the header's m_playerCarIndex, which is
        # meaningless during spectating). 255 = none, so nobody is highlighted.
        if self.session.get("is_spectating"):
            active_idx = self.session.get("spectator_car_index", 255)
        else:
            active_idx = self.player_car_index
        for idx in range(fp.NUM_CARS):
            lap = self.lap[idx]
            if not lap or lap["position"] <= 0:
                continue
            part = self.participants[idx] or {}
            stat = self.status[idx] or {}
            tele = self.telemetry[idx] or {}

            team_id = part.get("team_id")
            team_name, team_colour = fp.team_info(team_id)
            team_logo = fp.team_logo(team_id)
            tyre_label, tyre_colour = fp.tyre_info(stat.get("visual_tyre"))
            # Compound used on the fastest lap (from session history) — shown
            # only in quali. None until history arrives / a lap is set.
            best_tyre_label = best_tyre_colour = None
            best_lap = ""
            best_lap_ms = 0
            hist = self.history[idx]
            if hist:
                best_visual = fp.fastest_lap_tyre(hist["best_lap_num"], hist["tyre_stints"])
                if best_visual is not None:
                    best_tyre_label, best_tyre_colour = fp.tyre_info(best_visual)
                best_lap_ms = hist["best_lap_time_ms"]
                best_lap = _fmt_lap_time(best_lap_ms)
            name = part.get("name") or ""
            number = part.get("race_number")
            is_human = part.get("ai_controlled", 1) == 0
            # Human (multiplayer) players carry a single online handle, so the
            # name-swap system and the "show the whole name" rule apply to them.
            # AI bots keep their real driver name reduced to a surname.
            if is_human:
                override = config.resolve_driver_name(name, number)
                code = override or _player_name(name, number)
                name = override or code
            else:
                code = _driver_surname(name, number)
            status_label = fp.result_label(lap["result_status"])  # None if racing

            rows.append({
                "carIndex": idx,
                "position": lap["position"],
                "name": name or code,
                "code": code,
                "raceNumber": part.get("race_number", 0),
                "team": team_name,
                "teamColour": team_colour,
                "teamLogo": team_logo,   # filename under /teams/, or None
                "gapToLeader": _fmt_gap(lap["gap_to_leader_ms"]),
                "interval": _fmt_gap(lap["interval_to_front_ms"]),
                "lastLap": _fmt_lap_time(lap["last_lap_ms"]),
                "lapNum": lap["lap_num"],
                "pitting": lap["pit_status"] != 0,
                "penaltySec": lap["penalties_sec"],   # unserved time penalty (s)
                "driveThrough": lap["drive_through"] > 0,
                "statusLabel": status_label,        # "DNF"/"DSQ"/"DNS"/"NC" or None
                "retired": status_label is not None,  # greys out the row
                "finished": lap["result_status"] == fp.RESULT_FINISHED,
                "onOutLap": lap["driver_status"] == fp.DRIVER_STATUS_OUT_LAP,
                "noTime": lap["last_lap_ms"] == 0,  # no completed lap = no gap yet

                "tyre": tyre_label,
                "tyreColour": tyre_colour,
                "tyreAge": stat.get("tyre_age_laps", 0),
                "bestTyre": best_tyre_label,        # fastest-lap compound (quali)
                "bestTyreColour": best_tyre_colour,
                "bestLap": best_lap,                # fastest-lap time "M:SS.mmm" (quali)
                "bestLapMs": best_lap_ms,           # raw, for quali ordering (0 = none)
                "drs": bool(tele.get("drs", 0)),
                "speed": tele.get("speed", 0),
                "isPlayer": idx == active_idx,  # active/spectated car (red highlight)
            })

        rows.sort(key=lambda r: r["position"])

        # Current fastest lap of the race (race sessions only). Derived from the
        # per-car best lap times we already track in session history — the
        # quickest non-zero one — rather than the one-shot "FTLP" event, so it
        # self-heals on packet loss and is correct even when the server joins
        # mid-session. Exactly one car is flagged; none until a lap is set. A
        # driver who set it then retired keeps the flag (the lap still stands).
        info_kind = fp.session_info_kind(self.session.get("session_type", 0))
        fastest_idx = None
        if info_kind == "race":
            timed = [r for r in rows if r["bestLapMs"] > 0]
            if timed:
                fastest_idx = min(timed, key=lambda r: r["bestLapMs"])["carIndex"]
        for r in rows:
            r["fastestLap"] = r["carIndex"] == fastest_idx

        # Live sector-timing block for the active driver — qualifying only, and
        # only while the visibility rules say so (shown for this lap, or within
        # the brief hold that lets a final delta flash finish).
        sector_panel = None
        if info_kind == "quali" and active_idx is not None and 0 <= active_idx < fp.NUM_CARS:
            if self.panel_shown[active_idx] or time.monotonic() < self.panel_hold_until[active_idx]:
                rows_by_idx = {r["carIndex"]: r for r in rows}
                sector_panel = self._sector_panel(active_idx, rows_by_idx)

        # Live throttle/brake of the active/spectated car (0..1), for the inputs
        # trace overlay. None when there's no such car (the block then draws flat).
        inputs = None
        if active_idx is not None and 0 <= active_idx < fp.NUM_CARS:
            tele = self.telemetry[active_idx]
            if tele:
                stat = self.status[active_idx] or {}
                setup = self.setups[active_idx] or {}
                inputs = {
                    "throttle": tele.get("throttle", 0.0),
                    "brake": tele.get("brake", 0.0),
                    "rpm": tele.get("rpm", 0),
                    "ersMode": stat.get("ers_deploy_mode", 0),     # 0..3
                    "brakeBias": stat.get("front_brake_bias", 0),  # % (front)
                    "diff": setup.get("on_throttle_diff", 0),      # % on-throttle
                }

        return {
            "session": {
                "brandMark": config.BRAND_MARK,
                "track": self.session.get("track_name", "—"),
                "type": self.session.get("session_type_name", "—"),
                "totalLaps": self.session.get("total_laps", 0),
                "currentLap": leader_lap,
                "timeLeft": self.session.get("session_time_left", 0),
                "infoKind": info_kind,
                "modeRotation": config.MODE_ROTATION,  # pools + auto-rotation (client-side)
                "flag": self._flag_state(),
            },
            "cars": rows,
            "sectorPanel": sector_panel,   # live sector block (quali only; else None)
            "inputs": inputs,              # {throttle, brake} of the active car, or None
        }


def _driver_surname(name, race_number):
    """Full last name in caps from an AI driver name (fallback: car number)."""
    parts = [p for p in name.replace("_", " ").split() if p]
    if parts:
        return parts[-1].upper()
    if race_number:
        return f"#{race_number}"
    return "—"


def _player_name(name, race_number):
    """A human player's online handle, shown whole (it is a single name, not
    first/last) and upper-cased for the broadcast look. Falls back to the car
    number when the name is hidden/empty (e.g. online names turned off)."""
    name = (name or "").strip()
    if name:
        return name.upper()
    if race_number:
        return f"#{race_number}"
    return "PLAYER"
