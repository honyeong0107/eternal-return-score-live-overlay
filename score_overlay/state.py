from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field

from .recognizer import HudObservation, TeamObservation


TEAM_COLORS = ("#d9004d", "#ee7411", "#d8ad00", "#d6c5a4", "#ec6385", "#d900aa", "#bc3c5c", "#baff00")


@dataclass
class StableValue:
    value: object
    candidate: object = None
    count: int = 0

    def observe(self, value: object, confirmations: int = 2) -> bool:
        if value is None:
            return False
        if value == self.value:
            self.candidate = None
            self.count = 0
            return False
        if value == self.candidate:
            self.count += 1
        else:
            self.candidate = value
            self.count = 1
        if self.count < confirmations:
            return False
        self.value = value
        self.candidate = None
        self.count = 0
        return True


@dataclass
class TeamState:
    team: int
    name: str
    color: str
    ts: StableValue = field(default_factory=lambda: StableValue(0.0))
    ks: StableValue = field(default_factory=lambda: StableValue(0.0))
    round_ts: StableValue = field(default_factory=lambda: StableValue(0.0))
    round_ks: StableValue = field(default_factory=lambda: StableValue(0.0))
    carried_ts: float = 0.0
    carried_ks: float = 0.0
    alive: StableValue = field(default_factory=lambda: StableValue(3))
    knocked: StableValue = field(default_factory=lambda: StableValue(0))
    respawning: StableValue = field(default_factory=lambda: StableValue(0))
    status: str = "ACTIVE"
    eliminated_at: float | None = None
    respawn_grace_seconds: float = 0.0


class ScoreState:
    def __init__(self, tournament: dict | list[str]):
        self._lock = threading.Lock()
        if isinstance(tournament, list):
            tournament = {
                "id": "default",
                "name": "기본 대회",
                "teams": tournament,
                "theme": {
                    "title": "LEADERBOARD",
                    "accent": "#a8d8f0",
                    "surface": "#ffffff",
                    "text": "#111c22",
                    "muted": "#52656f",
                    "ks": "#3e718a",
                    "rank": "#a8d8f0",
                    "rankText": "#0e1a20",
                    "line": "#000000",
                    "elimination": "#86aabd",
                },
            }
        team_names = tournament["teams"]
        self.tournament = {
            "id": tournament["id"],
            "name": tournament["name"],
            "theme": deepcopy(tournament["theme"]),
        }
        self.day = StableValue(1)
        self.round_number = 1
        self.round_open = True
        self.rounds: list[dict] = []
        self.teams = [
            TeamState(index, team_names[index - 1], TEAM_COLORS[index - 1]) for index in range(1, 9)
        ]
        self.updated_at = time.time()
        self.frame_age_ms = 0
        self.processing_ms = 0.0
        self.source = "starting"
        self.resolution_ok = True
        self.wipe_seen_at = 0.0
        self.revision = 0

    def apply(
        self,
        observation: HudObservation,
        processing_ms: float,
        source: str,
        sample_seconds: float = 1.0,
    ) -> None:
        now = time.time()
        with self._lock:
            changed = self.day.observe(observation.day)
            if observation.wipe_marker:
                self.wipe_seen_at = now

            for seen in observation.teams:
                team = self.teams[seen.team - 1]
                changed |= self._apply_team(team, seen)
                if seen.respawning is not None and seen.respawning > 0:
                    team.respawn_grace_seconds = 10.0
                elif team.respawn_grace_seconds > 0:
                    team.respawn_grace_seconds = max(0.0, team.respawn_grace_seconds - sample_seconds)
                changed |= self._update_status(team, now)

            self.updated_at = now
            self.processing_ms = round(processing_ms, 2)
            self.source = source
            self.resolution_ok = observation.resolution_ok
            if changed:
                self.revision += 1

    def _apply_team(self, team: TeamState, seen: TeamObservation) -> bool:
        changed = False
        if seen.ts is not None and seen.ks is not None:
            # The HUD score resets each round, while the overlay score keeps
            # completed rounds as its carried total.
            if seen.ts >= float(team.round_ts.value) and seen.ks >= float(team.round_ks.value):
                round_changed = team.round_ts.observe(seen.ts)
                round_changed |= team.round_ks.observe(seen.ks)
                if round_changed:
                    team.ts = StableValue(team.carried_ts + float(team.round_ts.value))
                    team.ks = StableValue(team.carried_ks + float(team.round_ks.value))
                    changed = True
        changed |= team.alive.observe(seen.alive)
        changed |= team.knocked.observe(seen.knocked)
        changed |= team.respawning.observe(seen.respawning)
        return changed

    def _update_status(self, team: TeamState, now: float) -> bool:
        old = team.status
        alive = int(team.alive.value)
        day = int(self.day.value)
        if team.eliminated_at is not None:
            team.status = "ELIMINATED"
        elif alive > 0:
            team.status = "ACTIVE"
        elif day == 1 or int(team.respawning.value) > 0 or team.respawn_grace_seconds > 0:
            team.status = "BREAK"
        else:
            team.status = "ELIMINATED"
            team.eliminated_at = now
        return old != team.status

    def apply_live_snapshot(self, observation: HudObservation, source: str) -> None:
        with self._lock:
            if observation.day is not None:
                self.day = StableValue(observation.day)
            for seen in observation.teams:
                team = self.teams[seen.team - 1]
                if seen.ts is not None and seen.ks is not None:
                    round_ts = max(float(team.round_ts.value), seen.ts)
                    round_ks = max(float(team.round_ks.value), seen.ks)
                    team.round_ts = StableValue(round_ts)
                    team.round_ks = StableValue(round_ks)
                    team.ts = StableValue(team.carried_ts + round_ts)
                    team.ks = StableValue(team.carried_ks + round_ks)
                if seen.alive is not None:
                    team.alive = StableValue(seen.alive)
                if seen.knocked is not None:
                    team.knocked = StableValue(seen.knocked)
                if seen.respawning is not None:
                    team.respawning = StableValue(seen.respawning)
                    if seen.respawning > 0:
                        team.respawn_grace_seconds = 10.0
                self._update_status(team, time.time())
            self.source = source
            self.revision += 1
            self.updated_at = time.time()

    def adjust_current_round(self, team_number: int, ts: float, ks: float) -> None:
        if not 1 <= team_number <= 8:
            raise ValueError("팀 번호가 올바르지 않습니다.")
        if any(value < 0 or value > 999.5 or value * 2 != round(value * 2) for value in (ts, ks)):
            raise ValueError("점수는 0.5 단위여야 합니다.")
        with self._lock:
            if not self.round_open:
                raise ValueError("진행 중인 라운드가 없습니다.")
            team = self.teams[team_number - 1]
            team.round_ts = StableValue(ts)
            team.round_ks = StableValue(ks)
            team.ts = StableValue(team.carried_ts + ts)
            team.ks = StableValue(team.carried_ks + ks)
            self.revision += 1
            self.updated_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def complete_round(self) -> dict:
        with self._lock:
            if not self.round_open:
                raise ValueError("진행 중인 라운드가 없습니다.")
            result = {
                "round": self.round_number,
                "teams": [
                    {
                        "team": team.team,
                        "ts": float(team.round_ts.value),
                        "ks": float(team.round_ks.value),
                        "penalty": 0.0,
                        "alive": int(team.alive.value),
                        "knocked": int(team.knocked.value),
                        "respawning": int(team.respawning.value),
                        "status": team.status,
                        "eliminatedAt": team.eliminated_at,
                    }
                    for team in self.teams
                ],
            }
            self.rounds.append(result)
            for team in self.teams:
                team.carried_ts = float(team.ts.value)
                team.carried_ks = float(team.ks.value)

            self.round_number += 1
            self.day = StableValue(1)
            for team in self.teams:
                team.round_ts = StableValue(0.0)
                team.round_ks = StableValue(0.0)
                team.alive = StableValue(3)
                team.knocked = StableValue(0)
                team.respawning = StableValue(0)
                team.status = "ACTIVE"
                team.eliminated_at = None
                team.respawn_grace_seconds = 0.0
            self.revision += 1
            self.updated_at = time.time()
            return deepcopy(result)

    def undo_complete_round(self) -> dict:
        with self._lock:
            if not self.rounds:
                raise ValueError("되돌릴 종료 라운드가 없습니다.")
            if self.round_open and any(
                float(team.round_ts.value) != 0 or float(team.round_ks.value) != 0 for team in self.teams
            ):
                raise ValueError("현재 라운드 점수가 있어 이전 라운드 종료를 되돌릴 수 없습니다.")

            restored = self.rounds.pop()
            self.round_number = int(restored["round"])
            self.round_open = True
            self.day = StableValue(1)
            for team in self.teams:
                saved = restored["teams"][team.team - 1]
                carried_ts = 0.0
                carried_ks = 0.0
                for completed in self.rounds:
                    completed_team = completed["teams"][team.team - 1]
                    carried_ts += float(completed_team["ts"]) - float(completed_team.get("penalty", 0.0))
                    carried_ks += float(completed_team["ks"])
                team.carried_ts = carried_ts
                team.carried_ks = carried_ks
                team.round_ts = StableValue(float(saved["ts"]))
                team.round_ks = StableValue(float(saved["ks"]))
                team.ts = StableValue(carried_ts + float(saved["ts"]))
                team.ks = StableValue(carried_ks + float(saved["ks"]))
                team.alive = StableValue(int(saved.get("alive", 3)))
                team.knocked = StableValue(int(saved.get("knocked", 0)))
                team.respawning = StableValue(int(saved.get("respawning", 0)))
                team.status = str(saved.get("status", "ACTIVE"))
                team.eliminated_at = saved.get("eliminatedAt")
                team.respawn_grace_seconds = 0.0
            self.revision += 1
            self.updated_at = time.time()
            return deepcopy(restored)

    def adjust_round(
        self,
        round_number: int,
        team_number: int,
        ts: float,
        ks: float,
        penalty: float,
    ) -> dict:
        if round_number < 1 or not 1 <= team_number <= 8:
            raise ValueError("라운드 또는 팀 번호가 올바르지 않습니다.")
        values = (ts, ks, penalty)
        if any(value < 0 or value > 999.5 or value * 2 != round(value * 2) for value in values):
            raise ValueError("점수와 패널티는 0.5 단위여야 합니다.")
        with self._lock:
            result = next((item for item in self.rounds if item["round"] == round_number), None)
            if result is None:
                raise ValueError("종료된 라운드만 수정할 수 있습니다.")
            saved = result["teams"][team_number - 1]
            previous_penalty = float(saved.get("penalty", 0.0))
            delta_ts = (ts - float(saved["ts"])) - (penalty - previous_penalty)
            delta_ks = ks - float(saved["ks"])
            saved["ts"] = ts
            saved["ks"] = ks
            saved["penalty"] = penalty
            team = self.teams[team_number - 1]
            team.carried_ts += delta_ts
            team.carried_ks += delta_ks
            team.ts = StableValue(float(team.ts.value) + delta_ts)
            team.ks = StableValue(float(team.ks.value) + delta_ks)
            self.revision += 1
            self.updated_at = time.time()
            return deepcopy(result)

    def set_tournament(self, tournament: dict, reset: bool = True) -> None:
        with self._lock:
            self.tournament = {
                "id": tournament["id"],
                "name": tournament["name"],
                "theme": deepcopy(tournament["theme"]),
            }
            for index, name in enumerate(tournament["teams"]):
                self.teams[index].name = name
            if reset:
                self._reset_locked()
            else:
                self.revision += 1
                self.updated_at = time.time()

    def set_team_names(self, team_names: list[str]) -> None:
        with self._lock:
            for index, name in enumerate(team_names):
                self.teams[index].name = name
            self.revision += 1
            self.updated_at = time.time()

    def _reset_locked(self) -> None:
        self.day = StableValue(1)
        self.round_number = 1
        self.round_open = True
        self.rounds = []
        for team in self.teams:
            team.ts = StableValue(0.0)
            team.ks = StableValue(0.0)
            team.round_ts = StableValue(0.0)
            team.round_ks = StableValue(0.0)
            team.carried_ts = 0.0
            team.carried_ks = 0.0
            team.alive = StableValue(3)
            team.knocked = StableValue(0)
            team.respawning = StableValue(0)
            team.status = "ACTIVE"
            team.eliminated_at = None
            team.respawn_grace_seconds = 0.0
        self.revision += 1
        self.updated_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            rows = []
            ordered_teams = sorted(
                self.teams,
                key=lambda team: (
                    -float(team.ts.value),
                    -float(team.ks.value),
                    team.status == "ELIMINATED",
                    -float(team.eliminated_at or 0.0) if team.status == "ELIMINATED" else 0.0,
                    team.team,
                ),
            )
            for team in ordered_teams:
                rows.append(
                    {
                        "team": team.team,
                        "name": team.name,
                        "color": team.color,
                        "ts": team.ts.value,
                        "ks": team.ks.value,
                        "roundTs": team.round_ts.value,
                        "roundKs": team.round_ks.value,
                        "alive": team.alive.value,
                        "knocked": team.knocked.value,
                        "respawning": team.respawning.value,
                        "status": team.status,
                    }
                )
            active_teams = sum(row["status"] != "ELIMINATED" for row in rows)
            alive_players = sum(int(row["alive"]) for row in rows if row["status"] != "ELIMINATED")
            return {
                "tournament": deepcopy(self.tournament),
                "day": self.day.value,
                "round": self.round_number,
                "roundOpen": self.round_open,
                "completedRounds": deepcopy(self.rounds),
                "teams": rows,
                "activeTeams": active_teams,
                "alivePlayers": alive_players,
                "revision": self.revision,
                "updatedAt": self.updated_at,
                "health": {
                    "source": self.source,
                    "processingMs": self.processing_ms,
                    "resolutionOk": self.resolution_ok,
                    "wipeMarkerRecent": time.time() - self.wipe_seen_at < 5,
                },
            }
