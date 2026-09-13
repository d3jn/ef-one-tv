"""info.py — the driver-info overlay's calculation + data layer.

Ported from the F1 Racing Companion overlay. Four stateful trackers (tyre wear,
ERS deltas, best sectors per compound, stint pace) plus pit projection. GameState
feeds these on each LAP packet and calls InfoState.build() for the structured
snapshot block the web /info overlay renders as three pages:

  Page 1 — live: player tyre wear + laps-left, ERS state + deltas, standings ±5.
  Page 2 — pit projection (rejoin slot/gaps/loss/penalties) + weather forecast.
  Page 3 — best sectors per compound + a leader/ahead/you/behind pace table.

A persistent ahead/behind header sits above all pages (race only). Page switching
is driven server-side by the in-game UDP Action buttons (BUTN event).

The trackers are ported close to verbatim from the source; only field names are
adapted to our parsed-packet shapes (see _norm_lap). Keeping that calculation
logic intact is the point of the migration.
"""

import config
import f1_packets as fp

NUM_CARS = fp.NUM_CARS
ERS_MAX_J = fp.ERS_MAX_J
WEAR_TARGET_PCT = 80

# result_status buckets (F1 spec): 1 inactive, 2 active, 3 finished are "on the
# timing screen"; 4 DNF, 5 DSQ, 6 NC, 7 retired are out.
ACTIVE_STATUS = {1, 2, 3}
RETIRED_STATUS = {4, 5, 6, 7}

RACE_SESSION_TYPES = {15, 16, 17}
ACTIVE_SESSION_TYPES = set(range(5, 18))  # quali (5-9), sprint quali (10-14), race (15-17)

TYRE_CODE = {16: "S", 17: "M", 18: "H", 7: "I", 8: "W"}  # single-letter compounds
TYRE_NAMES = ["RL", "RR", "FL", "FR"]
# m_ersDeployMode. 3 is "boost" in the 2026 format (was "overtake" in 2025) — it is
# not the 2026 Overtake Mode, which comes from Car Telemetry 2 (see _car_block).
ERS_MODE_NAMES = {0: "None", 1: "Medium", 2: "Hotlap", 3: "Boost"}

STOP_GO_PENALTY_S = 10
DRIVE_THROUGH_PENALTY_S = 20
RACE_START_LAP_DISTANCE_M = 800

# Per-track pit-loss baselines (green / safety car), in seconds, keyed by track_id
# (2026 appendix ids). Rough real-world estimates; override per-league via
# settings.json (see config). Tracks without an estimate (reverse layouts 39-41,
# Madrid 42) use the -1 default.
PITSTOP_TIMES = {
    -1: (21, 14), 0: (19, 13), 2: (21, 14), 3: (22, 15), 4: (21, 14),
    5: (17, 12), 6: (17, 12), 7: (22, 15), 9: (19, 13), 10: (20, 14),
    11: (25, 17), 12: (26, 18), 13: (21, 14), 14: (21, 14), 15: (21, 14), 16: (20, 14),
    17: (19, 13), 19: (22, 15), 20: (19, 13), 26: (17, 12), 27: (27, 18),
    29: (17, 12), 30: (19, 13), 31: (19, 13), 32: (21, 14),
}


def _pit_loss(track_id, sc_active):
    green, sc = PITSTOP_TIMES.get(track_id, PITSTOP_TIMES[-1])
    # settings.json can override per track_id (config builds the dict).
    override = config.PIT_TIME_OVERRIDES.get(track_id)
    if override:
        green = override.get("green", green)
        sc = override.get("sc", sc)
    return float(sc if sc_active else green)


# --- Trackers (ported from the source overlay) ------------------------------

class WearTracker:
    """Most recent *clean full lap* tyre-wear delta per car on the current
    compound — used to project laps-left to the 80% wear cap."""

    def __init__(self, target_pct=WEAR_TARGET_PCT):
        self.target_pct = target_pct
        self._lap_start = {}      # car -> {lap, compound, wear, clean_start}
        self._last_lap_wear = {}  # car -> (compound, lap_wear_delta)
        self._last_age = {}

    def observe(self, car_idx, lap_num, max_wear, compound, tires_age_laps):
        if lap_num <= 0 or compound is None:
            return
        prev_age = self._last_age.get(car_idx)
        if prev_age is not None and tires_age_laps < prev_age:  # fresh tyres
            self._lap_start.pop(car_idx, None)
            self._last_lap_wear.pop(car_idx, None)
        self._last_age[car_idx] = tires_age_laps

        prev = self._lap_start.get(car_idx)
        if prev is None:
            self._lap_start[car_idx] = {"lap": lap_num, "compound": compound,
                                        "wear": max_wear, "clean_start": False}
            return
        if lap_num == prev["lap"]:
            if compound != prev["compound"]:  # mid-lap compound change = pit
                self._lap_start[car_idx] = {"lap": lap_num, "compound": compound,
                                            "wear": max_wear, "clean_start": False}
                self._last_lap_wear.pop(car_idx, None)
            return
        # Lap rolled over.
        if (lap_num == prev["lap"] + 1 and compound == prev["compound"]
                and prev["clean_start"]):
            delta = max_wear - prev["wear"]
            if delta > 0:
                self._last_lap_wear[car_idx] = (compound, delta)
            else:
                self._last_lap_wear.pop(car_idx, None)
        else:
            self._last_lap_wear.pop(car_idx, None)
        self._lap_start[car_idx] = {"lap": lap_num, "compound": compound,
                                    "wear": max_wear, "clean_start": True}

    def last_lap_wear(self, car_idx):
        return self._last_lap_wear.get(car_idx)


class ErsTracker:
    """Player ERS battery at SF crossings, exposing prev-lap and since-SF deltas."""

    HISTORY = 3

    def __init__(self):
        self._history = []
        self._last_lap_num = None

    def observe(self, lap_num, ers_pct):
        if not lap_num or lap_num <= 0:
            return
        if self._last_lap_num is None:
            self._last_lap_num = lap_num
            return
        if lap_num == self._last_lap_num:
            return
        if lap_num == self._last_lap_num + 1:
            self._history.append(ers_pct)
            while len(self._history) > self.HISTORY:
                self._history.pop(0)
        else:
            self._history = []
        self._last_lap_num = lap_num

    def deltas(self, current_ers_pct):
        if self._history:
            now_delta = current_ers_pct - self._history[-1]
        elif self._last_lap_num == 1:
            now_delta = current_ers_pct - 100.0  # race-start full battery
        else:
            return (None, None)
        prev_delta = (self._history[-1] - self._history[-2]
                      if len(self._history) >= 2 else None)
        return (prev_delta, now_delta)


class SectorTracker:
    """Per-car sector times across each car's last 3 laps, tagged by compound.
    Used for the best-sector-per-compound table. Pit-tainted sectors are
    excluded (in-lap S3 / out-lap S1)."""

    HISTORY_LAPS = 3

    def __init__(self):
        self._car_laps = {}
        self._car_state = {}

    def _new_entry(self):
        return {
            "s1_raw_ms": None, "s2_raw_ms": None,
            "s1": None, "s2": None, "s3": None,
            "c1": None, "c2": None, "c3": None,
            "pit_s1": False, "pit_s2": False, "pit_s3": False,
        }

    def observe(self, car_idx, lap_info, status):
        if not lap_info:
            return
        if lap_info.get("result_status", 0) not in ACTIVE_STATUS:
            self._car_laps.pop(car_idx, None)
            self._car_state.pop(car_idx, None)
            return
        lap_num = lap_info.get("current_lap_num", 0)
        if lap_num <= 0:
            return

        sector = lap_info.get("sector", 0)
        pit_status = lap_info.get("pit_status", 0)
        last_lap_ms = lap_info.get("last_lap_time_ms", 0)
        s1_total_ms = lap_info.get("sector1_total_ms", 0)
        s2_total_ms = lap_info.get("sector2_total_ms", 0)
        compound = TYRE_CODE.get(status.get("visual_tyre")) if status else None

        state = self._car_state.setdefault(car_idx, {"last_lap": None})
        laps = self._car_laps.setdefault(car_idx, {})

        if state["last_lap"] is not None and lap_num != state["last_lap"]:
            prev = laps.get(state["last_lap"])
            if (prev is not None and prev["s1_raw_ms"] is not None
                    and prev["s2_raw_ms"] is not None and last_lap_ms > 0
                    and not prev["pit_s3"]):
                s3_ms = last_lap_ms - prev["s1_raw_ms"] - prev["s2_raw_ms"]
                if s3_ms > 0:
                    prev["s3"] = s3_ms / 1000.0
                    prev["c3"] = compound

        entry = laps.setdefault(lap_num, self._new_entry())
        if pit_status != 0:
            if sector == 0:
                entry["pit_s1"] = True
            elif sector == 1:
                entry["pit_s2"] = True
            elif sector == 2:
                entry["pit_s3"] = True
        if sector >= 1 and entry["s1_raw_ms"] is None and s1_total_ms > 0:
            entry["s1_raw_ms"] = s1_total_ms
            if not entry["pit_s1"]:
                entry["s1"] = s1_total_ms / 1000.0
                entry["c1"] = compound
        if sector >= 2 and entry["s2_raw_ms"] is None and s2_total_ms > 0:
            entry["s2_raw_ms"] = s2_total_ms
            if not entry["pit_s2"]:
                entry["s2"] = s2_total_ms / 1000.0
                entry["c2"] = compound

        state["last_lap"] = lap_num
        cutoff = lap_num - (self.HISTORY_LAPS - 1)
        for stale in [ln for ln in laps if ln < cutoff]:
            del laps[stale]

    def best_per_compound_sector(self):
        owners = {}  # (compound, sector_idx) -> (time, car, lap)
        for car_idx, laps in self._car_laps.items():
            for ln, entry in laps.items():
                for sector_idx, t_key, c_key in ((0, "s1", "c1"), (1, "s2", "c2"), (2, "s3", "c3")):
                    t, c = entry[t_key], entry[c_key]
                    if t is None or c is None:
                        continue
                    key = (c, sector_idx)
                    if key not in owners or t < owners[key][0]:
                        owners[key] = (t, car_idx, ln)
        return {key: (t, self._is_holders_most_recent(car_idx, key[1], ln))
                for key, (t, car_idx, ln) in owners.items()}

    def _is_holders_most_recent(self, car_idx, sector_idx, lap_num):
        laps = self._car_laps.get(car_idx, {})
        if sector_idx == 0:
            c = [ln for ln, e in laps.items() if e["s1_raw_ms"] is not None]
            return bool(c) and max(c) == lap_num
        if sector_idx == 1:
            c = [ln for ln, e in laps.items() if e["s2_raw_ms"] is not None]
            return bool(c) and max(c) == lap_num
        if sector_idx == 2:
            last_lap = self._car_state.get(car_idx, {}).get("last_lap")
            if last_lap is None or last_lap < 2:
                return False
            return last_lap - 1 == lap_num
        return False


class StintTracker:
    """Per-car clean-lap history on the current physical tyre set for the pace
    table: avg lap, avg wear-rate, and falloff vs a frozen optimal-pace baseline."""

    BASELINE_WINDOW = 5

    def __init__(self):
        self._stints = {}
        self._state = {}
        self._baseline_ms = {}

    def _new_state(self):
        return {"last_lap_num": None, "last_compound": None, "last_age": None,
                "pit_tainted": False, "prev_rollover_wear": None, "clean_start": False}

    def _wipe(self, car_idx, state):
        self._stints.setdefault(car_idx, []).clear()
        self._baseline_ms[car_idx] = None
        state["prev_rollover_wear"] = None
        state["clean_start"] = False

    def observe(self, car_idx, lap_num, last_lap_time_ms, max_wear,
                pit_status, compound, tires_age_laps):
        if not lap_num or lap_num <= 0 or compound is None:
            return
        state = self._state.setdefault(car_idx, self._new_state())
        self._stints.setdefault(car_idx, [])
        self._baseline_ms.setdefault(car_idx, None)
        stint = self._stints[car_idx]

        if state["last_age"] is not None and tires_age_laps < state["last_age"]:
            self._wipe(car_idx, state)
        state["last_age"] = tires_age_laps

        if state["last_compound"] is not None and state["last_compound"] != compound:
            self._wipe(car_idx, state)
        state["last_compound"] = compound

        if state["last_lap_num"] is not None and lap_num != state["last_lap_num"]:
            if lap_num == state["last_lap_num"] + 1:
                if (last_lap_time_ms > 0 and not state["pit_tainted"]
                        and state["prev_rollover_wear"] is not None
                        and state["clean_start"]):
                    wear_delta = max_wear - state["prev_rollover_wear"]
                    if wear_delta >= 0:
                        stint.append({"lap_num": state["last_lap_num"],
                                      "lap_time_ms": last_lap_time_ms,
                                      "wear_delta": wear_delta})
                        if (len(stint) == self.BASELINE_WINDOW
                                and self._baseline_ms[car_idx] is None):
                            self._baseline_ms[car_idx] = self._compute_baseline(stint)
                state["prev_rollover_wear"] = max_wear
                state["clean_start"] = True
            else:
                self._wipe(car_idx, state)
            state["pit_tainted"] = False
        state["last_lap_num"] = lap_num
        if pit_status != 0:
            state["pit_tainted"] = True

    @staticmethod
    def _compute_baseline(first_n_laps):
        sorted_times = sorted(l["lap_time_ms"] for l in first_n_laps)
        middle = sorted_times[1:-1]  # double-trimmed mean (drop fastest + slowest)
        return sum(middle) / len(middle)

    def stint_summary(self, car_idx):
        stint = self._stints.get(car_idx, [])
        if not stint:
            return None
        last_3 = stint[-3:]
        avg_wear_delta = sum(l["wear_delta"] for l in last_3) / len(last_3)
        avg_lap_ms = sum(l["lap_time_ms"] for l in last_3) / len(last_3)
        lap_time_delta_ms = None
        baseline_ms = self._baseline_ms.get(car_idx)
        if baseline_ms is not None and len(stint) > self.BASELINE_WINDOW:
            post = stint[self.BASELINE_WINDOW:][-3:]
            lap_time_delta_ms = sum(l["lap_time_ms"] for l in post) / len(post) - baseline_ms
        return {"lap_count": len(stint), "avg_wear_delta": avg_wear_delta,
                "avg_lap_ms": avg_lap_ms, "lap_time_delta_ms": lap_time_delta_ms}


# --- Per-car accessors over GameState fragments -----------------------------

def _norm_lap(lap):
    """Source-shaped lap_info from our parsed lap dict (for the trackers)."""
    return {
        "result_status": lap["result_status"],
        "current_lap_num": lap["lap_num"],
        "sector": lap["sector"],
        "pit_status": lap["pit_status"],
        "last_lap_time_ms": lap["last_lap_ms"],
        "sector1_total_ms": lap["sector1_ms"],
        "sector2_total_ms": lap["sector2_ms"],
    }


def _compound(status):
    return TYRE_CODE.get(status.get("visual_tyre")) if status else None


def _max_wear(damage):
    return max(damage["tyre_wear"]) if damage else None


def _delta_to_leader_s(lap):
    return lap["gap_to_leader_ms"] / 1000.0


def _lap_diff(other, player, track_len):
    """Integer lap difference of other vs player from total_distance (falls back
    to lap_num). A ±1L marker only appears when physically a full lap apart."""
    if not other or not player:
        return 0
    if track_len and track_len > 0:
        od, pd = other.get("total_distance"), player.get("total_distance")
        if od is not None and pd is not None:
            return int((od - pd) / track_len)
    return other.get("lap_num", 0) - player.get("lap_num", 0)


def _gap(other, player, track_len):
    """{lapDiff, seconds} of other relative to player (seconds None when lapped)."""
    if not other or not player:
        return None
    ld = _lap_diff(other, player, track_len)
    if ld != 0:
        return {"lapDiff": ld, "seconds": None}
    return {"lapDiff": 0, "seconds": _delta_to_leader_s(other) - _delta_to_leader_s(player)}


def _display_name(game, idx):
    part = game.participants[idx] or {}
    name = part.get("name") or ""
    number = part.get("race_number")
    override = config.resolve_driver_name(name, number)
    if override:
        return override
    parts = [p for p in name.replace("_", " ").split() if p]
    if parts:
        return parts[-1].upper()
    return f"#{number}" if number else f"CAR {idx}"


def _fw_text(damage):
    if not damage:
        return None
    fl, fr = damage.get("fw_left", 0), damage.get("fw_right", 0)
    if fl == 0 and fr == 0:
        return None
    if abs(fl - fr) < 10:
        return f"{max(fl, fr):.0f}%"
    return f"L{fl:.0f}/R{fr:.0f}"


# --- InfoState: trackers + page state + structured snapshot -----------------

class InfoState:
    def __init__(self):
        self.wear = WearTracker()
        self.sector = SectorTracker()
        self.stint = StintTracker()
        self.ers = ErsTracker()
        self.active_page = 1
        self._prev_buttons = 0

    def on_button(self, buttons):
        """Edge-trigger page switching on the bound UDP Actions."""
        a1 = buttons & fp.UDP_ACTION_1_MASK
        a2 = buttons & fp.UDP_ACTION_2_MASK
        if a1 and not (self._prev_buttons & fp.UDP_ACTION_1_MASK):
            self.active_page = self.active_page % 3 + 1            # next, wrap 3->1
        if a2 and not (self._prev_buttons & fp.UDP_ACTION_2_MASK):
            self.active_page = (self.active_page - 2) % 3 + 1      # prev, wrap 1->3
        self._prev_buttons = buttons

    def observe(self, game):
        """Feed the trackers from the latest fragments (call per LAP packet)."""
        for idx in range(NUM_CARS):
            lap = game.lap[idx]
            if not lap:
                continue
            status = game.status[idx]
            self.sector.observe(idx, _norm_lap(lap), status)

            if lap["result_status"] not in ACTIVE_STATUS:
                continue
            damage = game.damage[idx]
            if not damage or not status:
                continue
            mw = _max_wear(damage)
            comp = _compound(status)
            age = status.get("tyre_age_laps", 0)
            self.wear.observe(idx, lap["lap_num"], mw, comp, age)
            self.stint.observe(idx, lap["lap_num"], lap["last_lap_ms"], mw,
                               lap["pit_status"], comp, age)

        player = game.lap[game.player_car_index]
        pstat = game.status[game.player_car_index]
        if player and pstat:
            ers_pct = pstat.get("ers_energy_j", 0) / ERS_MAX_J * 100
            self.ers.observe(player["lap_num"], ers_pct)

    # -- build ----------------------------------------------------------------

    def build(self, game, active_idx):
        """The /info snapshot block for the active page (+ persistent header)."""
        session = game.session
        stype = session.get("session_type", 0)
        if active_idx is None or not (0 <= active_idx < NUM_CARS) or stype not in ACTIVE_SESSION_TYPES:
            return {"available": False, "activePage": self.active_page}

        is_race = stype in RACE_SESSION_TYPES
        kind = "race" if is_race else ("quali" if 5 <= stype <= 14 else "other")
        player = game.lap[active_idx]
        retired = bool(player) and player.get("result_status", 0) in RETIRED_STATUS
        race_start = self._is_race_start(game, active_idx, is_race)

        out = {
            "available": True,
            "activePage": self.active_page,
            "sessionKind": kind,
            "banner": self._banner(player) if retired else None,
            "raceStart": race_start,
            "header": self._header(game, active_idx, is_race, retired, race_start),
        }
        page = self.active_page
        if page == 1:
            out["page"] = self._page_live(game, active_idx, is_race, retired, race_start)
        elif page == 2:
            out["page"] = self._page_pit(game, active_idx, is_race, retired, race_start)
        else:
            out["page"] = self._page_pace(game, active_idx, is_race, retired)
        return out

    def _is_race_start(self, game, idx, is_race):
        if not is_race:
            return False
        p = game.lap[idx]
        if not p or p.get("lap_num", 0) > 1:
            return False
        return p.get("lap_distance", 0.0) < RACE_START_LAP_DISTANCE_M

    @staticmethod
    def _banner(player):
        return {4: "DNF", 5: "DSQ", 6: "NC", 7: "RETIRED"}.get(player.get("result_status", 0), "OUT")

    # -- active cars helper ---------------------------------------------------

    def _active_sorted(self, game):
        cars = []
        for idx in range(NUM_CARS):
            info = game.lap[idx]
            if not info:
                continue
            pos = info.get("position", 0)
            if pos <= 0 or info.get("result_status", 0) not in ACTIVE_STATUS:
                continue
            cars.append((pos, idx, info))
        cars.sort()
        return cars

    def _neighbours(self, game, idx):
        """(leader_idx, ahead_idx, behind_idx) by race position around the player."""
        player = game.lap[idx]
        ppos = player.get("position", 0) if player else 0
        leader = ahead = behind = None
        for i in range(NUM_CARS):
            info = game.lap[i]
            if not info or info.get("result_status", 0) not in ACTIVE_STATUS:
                continue
            pos = info.get("position", 0)
            if pos == 1:
                leader = i
            if pos == ppos - 1:
                ahead = i
            if pos == ppos + 1:
                behind = i
        return leader, ahead, behind

    def _car_block(self, game, idx):
        """tyre/wear/battery/ERS-mode/overtake for the ahead/behind header, or None."""
        status, damage = game.status[idx], game.damage[idx]
        if not status or not damage:
            return None
        wear = damage["tyre_wear"]
        mw = max(wear)
        return {
            "tyre": _compound(status) or "?",
            "wearPct": round(mw),
            "wearCorner": TYRE_NAMES[wear.index(mw)],
            "batteryPct": round(status.get("ers_energy_j", 0) / ERS_MAX_J * 100),
            "ersMode": ERS_MODE_NAMES.get(status.get("ers_deploy_mode", 0), "?"),
            # 2026 Overtake Mode currently engaged (Car Telemetry 2).
            "overtake": bool((game.telemetry2[idx] or {}).get("overtake_active", 0)),
        }

    def _header(self, game, idx, is_race, retired, race_start):
        if retired or not is_race or race_start:
            return None
        player = game.lap[idx]
        if not player or player.get("position", 0) <= 0:
            return None
        track_len = game.session.get("track_length_m", 0)
        _, ahead, behind = self._neighbours(game, idx)
        rows = []
        for other in (ahead, behind):
            ol = game.lap[other] if other is not None else None
            block = self._car_block(game, other) if other is not None else None
            if ol and block:
                rows.append({"gap": _gap(ol, player, track_len), **block})
            else:
                rows.append(None)
        return rows

    # -- page 1: live ---------------------------------------------------------

    def _page_live(self, game, idx, is_race, retired, race_start):
        if retired:
            return {"type": "live", "standings": None, "player": None, "grid": None}
        if race_start:
            return {"type": "live", "grid": self._grid(game, idx),
                    "standings": None, "player": None}
        player = game.lap[idx]
        if not player or player.get("position", 0) <= 0:
            return {"type": "live", "standings": None, "player": None, "grid": None}
        return {
            "type": "live",
            "grid": None,
            "player": self._player_extras(game, idx) if is_race else None,
            "standings": self._standings(game, idx),
        }

    def _grid(self, game, idx):
        cars = self._active_sorted(game)
        rows = [{"pos": pos, "name": "------" if i == idx else _display_name(game, i),
                 "isPlayer": i == idx, "tyre": _compound(game.status[i]) or "?"}
                for pos, i, _ in cars]
        plist = next((k for k, (_, i, _) in enumerate(cars) if i == idx), None)
        if plist is None:
            return rows[:11]
        return rows[max(0, plist - 5):plist + 6]

    def _standings(self, game, idx):
        cars = self._active_sorted(game)
        if not cars:
            return []
        player = game.lap[idx]
        track_len = game.session.get("track_length_m", 0)
        rows, plist = [], None
        for k, (pos, i, info) in enumerate(cars):
            is_p = i == idx
            if is_p:
                plist = k
            damage = game.damage[i]
            rows.append({
                "pos": pos,
                "name": "------" if is_p else _display_name(game, i),
                "isPlayer": is_p,
                "gap": None if is_p else _gap(info, player, track_len),
                "tyre": _compound(game.status[i]) or "?",
                "wearPct": round(_max_wear(damage)) if _max_wear(damage) is not None else None,
                "fw": _fw_text(damage),
                "pen": {"timeSec": info.get("penalties_sec", 0),
                        "dt": info.get("drive_through", 0),
                        "sg": info.get("unserved_sg", 0)},
            })
        if plist is None:
            return rows[:11]
        return rows[max(0, plist - 5):plist + 6]

    def _player_extras(self, game, idx):
        status, damage = game.status[idx], game.damage[idx]
        out = {"tyre": None, "ers": None}
        race_laps = self._race_laps_remaining(game, idx)
        if damage:
            wear = damage["tyre_wear"]
            mw = max(wear)
            comp = _compound(status)
            last = self.wear.last_lap_wear(idx)
            tyre = {"wearPct": round(mw), "corner": TYRE_NAMES[wear.index(mw)],
                    "degPerLap": None, "tyreLaps": None, "raceLaps": race_laps}
            if last is not None and comp is not None and last[0] == comp and last[1] > 0:
                tyre["degPerLap"] = round(last[1], 1)
                tyre["tyreLaps"] = 0 if mw >= WEAR_TARGET_PCT else int((WEAR_TARGET_PCT - mw) / last[1])
            out["tyre"] = tyre
        if status:
            pct = status.get("ers_energy_j", 0) / ERS_MAX_J * 100
            prev_d, now_d = self.ers.deltas(pct)
            out["ers"] = {"pct": round(pct), "mode": ERS_MODE_NAMES.get(status.get("ers_deploy_mode", 0), "?"),
                          "prevDelta": None if prev_d is None else round(prev_d, 1),
                          "nowDelta": None if now_d is None else round(now_d, 1)}
        return out

    def _race_laps_remaining(self, game, idx):
        total = game.session.get("total_laps", 0)
        if not total:
            return None
        leader = next((game.lap[i] for i in range(NUM_CARS)
                       if game.lap[i] and game.lap[i].get("position") == 1), None)
        if not leader or leader.get("lap_num", 0) <= 0:
            return None
        leader_remaining = total - leader["lap_num"] + 1
        deficit = _lap_diff(leader, game.lap[idx], game.session.get("track_length_m", 0))
        return max(0, leader_remaining - deficit)

    # -- page 2: pit + weather ------------------------------------------------

    def _page_pit(self, game, idx, is_race, retired, race_start):
        weather = self._weather(game)
        if retired or not is_race:
            return {"type": "pit", "pit": None, "weather": weather, "note": None}
        if race_start:
            return {"type": "pit", "pit": None, "weather": weather,
                    "note": "Pit projection unavailable on lap 1"}
        player = game.lap[idx]
        if not player or player.get("position", 0) <= 0:
            return {"type": "pit", "pit": None, "weather": weather, "note": None}
        return {"type": "pit", "pit": self._pit_projection(game, idx),
                "weather": weather, "note": None}

    def _weather(self, game):
        out = []
        for s in game.session.get("weather_forecast", []):
            out.append({"timeOffset": s["time_offset"], "rainPct": s["rain_pct"],
                        "weather": fp.WEATHER_TYPES.get(s["weather"], "?")})
        return out

    def _pit_projection(self, game, idx):
        player = game.lap[idx]
        ppos = player["position"]
        track_id = game.session.get("track_id", -1)
        sc_active = game.session.get("safety_car_status", 0) in (1, 2)
        pit_loss = _pit_loss(track_id, sc_active)
        player_delta = _delta_to_leader_s(player)

        extra = 0
        if player.get("should_serve_pen"):
            extra += player.get("unserved_sg", 0) * STOP_GO_PENALTY_S
        projected_delta = player_delta + pit_loss + extra

        proj_cars = []
        for i in range(NUM_CARS):
            if i == idx:
                continue
            info = game.lap[i]
            if not info or info.get("position", 0) <= 0:
                continue
            if info.get("result_status", 0) not in ACTIVE_STATUS:
                continue
            if info.get("pit_status", 0) in (1, 2):
                continue
            proj_cars.append(info)
        proj_cars.sort(key=lambda c: _delta_to_leader_s(c))

        insert = 0
        while insert < len(proj_cars) and projected_delta > _delta_to_leader_s(proj_cars[insert]):
            insert += 1
        projected_pos = max(ppos, insert + 1)
        positions_lost = projected_pos - ppos

        track_len = game.session.get("track_length_m", 0)
        ahead = proj_cars[insert - 1] if insert - 1 >= 0 else None
        behind = proj_cars[insert] if insert < len(proj_cars) else None
        ahead_gap = self._proj_gap(projected_delta - _delta_to_leader_s(ahead), True, ahead, player, track_len) if ahead else None
        behind_gap = self._proj_gap(_delta_to_leader_s(behind) - projected_delta, False, behind, player, track_len) if behind else None

        pen = None
        dt, sg = player.get("drive_through", 0), player.get("unserved_sg", 0)
        if dt or sg:
            owed = dt * DRIVE_THROUGH_PENALTY_S + sg * STOP_GO_PENALTY_S
            pen = {"dt": dt, "sg": sg, "totalSec": owed}
        return {"projectedPos": projected_pos, "positionsLost": positions_lost,
                "pitLoss": round(pit_loss), "scActive": sc_active,
                "aheadGap": ahead_gap, "behindGap": behind_gap, "penOwed": pen}

    @staticmethod
    def _proj_gap(gap_seconds, is_ahead, other, player, track_len):
        ld = _lap_diff(other, player, track_len)
        if ld != 0:
            return {"lapDiff": ld, "seconds": None}
        return {"lapDiff": 0, "seconds": -gap_seconds if is_ahead else gap_seconds}

    # -- page 3: sectors + pace ----------------------------------------------

    def _page_pace(self, game, idx, is_race, retired):
        if retired or not is_race:
            return {"type": "pace", "sectors": None, "pace": None}
        return {"type": "pace", "sectors": self._sectors(), "pace": self._pace(game, idx)}

    def _sectors(self):
        best = self.sector.best_per_compound_sector()
        column_best = {}  # sector_idx -> (compound, time)
        for (compound, si), (t, _hot) in best.items():
            if si not in column_best or t < column_best[si][1]:
                column_best[si] = (compound, t)
        rows = []
        for compound in ("S", "M", "H", "I", "W"):
            cells = []
            for si in (0, 1, 2):
                entry = best.get((compound, si))
                col = column_best.get(si)
                if entry is None:
                    cells.append(None)
                else:
                    t, hot = entry
                    is_abs = col is not None and col[0] == compound
                    cells.append({"value": round(t, 3) if is_abs else round(t - col[1], 3),
                                  "isAbsolute": is_abs, "hot": hot})
            stars = sum(1 for si in (0, 1, 2) if column_best.get(si, (None,))[0] == compound)
            rows.append({"compound": compound, "stars": stars, "cells": cells})
        return rows

    def _pace(self, game, idx):
        player = game.lap[idx]
        if not player or player.get("position", 0) <= 0:
            return []
        leader, ahead, behind = self._neighbours(game, idx)
        ordered, seen = [], set()
        for i in (leader, ahead, idx, behind):
            if i is None or i in seen:
                continue
            ordered.append(i)
            seen.add(i)
        rows = []
        for i in ordered:
            info = game.lap[i] or {}
            status, damage = game.status[i], game.damage[i]
            summary = self.stint.stint_summary(i)
            mw = _max_wear(damage)
            rows.append({
                "pos": info.get("position", 0),
                "name": "------" if i == idx else _display_name(game, i),
                "isPlayer": i == idx,
                "compound": _compound(status) or "?",
                "wearPct": round(mw) if mw is not None else None,
                "wearDelta": round(summary["avg_wear_delta"], 1) if summary else None,
                "avgLapMs": round(summary["avg_lap_ms"]) if summary else None,
                "lapDeltaMs": (round(summary["lap_time_delta_ms"])
                               if summary and summary["lap_time_delta_ms"] is not None else None),
            })
        return rows
