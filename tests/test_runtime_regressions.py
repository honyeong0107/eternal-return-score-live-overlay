# Powered by Honyeong
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from copy import deepcopy
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from score_overlay.recognizer import (
    SCORE_LINES,
    HudObservation,
    HudRecognizer,
    TeamObservation,
)
from score_overlay.state import ScoreState
from score_overlay.tournaments import DEFAULT_PROFILE, TournamentStore
from score_overlay.web import OverlayServer


TEAM_ONE_SCORE_FIXTURE = (
    Path(__file__).parents[1]
    / "video-inspection-2026-08-19"
    / "keyframes"
    / "full-312.jpg"
)


def _bgr_from_hsv(hue: int) -> tuple[int, int, int]:
    pixel = np.uint8([[[hue, 255, 255]]])
    bgr = cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]
    return tuple(int(value) for value in bgr)


def _terminal_frame(hue: int, icon_count: int = 3) -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    line = 795
    glyph_y = line + 7
    for center_x in (31, 88, 145)[:icon_count]:
        frame[
            glyph_y + 37 : glyph_y + 76,
            center_x - 14 : center_x + 15,
        ] = _bgr_from_hsv(hue)
    return frame


@unittest.skipUnless(TEAM_ONE_SCORE_FIXTURE.exists(), "team 1 score fixture is unavailable")
class TeamOneScoreRegressionTests(unittest.TestCase):
    def test_team_one_score_survives_small_horizontal_hud_offset(self) -> None:
        frame = cv2.imdecode(
            np.fromfile(TEAM_ONE_SCORE_FIXTURE, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        shifted = cv2.warpAffine(
            frame,
            np.float32([[1, 0, 3], [0, 1, 0]]),
            (frame.shape[1], frame.shape[0]),
            borderMode=cv2.BORDER_REPLICATE,
        )

        teams = HudRecognizer().analyze(shifted).teams

        self.assertTrue(all(team.ts is not None and team.ks is not None for team in teams[1:]))
        self.assertEqual((6.0, 4.0), (teams[0].ts, teams[0].ks))


class TerminalStatusRegressionTests(unittest.TestCase):
    def test_terminated_teams_are_detected_with_both_spectator_hud_positions(self) -> None:
        fixture_path = Path(__file__).with_name("fixtures") / "terminated_team_hud.png"
        if not fixture_path.exists():
            self.skipTest("current HUD fixture is not included in the public repository")
        hud = cv2.imdecode(
            np.fromfile(fixture_path, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(hud)

        for hud_y_offset in (0, 55):
            with self.subTest(hud_y_offset=hud_y_offset):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                top = 750 + hud_y_offset
                frame[top : top + hud.shape[0], : hud.shape[1]] = hud

                observation = HudRecognizer().analyze(frame)
                alive = {team.team: team.alive for team in observation.teams}

                self.assertEqual(hud_y_offset, observation.hud_y_offset)
                self.assertEqual(0, alive[1])
                self.assertEqual(0, alive[3])
                self.assertEqual(3, alive[2])

    def test_portrait_status_colors_do_not_look_like_death_skulls(self) -> None:
        recognizer = HudRecognizer()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        line = SCORE_LINES[1]
        glyph_y = line + 7
        magenta = _bgr_from_hsv(170)

        # Normal portrait/status colors can appear above a portrait and along
        # its lower edge, but they do not form the skull's lower silhouette.
        for center_x in (31, 88, 145):
            frame[
                glyph_y + 38 : glyph_y + 44,
                center_x - 2 : center_x + 3,
            ] = magenta
            frame[
                glyph_y + 48 : glyph_y + 52,
                center_x - 3 : center_x + 4,
            ] = magenta
            frame[
                glyph_y + 66 : glyph_y + 68,
                center_x - 6 : center_x + 7,
            ] = magenta

        alive, knocked, respawning, escaped, _ = recognizer._read_team_status(
            frame, line, 0
        )

        self.assertEqual(3, alive)
        self.assertEqual(0, knocked)
        self.assertEqual(0, respawning)
        self.assertFalse(escaped)

    def test_selected_team_color_bar_does_not_eliminate_living_player(self) -> None:
        recognizer = HudRecognizer()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        line = SCORE_LINES[0]
        glyph_y = line + 7
        magenta = _bgr_from_hsv(170)

        # A selected team's magenta accent crosses all three portrait columns.
        # Only the first two columns contain the upper part of a death skull.
        for center_x in (31, 88, 145):
            frame[
                glyph_y + 69 : glyph_y + 76,
                center_x - 14 : center_x + 15,
            ] = magenta
        for center_x in (31, 88):
            frame[
                glyph_y + 56 : glyph_y + 64,
                center_x - 2 : center_x + 3,
            ] = magenta

        alive, knocked, respawning, escaped, _ = recognizer._read_team_status(
            frame, line, 0
        )

        self.assertEqual(1, alive)
        self.assertEqual(0, knocked)
        self.assertEqual(0, respawning)
        self.assertFalse(escaped)

        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        observation = HudObservation(
            5,
            (TeamObservation(1, 5.0, 3.0, alive, 0, 0, selected=True),),
            False,
            True,
        )
        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        team = next(row for row in state.snapshot()["teams"] if row["team"] == 1)
        self.assertEqual("ACTIVE", team["status"])

    def test_elimination_icons_survive_score_read_failure(self) -> None:
        recognizer = HudRecognizer()
        frame = _terminal_frame(170)

        def read_team(_frame, team, _x0, score_lines=SCORE_LINES):
            if score_lines != SCORE_LINES or team == 1:
                return TeamObservation(team, None, None, None, None, None)
            return TeamObservation(
                team,
                0.0,
                0.0,
                3,
                0,
                0,
                selected=team == 2,
                escaped=False,
            )

        with patch.object(recognizer, "_read_team", side_effect=read_team):
            observed = recognizer.analyze(frame).teams[0]

        self.assertIsNone(observed.ts)
        self.assertIsNone(observed.ks)
        self.assertEqual(0, observed.alive)
        self.assertFalse(observed.escaped)

        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        observation = HudObservation(2, (observed,), False, True)
        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        team = next(row for row in state.snapshot()["teams"] if row["team"] == 1)
        self.assertEqual("ELIMINATED", team["status"])

    def test_two_frame_zero_alive_glitch_does_not_eliminate_living_team(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        living = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 2, 0, 0, escaped=False),),
            False,
            True,
        )
        zero_alive = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 0, 0, 0, escaped=False),),
            False,
            True,
        )
        state.apply(living, 1.0, "test", sample_seconds=0.2)
        state.apply(living, 1.0, "test", sample_seconds=0.2)

        state.apply(zero_alive, 1.0, "test", sample_seconds=0.2)
        state.apply(zero_alive, 1.0, "test", sample_seconds=0.2)
        team = state.teams[6]
        self.assertEqual(2, team.alive.value)
        self.assertEqual("ACTIVE", team.status)

        state.apply(zero_alive, 1.0, "test", sample_seconds=0.2)
        self.assertEqual("ELIMINATED", team.status)

    def test_confirmed_respawn_break_remains_after_timer_disappears(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        living = HudObservation(
            2,
            (TeamObservation(1, 0.0, 0.0, 3, 0, 0, escaped=False),),
            False,
            True,
        )
        respawning = HudObservation(
            2,
            (TeamObservation(1, 0.0, 0.0, 0, 0, 1, escaped=False),),
            False,
            True,
        )
        no_timer = HudObservation(
            2,
            (TeamObservation(1, 0.0, 0.0, 0, 0, 0, escaped=False),),
            False,
            True,
        )

        state.apply(living, 1.0, "test", sample_seconds=0.2)
        state.apply(living, 1.0, "test", sample_seconds=0.2)
        for _ in range(3):
            state.apply(respawning, 1.0, "test", sample_seconds=0.2)
        self.assertEqual("BREAK", state.teams[0].status)

        for _ in range(12):
            state.apply(no_timer, 1.0, "test", sample_seconds=1.0)

        team = state.teams[0]
        self.assertEqual(0, team.respawning.value)
        self.assertEqual(0.0, team.respawn_grace_seconds)
        self.assertEqual("BREAK", team.status)

        state.apply(living, 1.0, "test", sample_seconds=0.2)
        state.apply(living, 1.0, "test", sample_seconds=0.2)
        self.assertEqual("ACTIVE", team.status)

    def test_confirmed_living_team_recovers_from_automatic_elimination(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        eliminated = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 0, 0, 0, escaped=False),),
            False,
            True,
        )
        living = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 3, 0, 0, escaped=False),),
            False,
            True,
        )

        state.apply(eliminated, 1.0, "test")
        state.apply(eliminated, 1.0, "test")
        state.apply(eliminated, 1.0, "test")
        self.assertEqual("ELIMINATED", state.teams[6].status)
        state.apply(living, 1.0, "test")
        state.apply(living, 1.0, "test")

        team = state.teams[6]
        self.assertEqual(3, team.alive.value)
        self.assertEqual("ACTIVE", team.status)
        self.assertIsNone(team.eliminated_at)

    def test_setting_active_returns_team_to_automatic_status_detection(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        state.adjust_current_round_batch(
            [{"team": 7, "ts": 11.5, "ks": 4.5, "penalty": 0.0, "status": "ACTIVE"}]
        )
        eliminated = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 0, 0, 0, escaped=False),),
            False,
            True,
        )

        state.apply(eliminated, 1.0, "test")
        state.apply(eliminated, 1.0, "test")
        state.apply(eliminated, 1.0, "test")

        team = state.teams[6]
        self.assertIsNone(team.manual_status)
        self.assertEqual("ELIMINATED", team.status)

    def test_escape_icons_survive_score_read_failure(self) -> None:
        recognizer = HudRecognizer()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        line = 765
        glyph_y = line + 7
        for center_x in (31, 88):
            frame[
                glyph_y + 37 : glyph_y + 76,
                center_x - 14 : center_x + 15,
            ] = _bgr_from_hsv(55)

        def read_team(_frame, team, _x0, score_lines=SCORE_LINES):
            if score_lines != SCORE_LINES or team == 1:
                return TeamObservation(team, None, None, None, None, None)
            return TeamObservation(team, 0.0, 0.0, 3, 0, 0, escaped=False)

        with patch.object(recognizer, "_read_team", side_effect=read_team):
            observed = recognizer.analyze(frame).teams[0]

        self.assertIsNone(observed.ts)
        self.assertIsNone(observed.ks)
        self.assertTrue(observed.escaped)

    def test_two_escape_observations_confirm_status(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        observation = HudObservation(
            day=2,
            teams=(TeamObservation(1, None, None, 3, 0, 0, escaped=True),),
            wipe_marker=False,
            resolution_ok=True,
        )

        state.apply(observation, 1.0, "test", sample_seconds=0.2)
        state.apply(observation, 1.0, "test", sample_seconds=0.2)

        team = next(row for row in state.snapshot()["teams"] if row["team"] == 1)
        self.assertEqual("ESCAPE", team["status"])

    def test_status_fallback_uses_the_confirmed_hud_line(self) -> None:
        recognizer = HudRecognizer()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        glyph_y = 765 + 7
        for center_x in (31, 88, 145):
            frame[
                glyph_y + 37 : glyph_y + 67,
                center_x - 14 : center_x + 15,
            ] = _bgr_from_hsv(170)

        def read_team(_frame, team, _x0, score_lines=SCORE_LINES):
            if score_lines != SCORE_LINES or team == 1:
                return TeamObservation(team, None, None, None, None, None)
            return TeamObservation(
                team,
                0.0,
                0.0,
                3,
                0,
                0,
                selected=team == 2,
                escaped=False,
            )

        with patch.object(recognizer, "_read_team", side_effect=read_team):
            observed = recognizer.analyze(frame).teams[0]

        self.assertIsNone(observed.alive)
        self.assertIsNone(observed.escaped)


class RuntimePersistenceRegressionTests(unittest.TestCase):
    def test_undo_complete_round_reopens_with_zero_round_score(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        state.adjust_current_round_batch(
            [{"team": 1, "ts": 4.0, "ks": 2.0, "penalty": 0.0}]
        )
        state.complete_round()
        state.begin_round()
        state.adjust_current_round_batch(
            [
                {
                    "team": 1,
                    "ts": 10.0,
                    "ks": 5.0,
                    "penalty": 1.0,
                    "status": "TERMINATED",
                }
            ]
        )
        state.complete_round()

        state.undo_complete_round()

        team = state.teams[0]
        self.assertEqual(2, state.round_number)
        self.assertTrue(state.round_open)
        self.assertEqual(1, state.day.value)
        self.assertEqual((0.0, 0.0), (team.round_ts.value, team.round_ks.value))
        self.assertEqual((4.0, 2.0), (team.ts.value, team.ks.value))
        self.assertNotIn(2, state.score_adjustments)
        for current in state.teams:
            with self.subTest(team=current.team):
                self.assertEqual((0.0, 0.0, 0.0), (
                    current.round_ts.value,
                    current.round_ks.value,
                    current.round_penalty,
                ))
                self.assertEqual((3, 0, 0, False), (
                    current.alive.value,
                    current.knocked.value,
                    current.respawning.value,
                    current.escaped.value,
                ))
                self.assertEqual("ACTIVE", current.status)
                self.assertIsNone(current.manual_status)
                self.assertIsNone(current.eliminated_at)
                self.assertIsNone(current.escaped_at)
                self.assertEqual(0.0, current.respawn_grace_seconds)

    def test_legacy_saved_active_status_returns_to_automatic_detection(self) -> None:
        state = ScoreState(deepcopy(DEFAULT_PROFILE))
        state.begin_round()
        session = state.export_session()
        session["teams"][6]["manualStatus"] = "ACTIVE"

        restored = ScoreState(deepcopy(DEFAULT_PROFILE))
        self.assertTrue(restored.restore_session(session))
        eliminated = HudObservation(
            2,
            (TeamObservation(7, 11.5, 4.5, 0, 0, 0, escaped=False),),
            False,
            True,
        )
        restored.apply(eliminated, 1.0, "test")
        restored.apply(eliminated, 1.0, "test")
        restored.apply(eliminated, 1.0, "test")

        team = restored.teams[6]
        self.assertIsNone(team.manual_status)
        self.assertEqual("ELIMINATED", team.status)

    def test_completed_and_open_rounds_restore_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            store = TournamentStore(config_path)
            state = ScoreState(store.active())
            state.set_change_callback(store.save_session)

            state.begin_round()
            state.adjust_current_round_batch(
                [{"team": 1, "ts": 10.0, "ks": 5.0, "penalty": 0.5, "status": "TERMINATED"}]
            )
            state.complete_round()
            state.begin_round()
            state.adjust_current_round_batch(
                [{"team": 2, "ts": 3.0, "ks": 1.0, "penalty": 0.0, "status": "ESCAPE"}]
            )

            reopened_store = TournamentStore(config_path)
            restored = ScoreState(reopened_store.active())
            self.assertTrue(restored.restore_session(reopened_store.load_session()))

            before = state.snapshot()
            after = restored.snapshot()
            self.assertEqual(before["round"], after["round"])
            self.assertEqual(before["roundOpen"], after["roundOpen"])
            self.assertEqual(before["completedRounds"], after["completedRounds"])
            self.assertEqual(before["scoreUndoRounds"], after["scoreUndoRounds"])
            self.assertEqual(before["checkpointTeams"], after["checkpointTeams"])
            self.assertEqual(before["championTeam"], after["championTeam"])
            self.assertEqual(
                [
                    (row["team"], row["ts"], row["ks"], row["status"])
                    for row in before["teams"]
                ],
                [
                    (row["team"], row["ts"], row["ks"], row["status"])
                    for row in after["teams"]
                ],
            )

            restored.undo_score_adjustment(2)
            current_team = next(
                row for row in restored.snapshot()["teams"] if row["team"] == 2
            )
            self.assertEqual((0.0, 0.0, "ACTIVE"), (
                current_team["roundTs"],
                current_team["roundKs"],
                current_team["status"],
            ))

            restored.undo_complete_round()
            reopened_round = restored.snapshot()
            restored_team = next(row for row in reopened_round["teams"] if row["team"] == 1)
            self.assertEqual(1, reopened_round["round"])
            self.assertTrue(reopened_round["roundOpen"])
            self.assertEqual((0.0, 0.0, "ACTIVE"), (
                restored_team["ts"],
                restored_team["ks"],
                restored_team["status"],
            ))

    def test_switching_tournaments_restores_each_tournaments_rounds_and_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            store = TournamentStore(config_path)
            tournament_b = deepcopy(DEFAULT_PROFILE)
            tournament_b.update({"id": "tournament-b", "name": "Tournament B"})
            store.save(tournament_b)
            store.select("default")

            state = ScoreState(store.active())
            state.set_change_callback(store.save_session)
            state.begin_round()
            state.adjust_current_round_batch(
                [{"team": 1, "ts": 10.0, "ks": 5.0, "penalty": 0.0}]
            )
            state.complete_round()
            tournament_a_snapshot = state.snapshot()

            live_score = _FakeLiveScore([])
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)

            def select_tournament(tournament_id: str) -> None:
                connection.request(
                    "POST",
                    "/api/tournaments/select",
                    body=json.dumps({"id": tournament_id}),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()

            try:
                select_tournament("tournament-b")
                state.begin_round()
                state.adjust_current_round_batch(
                    [{"team": 2, "ts": 20.0, "ks": 2.0, "penalty": 0.0}]
                )
                state.complete_round()
                tournament_b_snapshot = state.snapshot()

                select_tournament("default")
                restored_a = state.snapshot()
                self.assertEqual(
                    tournament_a_snapshot["completedRounds"],
                    restored_a["completedRounds"],
                )
                self.assertEqual(
                    [(team["team"], team["ts"], team["ks"]) for team in tournament_a_snapshot["teams"]],
                    [(team["team"], team["ts"], team["ks"]) for team in restored_a["teams"]],
                )

                select_tournament("tournament-b")
                restored_b = state.snapshot()
                self.assertEqual(
                    tournament_b_snapshot["completedRounds"],
                    restored_b["completedRounds"],
                )
                self.assertEqual(
                    [(team["team"], team["ts"], team["ks"]) for team in tournament_b_snapshot["teams"]],
                    [(team["team"], team["ts"], team["ks"]) for team in restored_b["teams"]],
                )
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            reopened_store = TournamentStore(config_path)
            reopened_state = ScoreState(reopened_store.active())
            self.assertTrue(reopened_state.restore_session(reopened_store.load_session()))
            self.assertEqual(
                tournament_b_snapshot["completedRounds"],
                reopened_state.snapshot()["completedRounds"],
            )

            profile_a = reopened_store.select("default")
            reopened_state.set_tournament(
                profile_a,
                reset=True,
                session=reopened_store.load_session(),
            )
            self.assertEqual(
                tournament_a_snapshot["completedRounds"],
                reopened_state.snapshot()["completedRounds"],
            )

    def test_legacy_single_score_session_migrates_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            state = ScoreState(deepcopy(DEFAULT_PROFILE))
            state.begin_round()
            state.adjust_current_round_batch(
                [{"team": 1, "ts": 10.0, "ks": 5.0, "penalty": 0.0}]
            )
            state.complete_round()
            legacy_session = state.export_session()
            config_path.write_text(
                json.dumps(
                    {
                        "activeTournament": "default",
                        "tournaments": [DEFAULT_PROFILE],
                        "scoreSession": legacy_session,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            store = TournamentStore(config_path)
            loaded_session = store.load_session()
            restored = ScoreState(store.active())
            self.assertTrue(restored.restore_session(loaded_session))
            self.assertEqual(
                state.snapshot()["completedRounds"],
                restored.snapshot()["completedRounds"],
            )
            store.save_session(loaded_session)

            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("scoreSession", migrated)
            self.assertEqual(loaded_session, migrated["scoreSessions"]["default"])


class _FakeLiveScore:
    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.tracking = False
        self.recognize_names_calls: list[bool] = []

    def is_tracking(self) -> bool:
        return self.tracking

    def parse(
        self,
        known_names: list[str],
        recognize_names: bool = True,
    ) -> tuple[list[str], HudObservation]:
        self.recognize_names_calls.append(recognize_names)
        names = self.names if recognize_names else known_names
        return names, HudObservation(
            day=1,
            teams=tuple(
                TeamObservation(team, 0.0, 0.0, 3, 0, 0)
                for team in range(1, 9)
            ),
            wipe_marker=False,
            resolution_ok=True,
        )

    def start(self) -> None:
        self.tracking = True

    def stop(self) -> None:
        self.tracking = False


class ResetTeamNamesRegressionTests(unittest.TestCase):
    def test_only_unconfigured_names_are_filled_by_first_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            profile = store.active()
            profile["teams"] = ["Configured 1", "", "Configured 3", "", "", "", "", ""]
            profile["teamNamesConfigured"] = [True, False, True, False, False, False, False, False]
            store.save(profile)

            recognized_names = [f"Recognized {team}" for team in range(1, 9)]
            state = ScoreState(store.active())
            live_score = _FakeLiveScore(recognized_names)
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("POST", "/api/live-score/start")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()
                teams = sorted(state.snapshot()["teams"], key=lambda row: row["team"])
                self.assertEqual(
                    [
                        "Configured 1",
                        "Recognized 2",
                        "Configured 3",
                        "Recognized 4",
                        "Recognized 5",
                        "Recognized 6",
                        "Recognized 7",
                        "Recognized 8",
                    ],
                    [team["name"] for team in teams],
                )
                self.assertEqual([True], live_score.recognize_names_calls)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_legacy_generated_team_labels_remain_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            profile = store.active()
            profile["teams"] = [f"TEAM {team}" for team in range(1, 9)]
            store.save(profile)
            self.assertEqual(
                [False] * 8,
                store.active()["teamNamesConfigured"],
            )

            recognized_names = [f"Recognized {team}" for team in range(1, 9)]
            state = ScoreState(store.active())
            live_score = _FakeLiveScore(recognized_names)
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("POST", "/api/live-score/start")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()
                teams = sorted(state.snapshot()["teams"], key=lambda row: row["team"])
                self.assertEqual(recognized_names, [team["name"] for team in teams])
                self.assertEqual([True], live_score.recognize_names_calls)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_configured_names_are_not_replaced_on_first_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            configured_names = [f"Configured {team}" for team in range(1, 9)]
            profile = store.active()
            profile["teams"] = configured_names
            profile["teamNamesConfigured"] = [True] * 8
            store.save(profile)

            state = ScoreState(store.active())
            live_score = _FakeLiveScore([f"OCR {team}" for team in range(1, 9)])
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("POST", "/api/live-score/start")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()
                teams = sorted(state.snapshot()["teams"], key=lambda row: row["team"])
                self.assertEqual(configured_names, [team["name"] for team in teams])
                self.assertEqual([False], live_score.recognize_names_calls)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_first_live_names_are_reused_when_the_next_round_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            first_names = [f"Round 1 OCR {team}" for team in range(1, 9)]
            state = ScoreState(store.active())
            live_score = _FakeLiveScore(first_names)
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("POST", "/api/live-score/start")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()
                teams = sorted(state.snapshot()["teams"], key=lambda row: row["team"])
                self.assertEqual(first_names, [team["name"] for team in teams])
                self.assertEqual([True], live_score.recognize_names_calls)

                connection.request("POST", "/api/rounds/complete")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()

                live_score.names = [f"Round 2 OCR {team}" for team in range(1, 9)]
                connection.request("POST", "/api/live-score/start")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                response.read()
                teams = sorted(state.snapshot()["teams"], key=lambda row: row["team"])
                self.assertEqual(first_names, [team["name"] for team in teams])
                self.assertEqual([True, False], live_score.recognize_names_calls)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_reset_restores_configured_names_after_ocr_names_are_shown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            configured_names = [f"Configured {team}" for team in range(1, 9)]
            ocr_names = [f"OCR {team}" for team in range(1, 9)]
            state = ScoreState(store.active())
            live_score = _FakeLiveScore(ocr_names)
            server = OverlayServer(("127.0.0.1", 0), state, store, live_score)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("POST", "/api/live-score/start")
                self.assertEqual(200, connection.getresponse().status)
                self.assertEqual(ocr_names, [team["name"] for team in state.snapshot()["teams"]])

                profile = store.active()
                profile["teams"] = configured_names
                profile["teamNamesConfigured"] = [True] * 8
                store.save(profile)
                connection.request("POST", "/api/reset")
                self.assertEqual(200, connection.getresponse().status)
                self.assertEqual(
                    configured_names,
                    [team["name"] for team in state.snapshot()["teams"]],
                )
                self.assertEqual(configured_names, store.active()["teams"])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
