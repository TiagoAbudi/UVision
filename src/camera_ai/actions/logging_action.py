from __future__ import annotations

import json
import logging
from pathlib import Path

from camera_ai.events import DetectionEvent


class JsonLogAction:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def handle(self, event: DetectionEvent, _frame) -> None:
        record = json.dumps(event.as_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(record + "\n")
        self.logger.info(
            "%s | pessoas=%d | confiança=%.2f",
            event.event_type,
            event.person_count,
            event.max_confidence,
        )

