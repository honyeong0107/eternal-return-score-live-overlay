import unittest

from score_overlay.live_score import LiveScoreCapture


class LiveScoreCaptureTest(unittest.TestCase):
    def test_stop_and_restart_toggle_tracking_without_reset(self) -> None:
        capture = LiveScoreCapture()
        self.assertFalse(capture.is_tracking())

        capture.start()
        self.assertTrue(capture.is_tracking())

        capture.stop()
        self.assertFalse(capture.is_tracking())

        capture.start()
        self.assertTrue(capture.is_tracking())

if __name__ == "__main__":
    unittest.main()
