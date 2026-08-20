import json
import tempfile
import unittest
from pathlib import Path

from score_overlay.tournaments import TournamentStore


class TournamentStoreTest(unittest.TestCase):
    def test_legacy_team_config_is_migrated_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"teams": [f"Team {index}" for index in range(1, 9)]}), encoding="utf-8")
            store = TournamentStore(path)
            self.assertEqual(store.active()["teams"][0], "Team 1")
            self.assertEqual(store.active()["theme"]["accent"], "#a8d8f0")
            self.assertEqual(store.active()["theme"]["surface"], "#ffffff")
            self.assertEqual(store.active()["theme"]["line"], "#000000")
            self.assertEqual(store.active()["theme"]["elimination"], "#86aabd")

    def test_profiles_are_saved_and_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = TournamentStore(path)
            saved = store.save(
                {
                    "name": "Summer Cup",
                    "maxRounds": 6,
                    "teams": [f"Club {index}" for index in range(1, 9)],
                    "theme": {
                        "title": "FINAL",
                        "accent": "#44ccff",
                        "surface": "#050708",
                        "text": "#ffffff",
                        "muted": "#aabbcc",
                        "ks": "#dddddd",
                        "rank": "#44ccff",
                        "rankText": "#001122",
                        "line": "#334455",
                    },
                }
            )
            self.assertEqual(saved["id"], "summer-cup")
            self.assertEqual(saved["maxRounds"], 6)
            self.assertEqual(saved["theme"]["surface"], "#ffffff")
            self.assertEqual(saved["theme"]["line"], "#000000")
            self.assertEqual(TournamentStore(path).active()["name"], "Summer Cup")

    def test_invalid_team_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            with self.assertRaisesRegex(ValueError, "정확히 8개"):
                store.save({"name": "Bad", "teams": ["Only one"], "theme": {}})

    def test_active_profile_can_be_deleted_and_falls_back_to_remaining_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            saved = store.save(
                {
                    "name": "Delete Me",
                    "teams": [f"Team {index}" for index in range(1, 9)],
                    "theme": {},
                }
            )

            selected = store.delete(saved["id"])

            self.assertEqual(selected["id"], "default")
            self.assertEqual(len(store.snapshot()["tournaments"]), 1)

    def test_last_profile_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TournamentStore(Path(directory) / "config.json")
            with self.assertRaisesRegex(ValueError, "최소 한 개"):
                store.delete("default")


if __name__ == "__main__":
    unittest.main()
