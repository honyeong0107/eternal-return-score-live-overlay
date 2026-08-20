from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .capture import WindowCaptureSource
from .live_score import LiveScoreCapture
from .state import ScoreState
from .tournaments import TournamentStore


STATIC = Path(__file__).with_name("static")


class OverlayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        state: ScoreState,
        tournaments: TournamentStore,
        live_score: LiveScoreCapture,
        capture_source: WindowCaptureSource | None = None,
    ):
        self.state = state
        self.tournaments = tournaments
        self.live_score = live_score
        self.capture_source = capture_source
        super().__init__(address, OverlayHandler)


class OverlayHandler(BaseHTTPRequestHandler):
    server: OverlayServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/api/state":
            snapshot = self.server.state.snapshot()
            snapshot["tracking"] = self.server.live_score.is_tracking()
            if self.server.capture_source is not None:
                snapshot["capture"] = self.server.capture_source.snapshot()
            self._json(snapshot)
            return
        if path == "/api/capture/windows":
            if self.server.capture_source is None:
                self._json({"windows": [], "selected": None})
            else:
                self._json(
                    {
                        "windows": self.server.capture_source.windows(),
                        "selected": self.server.capture_source.snapshot(),
                    }
                )
            return
        if path == "/api/tournaments":
            self._json(self.server.tournaments.snapshot())
            return
        if path in ("/", "/control"):
            self._file("control.html", "text/html; charset=utf-8")
            return
        if path == "/overlay":
            self._file("overlay.html", "text/html; charset=utf-8")
            return
        if path == "/overlay.css":
            self._file("overlay.css", "text/css; charset=utf-8")
            return
        if path == "/control.css":
            self._file("control.css", "text/css; charset=utf-8")
            return
        if path == "/control.js":
            self._file("control.js", "text/javascript; charset=utf-8")
            return
        if path == "/fonts/YuhanKimberly-Medium.otf":
            self._file("fonts/YuhanKimberly-Medium.otf", "font/otf")
            return
        if path == "/fonts/YuhanKimberly-Bold.otf":
            self._file("fonts/YuhanKimberly-Bold.otf", "font/otf")
            return
        if path == "/fonts/BebasNeue-Regular.ttf":
            self._file("fonts/BebasNeue-Regular.ttf", "font/ttf")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/reset":
                self.server.live_score.stop()
                self.server.state.reset()
                self._json({"ok": True})
                return
            if path == "/api/capture/select":
                if self.server.capture_source is None:
                    raise RuntimeError("창 캡처를 사용할 수 없습니다.")
                payload = self._read_json()
                self.server.live_score.stop()
                self.server.live_score.clear_frame()
                if str(payload.get("mode", "window")) == "monitor":
                    selected = self.server.capture_source.use_monitor()
                else:
                    selected = self.server.capture_source.select_window(int(payload.get("hwnd", 0)))
                self._json({"ok": True, "selected": selected, "tracking": False})
                return
            if path in ("/api/live-score", "/api/live-score/start"):
                if self.server.live_score.is_tracking():
                    self._json({"ok": True, "tracking": True, "state": self.server.state.snapshot()})
                    return
                active = self.server.tournaments.active()
                names, observation = self.server.live_score.parse(active["teams"])
                readable_scores = sum(
                    team.ts is not None and team.ks is not None
                    for team in observation.teams
                )
                if observation.day is None or readable_scores < 6:
                    raise RuntimeError("1920×1080 관전자 화면을 찾지 못했습니다.")
                profile = self.server.tournaments.update_active_teams(names)
                self.server.state.set_tournament(profile, reset=False)
                self.server.state.apply_live_snapshot(observation, self.server.state.source)
                self.server.live_score.start()
                self._json({"ok": True, "tracking": True, "teams": names, "state": self.server.state.snapshot()})
                return
            if path == "/api/live-score/stop":
                self.server.live_score.stop()
                self._json({"ok": True, "tracking": False, "state": self.server.state.snapshot()})
                return
            if path == "/api/live-score/adjust":
                payload = self._read_json()
                self.server.state.adjust_current_round(
                    int(payload.get("team", 0)),
                    float(payload.get("ts", -1)),
                    float(payload.get("ks", -1)),
                    float(payload.get("penalty", 0)),
                )
                self._json({"ok": True, "state": self.server.state.snapshot()})
                return
            if path == "/api/rounds/complete":
                self.server.live_score.stop()
                result = self.server.state.complete_round()
                self._json({"ok": True, "round": result, "state": self.server.state.snapshot()})
                return
            if path == "/api/rounds/adjust":
                payload = self._read_json()
                result = self.server.state.adjust_round(
                    int(payload.get("round", 0)),
                    int(payload.get("team", 0)),
                    float(payload.get("ts", -1)),
                    float(payload.get("ks", -1)),
                    float(payload.get("penalty", 0)),
                )
                self._json({"ok": True, "round": result, "state": self.server.state.snapshot()})
                return
            if path == "/api/rounds/undo":
                self.server.live_score.stop()
                result = self.server.state.undo_complete_round()
                self._json({"ok": True, "round": result, "state": self.server.state.snapshot()})
                return
            if path == "/api/tournaments/select":
                payload = self._read_json()
                self.server.live_score.stop()
                profile = self.server.tournaments.select(str(payload.get("id", "")))
                self.server.state.set_tournament(profile, reset=True)
                self._json({"ok": True, "profile": profile})
                return
            if path == "/api/tournaments/save":
                payload = self._read_json()
                previous_id = self.server.tournaments.active()["id"]
                profile = self.server.tournaments.save(payload)
                self.server.state.set_tournament(profile, reset=profile["id"] != previous_id)
                self._json({"ok": True, "profile": profile})
                return
            if path == "/api/tournaments/delete":
                payload = self._read_json()
                self.server.live_score.stop()
                profile = self.server.tournaments.delete(str(payload.get("id", "")))
                self.server.state.set_tournament(profile, reset=True)
                self._json(
                    {
                        "ok": True,
                        "profile": profile,
                        "tournaments": self.server.tournaments.snapshot(),
                    }
                )
                return
        except (ValueError, RuntimeError, json.JSONDecodeError) as error:
            self._json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            self._json(
                {"ok": False, "error": "처리 중 오류가 발생했습니다. 프로그램을 다시 실행해 보세요."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 100_000:
            raise ValueError("요청 내용이 비어 있거나 너무 큽니다.")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("요청 형식이 올바르지 않습니다.")
        return value

    def _json(self, value: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, name: str, content_type: str) -> None:
        payload = (STATIC / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return
