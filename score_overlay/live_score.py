from __future__ import annotations

import gc
import re
import threading
from collections import Counter, deque
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from .recognizer import (
    HudObservation,
    HudRecognizer,
    TEAM_STARTS,
    TEAM_WIDTH,
    normalize_frame,
)


KOREAN_MODEL_PATH = Path(__file__).with_name("models") / "korean_PP-OCRv5_rec_mobile.onnx"
SELECTED_NAME_Y = 788
NORMAL_NAME_Y = 820
NAME_HEIGHT = 32
PRIMARY_NAME_LEFT = 56
KOREAN_NAME_LEFT = 60
SELECTED_KOREAN_NAME_LEFT = 44
NAME_HISTORY_SIZE = 3


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


def _clean_candidate(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^[^0-9A-Za-z가-힣]+", "", cleaned)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣.]+$", "", cleaned)
    return re.sub(r"^[A-Za-z](?=[가-힣])", "", cleaned).strip()


def _consensus_name(candidates: list[str]) -> str:
    usable = [candidate for candidate in candidates if _normalized(candidate)]
    if not usable:
        return ""
    counts = Counter(_normalized(candidate) for candidate in usable)
    best = max(counts, key=lambda key: (counts[key], len(key)))
    return next(candidate for candidate in usable if _normalized(candidate) == best)


class LiveScoreCapture:
    def __init__(self):
        self._lock = threading.Lock()
        self._recent_frames: deque[np.ndarray] = deque(maxlen=NAME_HISTORY_SIZE)
        self._tracking = False

    def update_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._recent_frames.append(frame.copy())

    def clear_frame(self) -> None:
        with self._lock:
            self._recent_frames.clear()

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
            if not self._recent_frames:
                raise RuntimeError("아직 읽을 수 있는 게임 화면이 없습니다.")
            source_frames = [frame.copy() for frame in self._recent_frames]

        recognizer = HudRecognizer()
        observations = [recognizer.analyze(frame) for frame in source_frames]
        frames = [normalize_frame(frame) for frame in source_frames]
        observation = observations[-1]

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
            raise RuntimeError("팀명 인식 모듈이 없습니다. start.bat을 다시 실행하세요.") from error

        primary = RapidOCR(use_text_det=False, use_angle_cls=False, text_score=0.25)
        candidates: list[list[str]] = [[] for _ in TEAM_STARTS]
        for frame, seen in reversed(list(zip(frames, observations))):
            for index, team in enumerate(seen.teams):
                crop = self._name_crop(
                    frame,
                    index,
                    team.selected,
                    PRIMARY_NAME_LEFT,
                    seen.hud_y_offset,
                )
                result, _ = primary(crop)
                parsed = self._pick_name(result)
                if parsed:
                    candidates[index].append(parsed)

        parsed_names = [_consensus_name(values) for values in candidates]

        missing = [index for index, name in enumerate(parsed_names) if not name]
        korean = None
        if missing and KOREAN_MODEL_PATH.exists():
            korean = RapidOCR(
                use_text_det=False,
                use_angle_cls=False,
                text_score=0.25,
                rec_model_path=str(KOREAN_MODEL_PATH),
            )
            for frame, seen in reversed(list(zip(frames, observations))):
                for index in missing:
                    team = seen.teams[index]
                    left = SELECTED_KOREAN_NAME_LEFT if team.selected else KOREAN_NAME_LEFT
                    crop = self._name_crop(
                        frame,
                        index,
                        team.selected,
                        left,
                        seen.hud_y_offset,
                    )
                    result, _ = korean(crop)
                    parsed = self._pick_name(result)
                    if parsed:
                        candidates[index].append(parsed)
            for index in missing:
                parsed_names[index] = _consensus_name(candidates[index])

        parsed_names = [
            _best_known_name(parsed, known_names) if parsed else known_names[index]
            for index, parsed in enumerate(parsed_names)
        ]

        del primary
        if korean is not None:
            del korean
        gc.collect()
        return parsed_names, observation

    @staticmethod
    def _name_crop(
        frame: np.ndarray,
        index: int,
        selected: bool,
        left: int,
        hud_y_offset: int = 0,
    ) -> np.ndarray:
        x0 = TEAM_STARTS[index]
        x1 = TEAM_STARTS[index + 1] if index + 1 < len(TEAM_STARTS) else x0 + TEAM_WIDTH
        y0 = (SELECTED_NAME_Y if selected else NORMAL_NAME_Y) + hud_y_offset
        return frame[y0 : y0 + NAME_HEIGHT, x0 + left : x1]

    @staticmethod
    def _pick_name(result: list | None) -> str:
        if not result:
            return ""
        candidates: list[tuple[float, str]] = []
        for item in result:
            box, text, confidence = item
            value = _clean_candidate(str(text))
            if not value or float(confidence) < 0.45:
                continue
            compact = value.upper().replace(" ", "")
            if "TS" in compact or "KS" in compact or compact.isdigit():
                continue
            width = float(box[1][0]) - float(box[0][0])
            candidates.append((width * float(confidence), value))
        return max(candidates, default=(0.0, ""))[1]
