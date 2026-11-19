from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from camera_ai.detection.fall_monitor import FallMonitor
from camera_ai.detection.yolo_detector import PersonDetection


@dataclass(slots=True)
class PersonTrack:
    detection: PersonDetection
    seen_at: float
    monitor: FallMonitor


class PeopleFallMonitor:
    """Associate raw detections; optical-flow boxes never enter fall decisions."""

    def __init__(self, channel: int, logs_dir: Path, **options) -> None:
        self.channel, self.logs_dir, self.options = channel, logs_dir, options
        self.base = FallMonitor(channel, logs_dir, **options)
        self._tracks: dict[int, PersonTrack] = {}
        self._next_id = 1
        self._detections: list[PersonDetection] = []
        self._lock = threading.RLock()

    @property
    def detections(self) -> list[PersonDetection]:
        with self._lock:
            return list(self._detections)

    @staticmethod
    def _cost(first: PersonDetection, second: PersonDetection, width: int, height: int) -> float | None:
        a1 = max(1, (first.x2 - first.x1) * (first.y2 - first.y1))
        a2 = max(1, (second.x2 - second.x1) * (second.y2 - second.y1))
        if not 0.12 <= a1 / a2 <= 8:
            return None
        intersection = max(0, min(first.x2, second.x2) - max(first.x1, second.x1)) * max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
        iou = intersection / max(1, a1 + a2 - intersection)
        distance = math.hypot((first.x1 + first.x2 - second.x1 - second.x2) / (2 * width),
                              (first.y1 + first.y2 - second.y1 - second.y2) / (2 * height))
        if distance > 0.22 or (iou < 0.05 and distance > 0.10):
            return None
        return distance + (1 - iou) * 0.08

    def update(self, detections: list[PersonDetection], width: int, height: int, *, now: float | None = None) -> dict:
        current = time.monotonic() if now is None else now
        with self._lock:
            if width <= 0 or height <= 0:
                detections = []
            candidates = []
            for index, detection in enumerate(detections):
                costs = []
                for track_id, track in self._tracks.items():
                    if current - track.seen_at > 1.0:
                        continue
                    cost = self._cost(track.detection, detection, width, height)
                    if cost is not None:
                        costs.append((cost, index, track_id))
                costs.sort()
                # A crossing/occlusion can make identity ambiguous. Starting a
                # new history is preferable to carrying another person's fall.
                if len(costs) < 2 or costs[1][0] - costs[0][0] >= 0.025:
                    candidates.extend(costs)
            assigned: dict[int, int] = {}
            used = set()
            for _, index, track_id in sorted(candidates):
                if index not in assigned and track_id not in used:
                    assigned[index] = track_id
                    used.add(track_id)
            output = []
            for index, detection in enumerate(detections):
                track_id = assigned.get(index)
                if track_id is None:
                    track_id = self._next_id
                    self._next_id += 1
                    monitor = FallMonitor(
                        self.channel, self.logs_dir, **self.options, track_id=track_id,
                        shared_loggers=(self.base._sample_logger, self.base._event_logger),
                    )
                    self._tracks[track_id] = PersonTrack(detection, current, monitor)
                track = self._tracks[track_id]
                identified = replace(detection, track_id=track_id)
                track.monitor.update([identified], width, height, now=current)
                track.detection, track.seen_at = identified, current
                output.append(identified)
                used.add(track_id)
            for track_id, track in list(self._tracks.items()):
                if track_id not in used:
                    track.monitor.update([], width, height, now=current)
                if current - track.seen_at > 3:
                    track.monitor.close()
                    del self._tracks[track_id]
            self._detections = output
            return self.status()

    def status(self) -> dict:
        with self._lock:
            people = [track.monitor.status() for track in self._tracks.values()]
            order = {"normal": 0, "possible_fall": 1, "down": 2, "alert_pending": 3}
            status = dict(max(people, key=lambda item: order[item["state"]], default=self.base.status()))
            status["people"] = people
            status["trackedPeople"] = len(self._detections)
            return status

    def close(self) -> None:
        with self._lock:
            for track in self._tracks.values():
                track.monitor.close()
            self.base.close()

    def reset(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._detections.clear()
