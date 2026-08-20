import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from score_overlay.recognizer import HudObservation, TeamObservation
from score_overlay.state import ScoreState
from score_overlay.tournaments import TournamentStore
from score_overlay.web import OverlayServer


def observation(score: float | None) -> HudObservation:
    teams = tuple(
        TeamObservation(team, score if team == 1 else 0.0, score if team == 1 else 0.0, 3, 0, 0)
        if score is not None
        else TeamObservation(team, None, None, None, None, None)
        for team in range(1, 9)
    )
    return HudObservation(day=1, teams=teams, wipe_marker=False, resolution_ok=True)


class FakeLiveScore:
    def __init__(self, current: HudObservation):
        self.current = current
        self.tracking = False
        self.frame_cleared = False

    def parse(self, known_names: list[str]) -> tuple[list[str], HudObservation]:
        return known_names, self.current

    def start(self) -> None:
        self.tracking = True

    def stop(self) -> None:
        self.tracking = False

    def clear_frame(self) -> None:
        self.frame_cleared = True

    def is_tracking(self) -> bool:
        return self.tracking


class FakeCaptureSource:
    def __init__(self) -> None:
        self.selected = {"mode": "monitor", "hwnd": None, "title": None, "error": None}

    def windows(self) -> list[dict[str, str]]:
        return [{"hwnd": "22", "title": "Eternal Return"}]

    def snapshot(self) -> dict:
        return self.selected.copy()

    def select_window(self, hwnd: int) -> dict:
        if hwnd != 22:
            raise ValueError("선택한 창을 찾을 수 없습니다.")
        self.selected = {"mode": "window", "hwnd": "22", "title": "Eternal Return", "error": None}
        return self.snapshot()

    def use_monitor(self) -> dict:
        self.selected = {"mode": "monitor", "hwnd": None, "title": None, "error": None}
        return self.snapshot()


class LiveScoreApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = TournamentStore(Path(self.temporary.name) / "config.json")
        self.state = ScoreState(self.store.active())
        self.live = FakeLiveScore(observation(12.0))
        self.capture = FakeCaptureSource()
        self.server = OverlayServer(("127.0.0.1", 0), self.state, self.store, self.live, self.capture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def post(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(self.base + path, data=data, headers=headers, method="POST")
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def get(self, path: str) -> dict:
        with urlopen(self.base + path, timeout=3) as response:
            return json.load(response)

    def test_game_window_can_be_listed_and_selected(self) -> None:
        available = self.get("/api/capture/windows")
        self.assertEqual(available["windows"], [{"hwnd": "22", "title": "Eternal Return"}])

        self.live.start()
        selected = self.post("/api/capture/select", {"mode": "window", "hwnd": "22"})
        self.assertEqual(selected["selected"]["title"], "Eternal Return")
        self.assertFalse(self.live.tracking)
        self.assertTrue(self.live.frame_cleared)

    def test_active_tournament_can_be_deleted(self) -> None:
        saved = self.store.save(
            {
                "name": "Delete Me",
                "teams": [f"Team {index}" for index in range(1, 9)],
                "theme": {},
            }
        )
        self.state.set_tournament(saved, reset=True)
        self.live.start()

        result = self.post("/api/tournaments/delete", {"id": saved["id"]})

        self.assertEqual(result["profile"]["id"], "default")
        self.assertEqual(len(result["tournaments"]["tournaments"]), 1)
        self.assertFalse(self.live.tracking)

    def test_bebas_font_is_served_for_elimination_label(self) -> None:
        with urlopen(self.base + "/fonts/BebasNeue-Regular.ttf", timeout=3) as response:
            self.assertEqual(response.headers.get_content_type(), "font/ttf")
            self.assertGreater(len(response.read()), 0)

    def test_stop_and_restart_continue_from_accumulated_score(self) -> None:
        self.assertTrue(self.post("/api/live-score/start")["tracking"])
        self.assertEqual(self.state.teams[0].ts.value, 12.0)
        self.assertFalse(self.post("/api/live-score/stop")["tracking"])

        self.live.current = observation(9.0)
        self.assertTrue(self.post("/api/live-score/start")["tracking"])
        self.assertEqual(self.state.teams[0].ts.value, 12.0)

    def test_non_spectator_screen_does_not_start_or_replace_names(self) -> None:
        before = [team.name for team in self.state.teams]
        self.live.current = observation(None)
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/live-score/start")
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.live.tracking)
        self.assertEqual([team.name for team in self.state.teams], before)

    def test_round_adjustment_applies_penalty_without_extra_overlay_field(self) -> None:
        self.post("/api/live-score/start")
        self.post("/api/rounds/complete")
        result = self.post(
            "/api/rounds/adjust",
            {"round": 1, "team": 1, "ts": 13.0, "ks": 12.0, "penalty": 2.0},
        )

        team = next(item for item in result["state"]["teams"] if item["team"] == 1)
        self.assertEqual(team["ts"], 11.0)
        self.assertEqual(team["ks"], 12.0)
        self.assertNotIn("penalty", team)

    def test_current_round_score_can_be_changed_without_stopping_tracking(self) -> None:
        self.post("/api/live-score/start")

        result = self.post(
            "/api/live-score/adjust",
            {"team": 1, "ts": 6.0, "ks": 5.0},
        )

        team = next(item for item in result["state"]["teams"] if item["team"] == 1)
        self.assertEqual((team["roundTs"], team["roundKs"]), (6.0, 5.0))
        self.assertTrue(self.live.tracking)

    def test_round_completion_can_be_undone(self) -> None:
        self.post("/api/live-score/start")
        self.post("/api/rounds/complete")
        result = self.post("/api/rounds/undo")
        self.assertEqual(result["state"]["round"], 1)
        self.assertTrue(result["state"]["roundOpen"])
        self.assertEqual(result["state"]["completedRounds"], [])


if __name__ == "__main__":
    unittest.main()
