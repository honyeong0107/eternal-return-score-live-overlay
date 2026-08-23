# Powered by Honyeong
from __future__ import annotations

import gc
import re
import threading
from collections import Counter, deque
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
NAME_LEFTS = (70, 44)
NAME_Y_OFFSETS = (0, -4)
NAME_HISTORY_SIZE = 10


def _normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())


def _best_known_name(candidate: str, known_names: list[str]) -> str:
    compact = _normalized(candidate)
    if not compact:
        return candidate.strip()
    for known in known_names:
        known_compact = _normalized(known)
        if compact == known_compact:
            return candidate.strip()
        if len(known_compact) == len(compact) + 1 and any(
            known_compact[:index] + known_compact[index + 1 :] == compact
            for index in range(1, len(known_compact) - 1)
        ):
            return known
        if len(compact) == len(known_compact) + 1 and any(
            compact[:index] + compact[index + 1 :] == known_compact
            for index in range(1, len(compact) - 1)
        ):
            return known
    return candidate.strip()


def _clean_candidate(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^[^0-9A-Za-z가-힣]+", "", cleaned)
    cleaned = re.sub(r"[^0-9A-Za-z가-힣.]+$", "", cleaned)
    return re.sub(r"^[A-Za-z](?=[가-힣])", "", cleaned).strip()


def _consensus_name(candidates: list[str]) -> str:
    usable = [" ".join(candidate.split()) for candidate in candidates if _normalized(candidate)]
    if not usable:
        return ""
    counts = Counter(_normalized(candidate) for candidate in usable)
    best = max(counts, key=lambda key: (counts[key], len(key)))
    complete_suffixes = [
        candidate
        for candidate, count in counts.items()
        if len(candidate) == len(best) + 1
        and candidate.startswith(best)
        and count * 2 >= counts[best]
    ]
    if complete_suffixes:
        best = max(complete_suffixes, key=lambda key: (counts[key], len(key)))
    representations = Counter(
        candidate for candidate in usable if _normalized(candidate) == best
    )
    return max(
        representations,
        key=lambda candidate: (
            candidate.count(" "),
            representations[candidate],
            len(candidate),
        ),
    )


def _reconcile_latin_name(candidate: str, primary_candidate: str) -> str:
    candidate_compact = _normalized(candidate)
    primary_compact = _normalized(primary_candidate)
    if not primary_compact or candidate_compact == primary_compact:
        return candidate

    longer, shorter = (
        (candidate_compact, primary_compact)
        if len(candidate_compact) > len(primary_compact)
        else (primary_compact, candidate_compact)
    )
    if longer[1:] == shorter or longer[:-1] == shorter:
        return candidate
    differs_by_internal_character = len(longer) == len(shorter) + 1 and any(
        longer[:index] + longer[index + 1 :] == shorter
        for index in range(1, len(longer) - 1)
    )
    return primary_candidate if differs_by_internal_character else candidate


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

    def parse(
        self,
        known_names: list[str],
        recognize_names: bool = True,
    ) -> tuple[list[str], HudObservation]:
        with self._lock:
            if not self._recent_frames:
                raise RuntimeError("아직 읽을 수 있는 게임 화면이 없습니다.")
            source_frames = [frame.copy() for frame in self._recent_frames]

        recognizer = HudRecognizer()
        observations = [recognizer.analyze(frame) for frame in source_frames]
        frames = [normalize_frame(frame) for frame in source_frames]
        observation = observations[-1]

        if not recognize_names:
            return list(known_names), observation

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
            raise RuntimeError("팀명 인식 모듈이 없습니다. start.bat을 다시 실행하세요.") from error

        primary = None
        if KOREAN_MODEL_PATH.exists():
            reader = RapidOCR(
                use_text_det=False,
                use_angle_cls=False,
                text_score=0.25,
                rec_model_path=str(KOREAN_MODEL_PATH),
            )
        else:
            reader = RapidOCR(use_text_det=False, use_angle_cls=False, text_score=0.25)
        candidates: list[list[str]] = [[] for _ in TEAM_STARTS]
        wide_candidates: list[list[str]] = [[] for _ in TEAM_STARTS]
        for frame, seen in reversed(list(zip(frames, observations))):
            for index, team in enumerate(seen.teams):
                for left in NAME_LEFTS:
                    for name_y_offset in NAME_Y_OFFSETS:
                        crop = self._name_crop(
                            frame,
                            index,
                            team.selected,
                            left,
                            seen.hud_y_offset,
                            name_y_offset,
                        )
                        result, _ = reader(crop)
                        parsed = self._pick_name(result)
                        if parsed:
                            candidates[index].append(parsed)
                            if left == min(NAME_LEFTS):
                                wide_candidates[index].append(parsed)

        parsed_names = [_consensus_name(values) for values in candidates]

        missing = [index for index, name in enumerate(parsed_names) if not name]
        for frame, seen in reversed(list(zip(frames, observations))):
            for index in missing:
                team = seen.teams[index]
                for left in NAME_LEFTS:
                    for name_y_offset in NAME_Y_OFFSETS:
                        crop = self._name_crop(
                            frame,
                            index,
                            not team.selected,
                            left,
                            seen.hud_y_offset,
                            name_y_offset,
                        )
                        result, _ = reader(crop)
                        parsed = self._pick_name(result)
                        if parsed:
                            candidates[index].append(parsed)
                            if left == min(NAME_LEFTS):
                                wide_candidates[index].append(parsed)
        for index in missing:
            parsed_names[index] = _consensus_name(candidates[index])

        wide_names = [_consensus_name(values) for values in wide_candidates]
        for index, wide_name in enumerate(wide_names):
            if len(_normalized(wide_name)) >= len(_normalized(parsed_names[index])) + 2:
                parsed_names[index] = wide_name

        missing = [index for index, name in enumerate(parsed_names) if not name]
        latin_names = [
            index
            for index, name in enumerate(parsed_names)
            if re.search(r"[A-Za-z]", name)
        ]
        primary_indexes = sorted(set(missing + latin_names))
        if primary_indexes and KOREAN_MODEL_PATH.exists():
            primary = RapidOCR(use_text_det=False, use_angle_cls=False, text_score=0.25)
            primary_candidates: list[list[list[str]]] = [
                [[] for _ in NAME_LEFTS] for _ in TEAM_STARTS
            ]
            for frame, seen in reversed(list(zip(frames, observations))):
                for index in primary_indexes:
                    team = seen.teams[index]
                    for left_index, left in enumerate(NAME_LEFTS):
                        for name_y_offset in NAME_Y_OFFSETS:
                            crop = self._name_crop(
                                frame,
                                index,
                                team.selected,
                                left,
                                seen.hud_y_offset,
                                name_y_offset,
                            )
                            result, _ = primary(crop)
                            parsed = self._pick_name(result)
                            if parsed:
                                primary_candidates[index][left_index].append(parsed)
            for index in primary_indexes:
                if index in missing:
                    parsed_names[index] = _consensus_name(
                        [
                            name
                            for group in primary_candidates[index]
                            for name in group
                        ]
                    )
                else:
                    primary_names = [
                        _consensus_name(group) for group in primary_candidates[index]
                    ]
                    if all(primary_names) and len(
                        {_normalized(name) for name in primary_names}
                    ) == 1:
                        parsed_names[index] = _reconcile_latin_name(
                            parsed_names[index], primary_names[0]
                        )

        parsed_names = [
            _best_known_name(parsed, known_names) if parsed else known_names[index]
            for index, parsed in enumerate(parsed_names)
        ]

        del reader
        if primary is not None:
            del primary
        gc.collect()
        return parsed_names, observation

    @staticmethod
    def _name_crop(
        frame: np.ndarray,
        index: int,
        selected: bool,
        left: int,
        hud_y_offset: int = 0,
        name_y_offset: int = 0,
    ) -> np.ndarray:
        x0 = TEAM_STARTS[index]
        x1 = TEAM_STARTS[index + 1] if index + 1 < len(TEAM_STARTS) else x0 + TEAM_WIDTH
        y0 = (
            (SELECTED_NAME_Y if selected else NORMAL_NAME_Y)
            + hud_y_offset
            + name_y_offset
        )
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
            score_label = re.fullmatch(
                r"(?:TS|KS)(?:\d+(?:\.\d+)?)?",
                compact,
            )
            numeric_value = re.fullmatch(r"\d+(?:\.\d+)?", compact)
            if score_label or numeric_value:
                continue
            width = float(box[1][0]) - float(box[0][0])
            candidates.append((width * float(confidence), value))
        return max(candidates, default=(0.0, ""))[1]
