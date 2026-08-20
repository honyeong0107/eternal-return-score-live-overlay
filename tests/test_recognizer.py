import unittest

import cv2
import numpy as np

from score_overlay.recognizer import HudRecognizer, SCORE_DIGITS


class RespawnTimerTest(unittest.TestCase):
    def test_large_portrait_digits_are_detected(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        line = 795
        center_x = 31
        cv2.rectangle(frame, (center_x - 11, line + 110), (center_x - 3, line + 128), (255, 255, 255), -1)
        cv2.rectangle(frame, (center_x + 3, line + 110), (center_x + 13, line + 128), (255, 255, 255), -1)
        self.assertTrue(HudRecognizer._has_respawn_timer(frame, line, 0, center_x))

    def test_bright_shape_at_portrait_edge_is_ignored(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        line = 795
        center_x = 31
        cv2.rectangle(frame, (center_x - 20, line + 110), (center_x - 10, line + 128), (255, 255, 255), -1)
        self.assertFalse(HudRecognizer._has_respawn_timer(frame, line, 0, center_x))


class ZeroScoreTest(unittest.TestCase):
    def test_single_zero_is_a_valid_score(self) -> None:
        self.assertEqual(HudRecognizer._components_to_score([(100, "0", 0.0)]), 0.0)

    def test_selected_team_uses_its_shifted_ts_ks_boundary(self) -> None:
        roi = np.zeros((20, 178, 3), dtype=np.uint8)
        glyph = SCORE_DIGITS["0"].astype(bool)
        roi[7:18, 137:144][glyph] = 255
        roi[7:18, 171:178][glyph] = 255

        self.assertEqual(HudRecognizer()._read_score_line(roi, split_x=145), (0.0, 0.0))

    def test_selected_team_keeps_two_digit_scores_in_their_columns(self) -> None:
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for x, digit in ((104, "6"), (117, "0"), (150, "5"), (163, "0")):
            glyph = SCORE_DIGITS[digit].astype(bool)
            frame[772:783, x : x + 7][glyph] = 255

        observed = HudRecognizer()._read_team(frame, 1, 0)

        self.assertEqual((observed.ts, observed.ks), (6.0, 5.0))


if __name__ == "__main__":
    unittest.main()
