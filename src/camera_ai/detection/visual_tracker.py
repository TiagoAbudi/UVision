from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from camera_ai.detection.yolo_detector import PersonDetection


@dataclass(slots=True)
class _VisualTrack:
    box: np.ndarray
    confidence: float
    seen_at: float
    points: np.ndarray | None
    misses: int = 0
    track_id: int | None = None


class VisualPersonTracker:
    """Frame-rate display tracking. Never used as evidence for fall decisions.

    The caller serializes access. Motion comes from optical flow, not unlimited
    velocity extrapolation, so a stopped person does not keep drifting.
    """

    MAX_AGE = 0.55

    def __init__(self) -> None:
        self._tracks: list[_VisualTrack] = []
        self._gray: np.ndarray | None = None
        self._frame_number: int | None = None
        self._source_size: tuple[int, int] | None = None
        self._scale = 1.0

    @staticmethod
    def _gray_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
        scale = min(1.0, 480 / max(frame.shape[:2]))
        resized = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY), scale

    @staticmethod
    def _seeds(gray: np.ndarray, box: np.ndarray, scale: float, detection: PersonDetection | None = None) -> np.ndarray | None:
        if detection is not None:
            points = [
                (point.x * scale, point.y * scale)
                for point in detection.keypoints
                if point.confidence >= 0.4
                and 0 <= point.x * scale < gray.shape[1]
                and 0 <= point.y * scale < gray.shape[0]
            ]
            if len(points) >= 5:
                return np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        x1, y1, x2, y2 = box * scale
        # Keep old box strokes, labels and background edges outside the ROI.
        dx, dy = (x2 - x1) * 0.15, (y2 - y1) * 0.15
        left, right = max(0, int(x1 + dx)), min(gray.shape[1], int(x2 - dx))
        top, bottom = max(0, int(y1 + dy)), min(gray.shape[0], int(y2 - dy))
        if right <= left or bottom <= top:
            return None
        mask = np.zeros_like(gray)
        mask[top:bottom, left:right] = 255
        return cv2.goodFeaturesToTrack(gray, maxCorners=24, qualityLevel=0.02, minDistance=4, mask=mask)

    @staticmethod
    def _flow(previous: np.ndarray, current: np.ndarray, points: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None]:
        zero = np.zeros(2, dtype=np.float32)
        if points is None or len(points) < 3 or previous.shape != current.shape:
            return zero, None
        moved, valid, errors = cv2.calcOpticalFlowPyrLK(
            previous, current, points, None, winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.02),
        )
        if moved is None or valid is None or errors is None:
            return zero, None
        backwards, back_valid, _ = cv2.calcOpticalFlowPyrLK(
            current, previous, moved, None, winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, 0.02),
        )
        if backwards is None or back_valid is None:
            return zero, None
        keep = (
            (valid.ravel() == 1) & (back_valid.ravel() == 1)
            & (errors.ravel() < 35)
            & (np.linalg.norm(backwards - points, axis=2).ravel() < 1.5)
        )
        if np.count_nonzero(keep) < 3:
            return zero, None
        good = moved[keep]
        displacement = np.median((moved - points)[keep].reshape(-1, 2), axis=0)
        if not np.all(np.isfinite(displacement)) or np.linalg.norm(displacement) > max(current.shape) * 0.25:
            return zero, None
        return displacement, good

    def advance(self, frame: np.ndarray, frame_number: int, now: float) -> list[PersonDetection]:
        size = (frame.shape[1], frame.shape[0])
        self._tracks = [track for track in self._tracks if now - track.seen_at <= self.MAX_AGE]
        if self._source_size != size:
            self._tracks.clear()
            self._gray = None
            self._frame_number = None
            self._source_size = size
        if frame_number != self._frame_number:
            gray, scale = self._gray_frame(frame)
            if self._gray is not None:
                for track in self._tracks:
                    movement, points = self._flow(self._gray, gray, track.points)
                    movement /= scale
                    track.box += np.array([*movement, *movement], dtype=np.float32)
                    track.points = points if points is not None else self._seeds(gray, track.box, scale)
            self._gray, self._scale, self._frame_number = gray, scale, frame_number
        output = []
        for track in self._tracks:
            x1, y1, x2, y2 = track.box
            x1, x2 = np.clip([x1, x2], 0, size[0] - 1)
            y1, y2 = np.clip([y1, y2], 0, size[1] - 1)
            if x2 > x1 and y2 > y1:
                output.append(PersonDetection(int(x1), int(y1), int(x2), int(y2), track.confidence, track_id=track.track_id))
        return output

    @staticmethod
    def _match_score(first: np.ndarray, second: np.ndarray) -> float:
        overlap = np.maximum(0, np.minimum(first[2:], second[2:]) - np.maximum(first[:2], second[:2]))
        intersection = float(np.prod(overlap))
        area1, area2 = float(np.prod(first[2:] - first[:2])), float(np.prod(second[2:] - second[:2]))
        iou = intersection / max(1.0, area1 + area2 - intersection)
        centers_distance = float(np.linalg.norm((first[:2] + first[2:] - second[:2] - second[2:]) / 2))
        diagonal = max(1.0, float(np.linalg.norm(first[2:] - first[:2])))
        if iou < 0.1 and centers_distance / diagonal > 0.5:
            return -1.0
        return iou - centers_distance / diagonal * 0.15

    def observe(
        self,
        detections: list[PersonDetection],
        source_frame: np.ndarray,
        current_frame: np.ndarray,
        current_number: int,
        captured_at: float,
        now: float,
    ) -> None:
        self.advance(current_frame, current_number, now)
        if now - captured_at > self.MAX_AGE or source_frame.shape[:2] != current_frame.shape[:2]:
            return
        source_gray, scale = self._gray_frame(source_frame)
        assert self._gray is not None
        unmatched = set(range(len(self._tracks)))
        for detection in detections:
            box = np.array([detection.x1, detection.y1, detection.x2, detection.y2], dtype=np.float32)
            points = self._seeds(source_gray, box, scale, detection)
            # Align the result to the currently displayed image, compensating
            # for frames that arrived during GPU/animal verification work.
            movement, moved_points = self._flow(source_gray, self._gray, points)
            movement /= scale
            box += np.array([*movement, *movement], dtype=np.float32)
            candidates = [(self._match_score(self._tracks[index].box, box), index) for index in unmatched
                          if detection.track_id is None or self._tracks[index].track_id == detection.track_id]
            score, index = max(candidates, default=(-1, -1))
            if score >= 0:
                unmatched.remove(index)
                track = self._tracks[index]
                previous = track.box
                old_center, center = (previous[:2] + previous[2:]) / 2, (box[:2] + box[2:]) / 2
                old_size, size = previous[2:] - previous[:2], box[2:] - box[:2]
                # Strong position correction avoids the delay caused by a
                # slow EMA. Size is quieter, but adapts quickly during a fall.
                center = old_center + 0.85 * (center - old_center)
                size_alpha = 0.8 if np.max(np.abs(size - old_size) / np.maximum(old_size, 1)) > 0.3 else 0.45
                size = old_size + size_alpha * (size - old_size)
                track.box = np.concatenate((center - size / 2, center + size / 2)).astype(np.float32)
                track.confidence = detection.confidence
                track.track_id = detection.track_id
                track.seen_at = captured_at
                track.misses = 0
                track.points = moved_points if moved_points is not None else self._seeds(self._gray, track.box, scale)
            else:
                self._tracks.append(_VisualTrack(box, detection.confidence, captured_at, moved_points, track_id=detection.track_id))
        for index in unmatched:
            self._tracks[index].misses += 1
        self._tracks = [
            track for track in self._tracks
            if track.misses < 2 and now - track.seen_at <= self.MAX_AGE
        ]
