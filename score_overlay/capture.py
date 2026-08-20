from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str


def list_capturable_windows() -> list[WindowInfo]:
    """Return visible top-level Windows that can be selected as a capture target."""

    user32 = ctypes.windll.user32
    windows: list[WindowInfo] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.strip()
        if title:
            windows.append(WindowInfo(int(hwnd), title))
        return True

    user32.EnumWindows(collect, 0)
    unique = {window.hwnd: window for window in windows}
    return sorted(
        unique.values(),
        key=lambda window: (not _is_eternal_return(window.title), window.title.casefold()),
    )


def _is_eternal_return(title: str) -> bool:
    folded = title.casefold().strip()
    return folded in {"eternal return", "이터널 리턴"}


class WindowCaptureSource:
    """Low-rate Windows Graphics Capture source for one selected application window."""

    def __init__(
        self,
        fps: float,
        window_provider: Callable[[], list[WindowInfo]] = list_capturable_windows,
        capture_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._window_provider = window_provider
        self._capture_factory = capture_factory
        self._interval_ms = max(100, round(1000 / fps))
        self._condition = threading.Condition()
        self._selected: WindowInfo | None = None
        self._backend: Any | None = None
        self._control: Any | None = None
        self._frame: np.ndarray | None = None
        self._sequence = 0
        self._generation = 0
        self._error = ""

    def windows(self) -> list[dict[str, str]]:
        return [
            {"hwnd": str(window.hwnd), "title": window.title}
            for window in self._window_provider()
        ]

    def snapshot(self) -> dict[str, str | None]:
        with self._condition:
            return {
                "mode": "window" if self._selected else "monitor",
                "hwnd": str(self._selected.hwnd) if self._selected else None,
                "title": self._selected.title if self._selected else None,
                "error": self._error or None,
            }

    def select_preferred(self, query: str) -> dict[str, str | None]:
        windows = self._window_provider()
        if not windows:
            return self.snapshot()
        if query.casefold() == "auto":
            selected = next((window for window in windows if _is_eternal_return(window.title)), None)
        else:
            folded = query.casefold().strip()
            matches = [window for window in windows if folded in window.title.casefold()]
            selected = matches[0] if matches else None
        return self.select_window(selected.hwnd) if selected else self.snapshot()

    def select_window(self, hwnd: int) -> dict[str, str | None]:
        selected = next((window for window in self._window_provider() if window.hwnd == hwnd), None)
        if selected is None:
            raise ValueError("선택한 창을 찾을 수 없습니다. 목록을 새로고침해 주세요.")

        with self._condition:
            if self._selected == selected and self._control is not None:
                return {
                    "mode": "window",
                    "hwnd": str(selected.hwnd),
                    "title": selected.title,
                    "error": self._error or None,
                }
            old_backend = self._backend
            old_control = self._control
            old_selected = self._selected
            old_generation = self._generation
            self._generation += 1
            generation = self._generation
            self._selected = selected
            self._backend = None
            self._control = None
            self._frame = None
            self._error = ""

        factory = self._capture_factory
        if factory is None:
            try:
                from windows_capture import WindowsCapture
            except ImportError as error:
                raise RuntimeError("창 캡처 모듈이 없습니다. start.bat을 다시 실행하세요.") from error
            factory = WindowsCapture

        try:
            backend = factory(
                cursor_capture=False,
                draw_border=False,
                minimum_update_interval=self._interval_ms,
                window_hwnd=selected.hwnd,
            )
        except Exception:
            with self._condition:
                self._selected = old_selected
                self._backend = old_backend
                self._control = old_control
                self._generation = old_generation
                self._condition.notify_all()
            raise RuntimeError("선택한 창을 캡처하지 못했습니다.")

        @backend.event
        def on_frame_arrived(frame: Any, _capture_control: Any) -> None:
            pixels = np.asarray(frame.frame_buffer)[:, :, :3].copy()
            with self._condition:
                if generation != self._generation:
                    return
                self._frame = pixels
                self._sequence += 1
                self._condition.notify_all()

        @backend.event
        def on_closed() -> None:
            with self._condition:
                if generation != self._generation:
                    return
                self._error = "선택한 게임 창의 캡처가 종료됐습니다."
                self._frame = None
                self._condition.notify_all()

        try:
            control = backend.start_free_threaded()
        except Exception:
            with self._condition:
                self._selected = old_selected
                self._backend = old_backend
                self._control = old_control
                self._generation = old_generation
                self._condition.notify_all()
            raise RuntimeError("선택한 창을 캡처하지 못했습니다.")

        with self._condition:
            self._backend = backend
            self._control = control
            self._condition.notify_all()
        if old_control is not None:
            old_control.stop()
        return self.snapshot()

    def use_monitor(self) -> dict[str, str | None]:
        with self._condition:
            control = self._control
            self._generation += 1
            self._selected = None
            self._backend = None
            self._control = None
            self._frame = None
            self._error = ""
            self._condition.notify_all()
        if control is not None:
            control.stop()
        return self.snapshot()

    def read_frame(self, after_sequence: int, timeout: float) -> tuple[int, np.ndarray | None]:
        with self._condition:
            if self._selected is None:
                return self._sequence, None
            self._condition.wait_for(
                lambda: self._sequence > after_sequence or self._selected is None or bool(self._error),
                timeout=max(0.0, timeout),
            )
            return self._sequence, self._frame

    def stop(self) -> None:
        with self._condition:
            control = self._control
            self._generation += 1
            self._backend = None
            self._control = None
            self._condition.notify_all()
        if control is not None:
            control.stop()
