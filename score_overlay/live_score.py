from __future__ import annotations

import gc
import re
import threading
from difflib import SequenceMatcher

import numpy as np

from .recognizer import HudObservation, HudRecognizer, TEAM_STARTS, TEAM_WIDTH


def _normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())


def _best_known_name(candidate: str, known_names: list[str]) -> str:
    compact = _normalized(candidate)
    if not compact:
        return candidate.strip()
    best_name = candidate.strip()
    best_ratio = 0.0
    for known in known_names:
        ratio = SequenceMatcher(None, compact, _normalized(known)).ratio()
        if ratio > best_ratio:
            best_name = known
            best_ratio = ratio
    return best_name if best_ratio >= 0.72 else candidate.strip()


class LiveScoreCapture:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._tracking = False

    def update_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest_frame = frame.copy()

    def clear_frame(self) -> None:
        with self._lock:
            self._latest_frame = None

    def start(self) -> None:
        with self._lock:
            self._tracking = True

    def stop(self) -> None:
        with self._lock:
            self._tracking = False

    def is_tracking(self) -> bool:
        with self._lock:
            return self._tracking

    def parse(self, known_names: list[str]) -> tuple[list[str], HudObservation]:
        with self._lock:
            if self._latest_frame is None:
                raise RuntimeError("아직 읽을 수 있는 게임 화면이 없습니다.")
            frame = self._latest_frame.copy()

        observation = HudRecognizer().analyze(frame)

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
            raise RuntimeError("팀명 인식 모듈이 없습니다. start.bat을 다시 실행하세요.") from error

        engine = RapidOCR()
        parsed_names: list[str] = []
        for index, x0 in enumerate(TEAM_STARTS):
            result, _ = engine(frame[790:855, x0 : x0 + TEAM_WIDTH])
            parsed = self._pick_name(result)
            parsed_names.append(_best_known_name(parsed, known_names) if parsed else known_names[index])

        del engine
        gc.collect()
        return parsed_names, observation

    @staticmethod
    def _pick_name(result: list | None) -> str:
        if not result:
            return ""
        candidates: list[tuple[float, str]] = []
        for item in result:
            box, text, confidence = item
            value = str(text).strip()
            if not value or float(confidence) < 0.45:
                continue
            compact = value.upper().replace(" ", "")
            if "TS" in compact or "KS" in compact or compact.isdigit():
                continue
            width = float(box[1][0]) - float(box[0][0])
            candidates.append((width * float(confidence), value))
        return max(candidates, default=(0.0, ""))[1]
