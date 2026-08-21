from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field

from .recognizer import HudObservation, TeamObservation


TEAM_COLORS = ("#d9004d", "#ee7411", "#d8ad00", "#d6c5a4", "#ec6385", "#d900aa", "#bc3c5c", "#baff00")
TERMINAL_STATUSES = {"ELIMINATED", "ESCAPE"}
ROUND_STATUSES = {"ACTIVE", "BREAK", "ELIMINATED", "ESCAPE"}
STATUS_ALIASES = {"TERMINATED": "ELIMINATED"}
CHECKPOINT_COUNTS = {5: 2, 6: 3, 7: 5, 8: 7}
ESCAPE_CONFIRMATIONS = 3


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
    round_penalty: float = 0.0
    carried_ts: float = 0.0
    carried_ks: float = 0.0
    alive: StableValue = field(default_factory=lambda: StableValue(3))
    knocked: StableValue = field(default_factory=lambda: StableValue(0))
    respawning: StableValue = field(default_factory=lambda: StableValue(0))
    escaped: StableValue = field(default_factory=lambda: StableValue(False))
    status: str = "ACTIVE"
    manual_status: str | None = None
    eliminated_at: float | None = None
    escaped_at: float | None = None
    respawn_grace_seconds: float = 0.0


class ScoreState:
    def __init__(self, tournament: dict | list[str]):
        self._lock = threading.Lock()
        if isinstance(tournament, list):
            tournament = {
                "id": "default",
                "name": "기본 대회",
                "checkpointEnabled": False,
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
            "checkpointEnabled": bool(tournament.get("checkpointEnabled", False)),
            "theme": deepcopy(tournament["theme"]),
        }
        self.day = StableValue(1)
        self.round_number = 1
        self.round_open = False
        self.rounds: list[dict] = []
        self.score_adjustments: dict[int, list[list[dict]]] = {}
        self.checkpoint_teams: list[int] = []
        self.champion_team: int | None = None
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
            if not self.round_open:
                return
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
            changed |= self._update_champion_locked()

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
                    team.ts = StableValue(
                        team.carried_ts + float(team.round_ts.value) - team.round_penalty
                    )
                    team.ks = StableValue(team.carried_ks + float(team.round_ks.value))
                    changed = True
        changed |= team.alive.observe(seen.alive)
        changed |= team.knocked.observe(seen.knocked)
        changed |= team.respawning.observe(seen.respawning)
        changed |= team.escaped.observe(seen.escaped, confirmations=ESCAPE_CONFIRMATIONS)
        return changed

    def _update_status(self, team: TeamState, now: float) -> bool:
        old = team.status
        if team.manual_status is not None:
            team.status = team.manual_status
            return old != team.status
        alive = int(team.alive.value)
        day = int(self.day.value)
        if team.escaped_at is not None or bool(team.escaped.value):
            team.status = "ESCAPE"
            if team.escaped_at is None:
                team.escaped_at = now
        elif team.eliminated_at is not None:
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
                    team.ts = StableValue(team.carried_ts + round_ts - team.round_penalty)
                    team.ks = StableValue(team.carried_ks + round_ks)
                if seen.alive is not None:
                    team.alive = StableValue(seen.alive)
                if seen.knocked is not None:
                    team.knocked = StableValue(seen.knocked)
                if seen.respawning is not None:
                    team.respawning = StableValue(seen.respawning)
                    if seen.respawning > 0:
                        team.respawn_grace_seconds = 10.0
                if seen.escaped is not None:
                    team.escaped.observe(seen.escaped, confirmations=ESCAPE_CONFIRMATIONS)
                self._update_status(team, time.time())
            self._update_champion_locked()
            self.source = source
            self.revision += 1
            self.updated_at = time.time()

    def begin_round(self) -> None:
        with self._lock:
            if self.round_open:
                return
            self.round_open = True
            self.revision += 1
            self.updated_at = time.time()

    @staticmethod
    def _validated_score_changes(changes: list[dict]) -> list[dict]:
        if not changes:
            raise ValueError("수정할 점수가 없습니다.")
        normalized = []
        seen_teams = set()
        for change in changes:
            team_number = int(change.get("team", 0))
            status = str(change.get("status", "")).strip().upper() or None
            status = STATUS_ALIASES.get(status, status)
            values = (
                float(change.get("ts", -1)),
                float(change.get("ks", -1)),
                float(change.get("penalty", 0)),
            )
            if not 1 <= team_number <= 8 or team_number in seen_teams:
                raise ValueError("팀 번호가 올바르지 않습니다.")
            if any(value < 0 or value > 999.5 or value * 2 != round(value * 2) for value in values):
                raise ValueError("점수와 패널티는 0.5 단위여야 합니다.")
            if status is not None and status not in ROUND_STATUSES:
                raise ValueError("라운드 상태가 올바르지 않습니다.")
            seen_teams.add(team_number)
            normalized.append(
                {
                    "team": team_number,
                    "ts": values[0],
                    "ks": values[1],
                    "penalty": values[2],
                    "status": status,
                }
            )
        return normalized

    @staticmethod
    def _current_round_values(team: TeamState) -> dict:
        return {
            "team": team.team,
            "ts": float(team.round_ts.value),
            "ks": float(team.round_ks.value),
            "penalty": team.round_penalty,
            "alive": int(team.alive.value),
            "knocked": int(team.knocked.value),
            "respawning": int(team.respawning.value),
            "escaped": bool(team.escaped.value),
            "status": team.status,
            "manualStatus": team.manual_status,
            "eliminatedAt": team.eliminated_at,
            "escapedAt": team.escaped_at,
            "respawnGraceSeconds": team.respawn_grace_seconds,
        }

    @staticmethod
    def _apply_team_status(team: TeamState, status: str, now: float) -> None:
        if status == "ACTIVE":
            team.alive = StableValue(max(1, int(team.alive.value)))
            team.respawning = StableValue(0)
            team.escaped = StableValue(False)
            team.eliminated_at = None
            team.escaped_at = None
            team.respawn_grace_seconds = 0.0
        elif status == "BREAK":
            team.alive = StableValue(0)
            team.respawning = StableValue(max(1, int(team.respawning.value)))
            team.escaped = StableValue(False)
            team.eliminated_at = None
            team.escaped_at = None
            team.respawn_grace_seconds = max(10.0, team.respawn_grace_seconds)
        elif status == "ELIMINATED":
            team.alive = StableValue(0)
            team.respawning = StableValue(0)
            team.escaped = StableValue(False)
            team.eliminated_at = team.eliminated_at or now
            team.escaped_at = None
            team.respawn_grace_seconds = 0.0
        else:
            team.escaped = StableValue(True)
            team.eliminated_at = None
            team.escaped_at = team.escaped_at or now
            team.respawn_grace_seconds = 0.0
        team.status = status
        team.manual_status = status

    @staticmethod
    def _apply_saved_status(saved: dict, status: str, now: float) -> None:
        if status == "ACTIVE":
            saved.update(
                alive=max(1, int(saved.get("alive", 0))),
                respawning=0,
                escaped=False,
                status=status,
                manualStatus=status,
                eliminatedAt=None,
                escapedAt=None,
            )
        elif status == "BREAK":
            saved.update(
                alive=0,
                respawning=max(1, int(saved.get("respawning", 0))),
                escaped=False,
                status=status,
                manualStatus=status,
                eliminatedAt=None,
                escapedAt=None,
            )
        elif status == "ELIMINATED":
            saved.update(
                alive=0,
                respawning=0,
                escaped=False,
                status=status,
                manualStatus=status,
                eliminatedAt=saved.get("eliminatedAt") or now,
                escapedAt=None,
            )
        else:
            saved.update(
                escaped=True,
                status=status,
                manualStatus=status,
                eliminatedAt=None,
                escapedAt=saved.get("escapedAt") or now,
            )

    def adjust_current_round(self, team_number: int, ts: float, ks: float, penalty: float) -> None:
        self.adjust_current_round_batch(
            [{"team": team_number, "ts": ts, "ks": ks, "penalty": penalty}]
        )

    def adjust_current_round_batch(self, changes: list[dict]) -> None:
        normalized = self._validated_score_changes(changes)
        with self._lock:
            if not self.round_open:
                raise ValueError("진행 중인 라운드가 없습니다.")
            previous = []
            for change in normalized:
                team = self.teams[change["team"] - 1]
                previous.append(self._current_round_values(team))
                team.round_ts = StableValue(change["ts"])
                team.round_ks = StableValue(change["ks"])
                team.round_penalty = change["penalty"]
                team.ts = StableValue(team.carried_ts + change["ts"] - change["penalty"])
                team.ks = StableValue(team.carried_ks + change["ks"])
                if change["status"] is not None:
                    self._apply_team_status(team, change["status"], time.time())
            self.score_adjustments.setdefault(self.round_number, []).append(previous)
            self.revision += 1
            self.updated_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def complete_round(self) -> dict:
        with self._lock:
            if not self.round_open:
                raise ValueError("진행 중인 라운드가 없습니다.")
            winner_team = self._round_winner_locked()
            self._update_champion_locked()
            result = {
                "round": self.round_number,
                "checkpointTeams": list(self.checkpoint_teams),
                "winnerTeam": winner_team,
                "teams": [
                    {
                        "team": team.team,
                        "ts": float(team.round_ts.value),
                        "ks": float(team.round_ks.value),
                        "penalty": team.round_penalty,
                        "alive": int(team.alive.value),
                        "knocked": int(team.knocked.value),
                        "respawning": int(team.respawning.value),
                        "escaped": bool(team.escaped.value),
                        "status": team.status,
                        "manualStatus": team.manual_status,
                        "eliminatedAt": team.eliminated_at,
                        "escapedAt": team.escaped_at,
                    }
                    for team in self.teams
                ],
            }
            self.rounds.append(result)
            for team in self.teams:
                team.carried_ts = float(team.ts.value)
                team.carried_ks = float(team.ks.value)

            self.round_number += 1
            self.round_open = False
            self.checkpoint_teams = self._checkpoint_teams_for_round_locked(self.round_number)
            self.day = StableValue(1)
            for team in self.teams:
                team.round_ts = StableValue(0.0)
                team.round_ks = StableValue(0.0)
                team.round_penalty = 0.0
                team.alive = StableValue(3)
                team.knocked = StableValue(0)
                team.respawning = StableValue(0)
                team.escaped = StableValue(False)
                team.status = "ACTIVE"
                team.manual_status = None
                team.eliminated_at = None
                team.escaped_at = None
                team.respawn_grace_seconds = 0.0
            self.revision += 1
            self.updated_at = time.time()
            return deepcopy(result)

    def undo_complete_round(self) -> dict:
        with self._lock:
            if not self.rounds:
                raise ValueError("되돌릴 종료 라운드가 없습니다.")
            if self.round_open and any(
                float(team.round_ts.value) != 0
                or float(team.round_ks.value) != 0
                or team.round_penalty != 0
                for team in self.teams
            ):
                raise ValueError("현재 라운드 점수가 있어 이전 라운드 종료를 되돌릴 수 없습니다.")

            restored = self.rounds.pop()
            self.round_number = int(restored["round"])
            restored_checkpoint_teams = restored.get("checkpointTeams")
            if self.tournament["checkpointEnabled"]:
                self.checkpoint_teams = (
                    list(restored_checkpoint_teams)
                    if restored_checkpoint_teams is not None
                    else self._checkpoint_teams_for_round_locked(self.round_number)
                )
            else:
                self.checkpoint_teams = []
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
                team.round_penalty = float(saved.get("penalty", 0.0))
                team.ts = StableValue(carried_ts + float(saved["ts"]) - team.round_penalty)
                team.ks = StableValue(carried_ks + float(saved["ks"]))
                team.alive = StableValue(int(saved.get("alive", 3)))
                team.knocked = StableValue(int(saved.get("knocked", 0)))
                team.respawning = StableValue(int(saved.get("respawning", 0)))
                team.escaped = StableValue(bool(saved.get("escaped", False)))
                team.status = str(saved.get("status", "ACTIVE"))
                team.manual_status = saved.get("manualStatus")
                team.eliminated_at = saved.get("eliminatedAt")
                team.escaped_at = saved.get("escapedAt")
                team.respawn_grace_seconds = 0.0
            self.champion_team = self._completed_champion_locked()
            self._update_champion_locked()
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
        return self.adjust_round_batch(
            round_number,
            [{"team": team_number, "ts": ts, "ks": ks, "penalty": penalty}],
        )

    def adjust_round_batch(self, round_number: int, changes: list[dict]) -> dict:
        if round_number < 1:
            raise ValueError("라운드 또는 팀 번호가 올바르지 않습니다.")
        normalized = self._validated_score_changes(changes)
        with self._lock:
            result = next((item for item in self.rounds if item["round"] == round_number), None)
            if result is None:
                raise ValueError("종료된 라운드만 수정할 수 있습니다.")
            previous = []
            for change in normalized:
                saved = result["teams"][change["team"] - 1]
                previous.append(deepcopy(saved))
                previous_penalty = float(saved.get("penalty", 0.0))
                delta_ts = (change["ts"] - float(saved["ts"])) - (
                    change["penalty"] - previous_penalty
                )
                delta_ks = change["ks"] - float(saved["ks"])
                saved["ts"] = change["ts"]
                saved["ks"] = change["ks"]
                saved["penalty"] = change["penalty"]
                team = self.teams[change["team"] - 1]
                team.carried_ts += delta_ts
                team.carried_ks += delta_ks
                team.ts = StableValue(float(team.ts.value) + delta_ts)
                team.ks = StableValue(float(team.ks.value) + delta_ks)
                if change["status"] is not None:
                    self._apply_saved_status(saved, change["status"], time.time())
            self.score_adjustments.setdefault(round_number, []).append(previous)
            self.revision += 1
            self.updated_at = time.time()
            return deepcopy(result)

    def undo_score_adjustment(self, round_number: int) -> dict:
        with self._lock:
            history = self.score_adjustments.get(round_number)
            if not history:
                raise ValueError("되돌릴 점수 수정이 없습니다.")
            completed = next((item for item in self.rounds if item["round"] == round_number), None)
            current = self.round_open and self.round_number == round_number
            if not current and completed is None:
                raise ValueError("선택한 라운드를 찾을 수 없습니다.")

            previous = history.pop()
            if not history:
                del self.score_adjustments[round_number]
            if current:
                for saved in previous:
                    team = self.teams[saved["team"] - 1]
                    team.round_ts = StableValue(saved["ts"])
                    team.round_ks = StableValue(saved["ks"])
                    team.round_penalty = saved["penalty"]
                    team.ts = StableValue(team.carried_ts + saved["ts"] - saved["penalty"])
                    team.ks = StableValue(team.carried_ks + saved["ks"])
                    team.alive = StableValue(saved["alive"])
                    team.knocked = StableValue(saved["knocked"])
                    team.respawning = StableValue(saved["respawning"])
                    team.escaped = StableValue(saved["escaped"])
                    team.status = saved["status"]
                    team.manual_status = saved["manualStatus"]
                    team.eliminated_at = saved["eliminatedAt"]
                    team.escaped_at = saved["escapedAt"]
                    team.respawn_grace_seconds = saved["respawnGraceSeconds"]
            else:
                for previous_score in previous:
                    saved = completed["teams"][previous_score["team"] - 1]
                    current_penalty = float(saved.get("penalty", 0.0))
                    delta_ts = (previous_score["ts"] - float(saved["ts"])) - (
                        previous_score["penalty"] - current_penalty
                    )
                    delta_ks = previous_score["ks"] - float(saved["ks"])
                    saved.clear()
                    saved.update(deepcopy(previous_score))
                    team = self.teams[previous_score["team"] - 1]
                    team.carried_ts += delta_ts
                    team.carried_ks += delta_ks
                    team.ts = StableValue(float(team.ts.value) + delta_ts)
                    team.ks = StableValue(float(team.ks.value) + delta_ks)
            self.revision += 1
            self.updated_at = time.time()
            return {"round": round_number, "teams": deepcopy(previous)}

    def set_tournament(self, tournament: dict, reset: bool = True) -> None:
        with self._lock:
            checkpoint_was_enabled = self.tournament["checkpointEnabled"]
            self.tournament = {
                "id": tournament["id"],
                "name": tournament["name"],
                "checkpointEnabled": bool(tournament.get("checkpointEnabled", False)),
                "theme": deepcopy(tournament["theme"]),
            }
            for index, name in enumerate(tournament["teams"]):
                self.teams[index].name = name
            if reset:
                self._reset_locked()
            else:
                if not self.tournament["checkpointEnabled"]:
                    self.checkpoint_teams = []
                    self.champion_team = None
                elif not checkpoint_was_enabled:
                    self.checkpoint_teams = self._checkpoint_teams_for_round_locked(
                        self.round_number
                    )
                    self.champion_team = self._completed_champion_locked()
                    self._update_champion_locked()
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
        self.round_open = False
        self.rounds = []
        self.score_adjustments = {}
        self.checkpoint_teams = []
        self.champion_team = None
        for team in self.teams:
            team.ts = StableValue(0.0)
            team.ks = StableValue(0.0)
            team.round_ts = StableValue(0.0)
            team.round_ks = StableValue(0.0)
            team.round_penalty = 0.0
            team.carried_ts = 0.0
            team.carried_ks = 0.0
            team.alive = StableValue(3)
            team.knocked = StableValue(0)
            team.respawning = StableValue(0)
            team.escaped = StableValue(False)
            team.status = "ACTIVE"
            team.manual_status = None
            team.eliminated_at = None
            team.escaped_at = None
            team.respawn_grace_seconds = 0.0
        self.revision += 1
        self.updated_at = time.time()

    def _checkpoint_teams_for_round_locked(self, round_number: int) -> list[int]:
        if not self.tournament["checkpointEnabled"] or round_number < 5:
            return []
        count = 8 if round_number >= 9 else CHECKPOINT_COUNTS.get(round_number, 0)
        totals = {team.team: [0.0, 0.0] for team in self.teams}
        for completed in self.rounds:
            for saved in completed["teams"]:
                totals[int(saved["team"])][0] += float(saved["ts"]) - float(
                    saved.get("penalty", 0.0)
                )
                totals[int(saved["team"])][1] += float(saved["ks"])
        ranked = sorted(totals, key=lambda team: (-totals[team][0], -totals[team][1], team))
        return ranked[:count]

    def _round_winner_locked(self) -> int | None:
        remaining = [
            team.team for team in self.teams if team.status not in TERMINAL_STATUSES
        ]
        return remaining[0] if len(remaining) == 1 else None

    def _completed_champion_locked(self) -> int | None:
        for completed in self.rounds:
            winner = completed.get("winnerTeam")
            if winner in completed.get("checkpointTeams", []):
                return int(winner)
        return None

    def _update_champion_locked(self) -> bool:
        if self.champion_team is not None or not self.tournament["checkpointEnabled"]:
            return False
        winner = self._round_winner_locked()
        if winner not in self.checkpoint_teams:
            return False
        self.champion_team = winner
        return True

    def snapshot(self) -> dict:
        with self._lock:
            rows = []
            ordered_teams = sorted(
                self.teams,
                key=lambda team: (
                    team.team != self.champion_team,
                    -float(team.ts.value),
                    -float(team.ks.value),
                    team.status in TERMINAL_STATUSES,
                    -float(team.escaped_at or team.eliminated_at or 0.0)
                    if team.status in TERMINAL_STATUSES
                    else 0.0,
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
                        "roundPenalty": team.round_penalty,
                        "alive": team.alive.value,
                        "knocked": team.knocked.value,
                        "respawning": team.respawning.value,
                        "escaped": team.escaped.value,
                        "status": team.status,
                        "checkpoint": team.team in self.checkpoint_teams,
                        "champion": team.team == self.champion_team,
                    }
                )
            active_teams = sum(row["status"] not in TERMINAL_STATUSES for row in rows)
            alive_players = sum(
                int(row["alive"]) for row in rows if row["status"] not in TERMINAL_STATUSES
            )
            return {
                "tournament": deepcopy(self.tournament),
                "day": self.day.value,
                "round": self.round_number,
                "roundOpen": self.round_open,
                "completedRounds": deepcopy(self.rounds),
                "scoreUndoRounds": sorted(self.score_adjustments),
                "checkpointTeams": list(self.checkpoint_teams),
                "championTeam": self.champion_team,
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
