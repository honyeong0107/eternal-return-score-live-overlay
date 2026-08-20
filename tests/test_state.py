import unittest

from score_overlay.recognizer import HudObservation, TeamObservation
from score_overlay.state import ScoreState
from score_overlay.tournaments import DEFAULT_PROFILE


def observation(day: int, alive: int, respawning: int) -> HudObservation:
    teams = []
    for team in range(1, 9):
        teams.append(
            TeamObservation(
                team=team,
                ts=0.0,
                ks=0.0,
                alive=alive if team == 1 else 3,
                knocked=0,
                respawning=respawning if team == 1 else 0,
            )
        )
    return HudObservation(day=day, teams=tuple(teams), wipe_marker=False, resolution_ok=True)


def score_observation(ts: float, ks: float) -> HudObservation:
    teams = tuple(
        TeamObservation(team, ts if team == 1 else 0.0, ks if team == 1 else 0.0, 3, 0, 0)
        for team in range(1, 9)
    )
    return HudObservation(day=2, teams=teams, wipe_marker=False, resolution_ok=True)


class ScoreStateTest(unittest.TestCase):
    def apply_twice(self, state: ScoreState, value: HudObservation) -> None:
        state.apply(value, 1.0, "test")
        state.apply(value, 1.0, "test")

    def test_day_one_team_wipe_is_not_eliminated(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        self.apply_twice(state, observation(day=1, alive=0, respawning=0))
        self.assertEqual(state.teams[0].status, "BREAK")

    def test_respawn_timer_prevents_elimination_and_has_grace(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        self.apply_twice(state, observation(day=2, alive=0, respawning=1))
        self.assertEqual(state.teams[0].status, "BREAK")

        self.apply_twice(state, observation(day=2, alive=0, respawning=0))
        self.assertEqual(state.teams[0].status, "BREAK")

        for _ in range(7):
            state.apply(observation(day=2, alive=0, respawning=0), 1.0, "test")
        self.assertEqual(state.teams[0].status, "BREAK")
        state.apply(observation(day=2, alive=0, respawning=0), 1.0, "test")
        self.assertEqual(state.teams[0].status, "ELIMINATED")

    def test_elimination_is_permanent(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        self.apply_twice(state, observation(day=2, alive=0, respawning=0))
        self.assertEqual(state.teams[0].status, "ELIMINATED")

        self.apply_twice(state, observation(day=2, alive=3, respawning=0))
        self.assertEqual(state.teams[0].status, "ELIMINATED")

    def test_restarting_live_tracking_keeps_accumulated_score(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.apply_live_snapshot(score_observation(12.0, 8.0), "screen")
        state.apply_live_snapshot(score_observation(9.0, 6.0), "screen")
        self.assertEqual(state.teams[0].ts.value, 12.0)
        self.assertEqual(state.teams[0].ks.value, 8.0)

        state.apply_live_snapshot(score_observation(13.5, 9.0), "screen")
        self.assertEqual(state.teams[0].ts.value, 13.5)
        self.assertEqual(state.teams[0].ks.value, 9.0)

    def test_current_round_score_can_be_corrected_while_round_is_open(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.apply_live_snapshot(score_observation(60.5, 0.0), "screen")

        state.adjust_current_round(1, 6.0, 5.0)

        team = next(item for item in state.snapshot()["teams"] if item["team"] == 1)
        self.assertEqual((team["roundTs"], team["roundKs"]), (6.0, 5.0))
        self.assertEqual((team["ts"], team["ks"]), (6.0, 5.0))

    def test_current_round_correction_allows_ks_above_ts(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)

        state.adjust_current_round(1, 4.5, 5.0)

        team = next(item for item in state.snapshot()["teams"] if item["team"] == 1)
        self.assertEqual((team["roundTs"], team["roundKs"]), (4.5, 5.0))

    def test_score_tie_uses_current_round_survival_time(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.teams[0].eliminated_at = 100.0
        state.teams[0].status = "ELIMINATED"
        state.teams[1].eliminated_at = 200.0
        state.teams[1].status = "ELIMINATED"

        order = [item["team"] for item in state.snapshot()["teams"]]

        self.assertLess(order.index(3), order.index(2))
        self.assertLess(order.index(2), order.index(1))

    def test_completed_round_is_carried_into_next_round(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.apply_live_snapshot(score_observation(12.0, 8.0), "screen")
        state.complete_round()
        state.apply_live_snapshot(score_observation(3.5, 2.0), "screen")

        self.assertEqual(state.round_number, 2)
        self.assertEqual(state.teams[0].round_ts.value, 3.5)
        self.assertEqual(state.teams[0].ts.value, 15.5)
        self.assertEqual(state.teams[0].ks.value, 10.0)

    def test_round_adjustment_and_penalty_update_total_only(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.apply_live_snapshot(score_observation(12.0, 8.0), "screen")
        state.complete_round()
        state.adjust_round(1, 1, 13.0, 8.5, 1.5)

        saved = state.snapshot()["completedRounds"][0]["teams"][0]
        self.assertEqual(saved["ts"], 13.0)
        self.assertEqual(saved["ks"], 8.5)
        self.assertEqual(saved["penalty"], 1.5)
        self.assertEqual(state.teams[0].ts.value, 11.5)
        self.assertEqual(state.teams[0].ks.value, 8.5)

    def test_completed_round_correction_allows_ks_above_ts(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.complete_round()

        state.adjust_round(1, 1, 4.5, 5.0, 0.5)

        saved = state.snapshot()["completedRounds"][0]["teams"][0]
        self.assertEqual((saved["ts"], saved["ks"], saved["penalty"]), (4.5, 5.0, 0.5))

    def test_manual_score_corrections_require_half_point_steps(self) -> None:
        current = ScoreState(DEFAULT_PROFILE)
        with self.assertRaisesRegex(ValueError, "0.5 단위"):
            current.adjust_current_round(1, 4.25, 5.0)

        completed = ScoreState(DEFAULT_PROFILE)
        completed.complete_round()
        with self.assertRaisesRegex(ValueError, "0.5 단위"):
            completed.adjust_round(1, 1, 4.5, 5.0, 0.25)

    def test_tournament_round_limit_closes_last_round(self) -> None:
        profile = {**DEFAULT_PROFILE, "maxRounds": 2}
        state = ScoreState(profile)
        state.complete_round()
        self.assertTrue(state.round_open)
        state.complete_round()
        self.assertFalse(state.round_open)
        with self.assertRaisesRegex(ValueError, "2라운드"):
            state.complete_round()

    def test_undo_last_completed_round_restores_its_scores(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.apply_live_snapshot(score_observation(12.0, 8.0), "screen")
        state.complete_round()
        state.adjust_round(1, 1, 13.0, 8.5, 1.5)
        restored = state.undo_complete_round()

        self.assertEqual(restored["round"], 1)
        self.assertEqual(state.round_number, 1)
        self.assertTrue(state.round_open)
        self.assertEqual(state.teams[0].round_ts.value, 13.0)
        self.assertEqual(state.teams[0].ts.value, 13.0)
        self.assertEqual(state.snapshot()["completedRounds"], [])

    def test_undo_round_refuses_to_discard_new_round_score(self) -> None:
        state = ScoreState(DEFAULT_PROFILE)
        state.complete_round()
        state.apply_live_snapshot(score_observation(1.0, 1.0), "screen")
        with self.assertRaisesRegex(ValueError, "현재 라운드 점수"):
            state.undo_complete_round()


if __name__ == "__main__":
    unittest.main()
