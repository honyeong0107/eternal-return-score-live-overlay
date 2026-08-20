import unittest

import numpy as np

from score_overlay.capture import WindowCaptureSource, WindowInfo


class FakeControl:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeBackend:
    instances: list["FakeBackend"] = []

    def __init__(self, **options) -> None:
        self.options = options
        self.handlers = {}
        self.control = FakeControl()
        self.__class__.instances.append(self)

    def event(self, handler):
        self.handlers[handler.__name__] = handler
        return handler

    def start_free_threaded(self) -> FakeControl:
        return self.control

    def emit(self, pixels: np.ndarray) -> None:
        frame = type("Frame", (), {"frame_buffer": pixels})()
        self.handlers["on_frame_arrived"](frame, None)


class WindowCaptureSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeBackend.instances.clear()
        self.windows = [
            WindowInfo(11, "Mozilla Firefox"),
            WindowInfo(12, "이터널 리턴 라이브 스코어 — Mozilla Firefox"),
            WindowInfo(22, "Eternal Return"),
        ]
        self.source = WindowCaptureSource(
            fps=2.0,
            window_provider=lambda: self.windows,
            capture_factory=FakeBackend,
        )

    def tearDown(self) -> None:
        self.source.stop()

    def test_auto_selects_eternal_return_and_limits_capture_to_requested_fps(self) -> None:
        selected = self.source.select_preferred("auto")

        self.assertEqual(selected["hwnd"], "22")
        self.assertEqual(selected["title"], "Eternal Return")
        self.assertEqual(FakeBackend.instances[0].options["window_hwnd"], 22)
        self.assertEqual(FakeBackend.instances[0].options["minimum_update_interval"], 500)
        self.assertFalse(FakeBackend.instances[0].options["cursor_capture"])

    def test_switching_windows_stops_old_capture_and_returns_new_window_frame(self) -> None:
        self.source.select_window(22)
        old_backend = FakeBackend.instances[-1]
        self.source.select_window(11)
        new_backend = FakeBackend.instances[-1]

        self.assertTrue(old_backend.control.stopped)
        pixels = np.full((1080, 1920, 4), 127, dtype=np.uint8)
        new_backend.emit(pixels)
        sequence, frame = self.source.read_frame(-1, timeout=0)

        self.assertGreaterEqual(sequence, 1)
        self.assertEqual(frame.shape, (1080, 1920, 3))
        self.assertEqual(int(frame[0, 0, 0]), 127)


if __name__ == "__main__":
    unittest.main()
