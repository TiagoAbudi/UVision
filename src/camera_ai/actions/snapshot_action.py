from __future__ import annotations

from pathlib import Path

import cv2

from camera_ai.events import DetectionEvent


class SnapshotAction:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def handle(self, event: DetectionEvent, frame) -> None:
        if event.event_type != "PERSON_DETECTED" or frame is None:
            return
        timestamp = event.occurred_at.strftime("%Y%m%dT%H%M%S_%fZ")
        target = self.directory / f"person_{timestamp}.jpg"
        if not cv2.imwrite(str(target), frame):
            raise RuntimeError(f"Não foi possível salvar o snapshot {target.name}")

