from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
TEAM_STARTS = (0, 188, 366, 544, 722, 900, 1078, 1255)
TEAM_WIDTH = 178
SCORE_LINES = (765, 795)  # selected team, normal teams
SCORE_LINE_LAYOUTS = (SCORE_LINES, (820, 850))  # spectator controls visible, hidden
MIN_ESCAPE_ICONS = 2


def normalize_frame(frame: np.ndarray) -> np.ndarray:
    if frame.shape[1] == REFERENCE_WIDTH and frame.shape[0] == REFERENCE_HEIGHT:
        return frame
    return cv2.resize(frame, (REFERENCE_WIDTH, REFERENCE_HEIGHT), interpolation=cv2.INTER_AREA)


def _bitmap(rows: str) -> np.ndarray:
    return np.array([[char == "#" for char in row] for row in rows.split("/")], dtype=np.uint8)


# Templates are extracted from the fixed spectator HUD in the supplied 1920x1080 video.
# Keeping them as 7x11 bitmaps makes recognition deterministic and very cheap.
SCORE_DIGITS = {
    "0": _bitmap(".#####./#######/##....#/##....#/##....#/##....#/##....#/##....#/##....#/##...##/.######"),
    "1": _bitmap(".####../.####../...##../...##../...##../...##../...##../...##../...##../...###./#######"),
    "2": _bitmap(".#####./.######/......#/.....##/.....##/.#####./###..../##...../#....../##....#/#######"),
    "3": _bitmap("######./#######/......#/......#/.....##/.######/......#/......#/......#/.....##/#######"),
    "4": _bitmap("...##../...##../..##.../..##.../.##...#/.##...#/.#....#/##...##/#######/.....##/......#"),
    "5": _bitmap("#######/#######/##...../##...../#####../#######/.....##/......#/......#/.....##/#######"),
    "6": _bitmap(".#####./######./##...../##...../#####../#######/##...##/##....#/##....#/##...##/.######"),
    "7": _bitmap("#######/#######/.....##/.....##/.....##/....##./....##./...##../...##../..##.../..#...."),
    "8": _bitmap(".#####./#######/##....#/##....#/##...##/.######/##...##/##....#/##....#/##...##/.######"),
    "9": _bitmap(".#####./#######/##....#/##....#/##....#/#######/.######/......#/.....##/.....##/.#####."),
}

DAY_DIGITS = {
    1: _bitmap("...##../...##../..###../....#../....#../...##../...##../....#../....#../..####./#######"),
    2: _bitmap("..####./..####./..#####/.....##/.....##/..#####/..#####/####.../###..../####.../#######"),
    3: _bitmap("..####./..####./#######/.....##/.....##/..#####/..#####/.....##/.....##/..#####/######."),
    4: _bitmap("....#../....#../...##../...##../..##..#/..##.##/..##.##/###..##/#######/#######/.....##"),
    5: _bitmap("#######/#######/#######/###..../###..../#######/#######/.....##/.....##/#######/######."),
    6: _bitmap("..####./..####./#######/###..../###..../#######/#######/###..##/###..##/###..##/..####."),
    7: _bitmap("#######/#######/#######/.....##/.....##/....##./....##./....##./...##../...##../..##..."),
    8: _bitmap("..####./..####./#######/###..##/###..##/..####./..####./###..##/###..##/#######/..####."),
}


@dataclass(frozen=True)
class TeamObservation:
    team: int
    ts: Optional[float]
    ks: Optional[float]
    alive: Optional[int]
    knocked: Optional[int]
    respawning: Optional[int]
    selected: bool = False
    escaped: Optional[bool] = None


@dataclass(frozen=True)
class HudObservation:
    day: Optional[int]
    teams: tuple[TeamObservation, ...]
    wipe_marker: bool
    resolution_ok: bool
    hud_y_offset: int = 0


class HudRecognizer:
    """Recognize only the fixed HUD regions needed by the overlay.

    No neural OCR is used. A frame costs a few small HSV conversions and binary
    component scans, which is suitable for low-spec machines at 1-2 samples/sec.
    """

    def analyze(self, frame: np.ndarray) -> HudObservation:
        if frame is None or frame.ndim != 3:
            return self._empty(False)

        resolution_ok = frame.shape[1] == REFERENCE_WIDTH and frame.shape[0] == REFERENCE_HEIGHT
        if not resolution_ok:
            frame = normalize_frame(frame)

        day = self._read_day(frame)
        teams: tuple[TeamObservation, ...] = ()
        hud_y_offset = 0
        valid_team_count = -1
        for score_lines in SCORE_LINE_LAYOUTS:
            candidate = tuple(
                self._read_team(frame, index, start, score_lines)
                for index, start in enumerate(TEAM_STARTS, 1)
            )
            candidate_count = sum(team.ts is not None and team.ks is not None for team in candidate)
            if candidate_count > valid_team_count:
                teams = candidate
                hud_y_offset = score_lines[0] - SCORE_LINES[0]
                valid_team_count = candidate_count
        wipe_marker = self._read_wipe_marker(frame)
        return HudObservation(
            day=day,
            teams=teams,
            wipe_marker=wipe_marker,
            resolution_ok=resolution_ok,
            hud_y_offset=hud_y_offset,
        )

    @staticmethod
    def _empty(resolution_ok: bool) -> HudObservation:
        teams = tuple(TeamObservation(i, None, None, None, None, None) for i in range(1, 9))
        return HudObservation(None, teams, False, resolution_ok)

    def _read_team(
        self,
        frame: np.ndarray,
        team: int,
        x0: int,
        score_lines: tuple[int, int] = SCORE_LINES,
    ) -> TeamObservation:
        best: Optional[tuple[float, float, int, int]] = None
        x_candidates = (x0, x0 + 1) if x0 == 0 else (x0,)
        for line in score_lines:
            for score_x0 in x_candidates:
                parsed = self._read_score_line(
                    frame[line : line + 20, score_x0 : score_x0 + TEAM_WIDTH],
                    split_x=135,
                )
                if parsed is not None:
                    best = (parsed[0], parsed[1], line, score_x0)
                    break
            if best is not None:
                break

        if best is None:
            return TeamObservation(team, None, None, None, None, None)

        ts, ks, line, x0 = best
        glyph_y = line + 7
        skulls = 0
        knocked = 0
        respawning = 0
        escape_icons = 0
        for center_x in (31, 88, 145):
            timer_visible = self._has_respawn_timer(frame, line, x0, center_x)
            patch = frame[glyph_y + 37 : glyph_y + 76, x0 + center_x - 14 : x0 + center_x + 15]
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            escape_green = (
                (hsv[:, :, 0] >= 48)
                & (hsv[:, :, 0] <= 65)
                & (hsv[:, :, 1] > 120)
                & (hsv[:, :, 2] > 140)
            )
            magenta = (
                (hsv[:, :, 0] >= 155)
                & (hsv[:, :, 0] <= 179)
                & (hsv[:, :, 1] > 110)
                & (hsv[:, :, 2] > 140)
            )
            orange = (
                (hsv[:, :, 0] >= 3)
                & (hsv[:, :, 0] <= 18)
                & (hsv[:, :, 1] > 135)
                & (hsv[:, :, 2] > 135)
            )
            if int(escape_green.sum()) >= 120:
                escape_icons += 1
            if timer_visible:
                skulls += 1
                respawning += 1
            elif int(magenta.sum()) >= 95:
                skulls += 1
            elif int(orange.sum()) >= 95:
                knocked += 1

        return TeamObservation(
            team,
            ts,
            ks,
            3 - skulls,
            knocked,
            respawning,
            selected=line == score_lines[0],
            escaped=escape_icons >= MIN_ESCAPE_ICONS,
        )

    @staticmethod
    def _has_respawn_timer(frame: np.ndarray, line: int, x0: int, center_x: int) -> bool:
        roi = frame[line + 90 : line + 145, x0 + center_x - 20 : x0 + center_x + 20]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 2] > 210) & (hsv[:, :, 1] < 80)).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for component in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[component])
            if x <= 1 or x + width >= roi.shape[1] - 1:
                continue
            if 18 <= y <= 24 and 17 <= height <= 23 and 3 <= width <= 16 and area >= 55:
                return True
        return False

    def _read_score_line(self, roi: np.ndarray, split_x: int = 135) -> Optional[tuple[float, float]]:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 2] > 180) & (hsv[:, :, 1] < 85)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        components: list[tuple[int, str, float]] = []
        for component in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[component])
            if area < 20 or not (9 <= height <= 13 and 5 <= width <= 8 and x >= 80):
                continue
            glyph = (labels[y : y + height, x : x + width] == component).astype(np.uint8)
            digit, error = self._classify(glyph, SCORE_DIGITS)
            if error <= 0.30:
                components.append((x, str(digit), error))

        ts = self._components_to_score([item for item in components if item[0] < split_x])
        ks = self._components_to_score([item for item in components if item[0] >= split_x])
        if ts is None or ks is None or ts < ks or ts > 99.5:
            return None
        return ts, ks

    @staticmethod
    def _components_to_score(components: list[tuple[int, str, float]]) -> Optional[float]:
        components.sort(key=lambda item: item[0])
        if len(components) == 1:
            return 0.0 if components[0][1] == "0" else None
        if not 2 <= len(components) <= 3:
            return None
        digits = "".join(item[1] for item in components)
        if digits[-1] not in "05":
            return None
        value = float(f"{digits[:-1]}.{digits[-1]}")
        return value if value * 2 == round(value * 2) else None

    def _read_day(self, frame: np.ndarray) -> Optional[int]:
        roi = frame[0:35, 840:860]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 2] > 175) & (hsv[:, :, 1] < 100)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for component in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[component])
            if area < 10 or not (8 <= height <= 12 and x < 15):
                continue
            glyph = (labels[y : y + height, x : x + width] == component).astype(np.uint8)
            day, error = self._classify(glyph, DAY_DIGITS)
            if error <= 0.20:
                return int(day)
        return None

    @staticmethod
    def _read_wipe_marker(frame: np.ndarray) -> bool:
        # "전멸" appears in a dedicated red label immediately left of the skull.
        roi = frame[35:420, 1760:1810]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        red = (
            ((hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 168))
            & (hsv[:, :, 1] > 140)
            & (hsv[:, :, 2] > 110)
        )
        row_counts = red.sum(axis=1).astype(np.int32)
        if len(row_counts) < 20:
            return False
        rolling = np.convolve(row_counts, np.ones(20, dtype=np.int32), mode="valid")
        return bool(rolling.max(initial=0) >= 100)

    @staticmethod
    def _classify(glyph: np.ndarray, templates: dict) -> tuple[object, float]:
        normalized = cv2.resize(glyph, (7, 11), interpolation=cv2.INTER_NEAREST)
        errors = ((float(np.mean(normalized != template)), label) for label, template in templates.items())
        error, label = min(errors, key=lambda item: item[0])
        return label, error
