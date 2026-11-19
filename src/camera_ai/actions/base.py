from __future__ import annotations

import logging
from typing import Protocol

from camera_ai.events import DetectionEvent


class EventAction(Protocol):
    def handle(self, event: DetectionEvent, frame) -> None: ...


class ActionDispatcher:
    def __init__(self, actions: list[EventAction] | None = None) -> None:
        self.actions = list(actions or [])
        self.logger = logging.getLogger(__name__)

    def register(self, action: EventAction) -> None:
        self.actions.append(action)

    def dispatch(self, event: DetectionEvent, frame) -> None:
        for action in self.actions:
            try:
                action.handle(event, frame)
            except Exception:
                self.logger.exception("A ação %s falhou para %s", type(action).__name__, event.event_type)

