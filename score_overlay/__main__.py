from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np

from .capture import WindowCaptureSource
from .recognizer import HudRecognizer
from .live_score import LiveScoreCapture
from .state import ScoreState
from .tournaments import TournamentStore
from .web import OverlayServer


def monitor_capture(monitor: int):
    try:
        import mss
    except ImportError as error:
        raise RuntimeError("화면 캡처에는 mss가 필요합니다: pip install -r requirements.txt") from error
    capture = mss.MSS()
    if monitor >= len(capture.monitors):
        capture.close()
        raise ValueError(f"모니터 {monitor}을 찾을 수 없습니다. 사용 가능: 1-{len(capture.monitors)-1}")
    return capture, capture.monitors[monitor]


def analyze_loop(
    args: argparse.Namespace,
    state: ScoreState,
    live_score: LiveScoreCapture,
    capture_source: WindowCaptureSource,
    stop: threading.Event,
) -> None:
    recognizer = HudRecognizer()
    period = 1.0 / args.fps
    monitor, region = monitor_capture(args.monitor)
    sequence = -1
    try:
        while not stop.is_set():
            cycle_started = time.perf_counter()
            capture_state = capture_source.snapshot()
            if capture_state["mode"] == "window":
                sequence, frame = capture_source.read_frame(sequence, period)
                source = "window"
                if frame is None:
                    continue
            else:
                frame = np.asarray(monitor.grab(region))[:, :, :3]
                source = "screen"

            live_score.update_frame(frame)
            if live_score.is_tracking():
                started = time.perf_counter()
                observation = recognizer.analyze(frame)
                elapsed_ms = (time.perf_counter() - started) * 1000
                state.apply(observation, elapsed_ms, source, period)
            time.sleep(max(0.0, period - (time.perf_counter() - cycle_started)))
    finally:
        monitor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="이터널 리턴 경량 실시간 점수 오버레이")
    parser.add_argument("--fps", type=float, default=2.0, help="초당 HUD 인식 횟수 (저사양 권장 1.0)")
    parser.add_argument("--monitor", type=int, default=1, help="화면 캡처 모니터 번호")
    parser.add_argument(
        "--window",
        default="auto",
        help="캡처할 창 제목. auto는 실행 중인 이터널 리턴 창을 자동 선택합니다.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--open", action="store_true", help="제어 페이지를 기본 브라우저에서 열기")
    args = parser.parse_args()
    if not 0.25 <= args.fps <= 10:
        parser.error("--fps는 0.25에서 10 사이여야 합니다.")
    return args


def main() -> int:
    args = parse_args()
    tournaments = TournamentStore(args.config)
    state = ScoreState(tournaments.active())
    live_score = LiveScoreCapture()
    capture_source = WindowCaptureSource(args.fps)
    if args.window.casefold() not in {"", "none", "monitor"}:
        try:
            capture_source.select_preferred(args.window)
        except RuntimeError as error:
            print(f"창 자동 선택 실패: {error} 모니터 화면으로 시작합니다.")
    stop = threading.Event()
    worker = threading.Thread(
        target=analyze_loop,
        args=(args, state, live_score, capture_source, stop),
        daemon=True,
    )
    worker.start()

    server = OverlayServer((args.host, args.port), state, tournaments, live_score, capture_source)
    url = f"http://{args.host}:{args.port}"
    print(f"제어 페이지: {url}")
    print(f"OBS 브라우저 소스: {url}/overlay (800x600)")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        live_score.stop()
        capture_source.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
