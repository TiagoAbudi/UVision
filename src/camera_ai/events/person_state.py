from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class PresenceState(StrEnum):
    ABSENT = "ABSENT"
    CONFIRMING = "CONFIRMING"
    PRESENT = "PRESENT"
    CLEARING = "CLEARING"


@dataclass(frozen=True, slots=True)
class DetectionEvent:
    event_type: str
    occurred_at: datetime
    frame_number: int
    person_count: int
    max_confidence: float

    def as_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "frame_number": self.frame_number,
            "person_count": self.person_count,
            "max_confidence": round(self.max_confidence, 4),
        }


class PersonPresenceTracker:
    def __init__(
        self,
        *,
        confirm_frames: int = 3,
        clear_frames: int = 10,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if confirm_frames < 1 or clear_frames < 1 or cooldown_seconds < 0:
            raise ValueError("Parâmetros inválidos para o rastreador de presença.")
        self.confirm_frames = confirm_frames
        self.clear_frames = clear_frames
        self.cooldown_seconds = cooldown_seconds
        self.state = PresenceState.ABSENT
        self.is_present = False
        self._positive_streak = 0
        self._negative_streak = 0
        self._last_detected_event_at = float("-inf")

    def update(
        self,
        person_count: int,
        max_confidence: float,
        frame_number: int,
        *,
        monotonic_time: float | None = None,
        occurred_at: datetime | None = None,
    ) -> list[DetectionEvent]:
        now = time.monotonic() if monotonic_time is None else monotonic_time
        timestamp = occurred_at or datetime.now(timezone.utc)
        events: list[DetectionEvent] = []

        if person_count > 0:
            self._negative_streak = 0
            if self.is_present:
                self.state = PresenceState.PRESENT
                return events

            self._positive_streak += 1
            self.state = PresenceState.CONFIRMING
            if self._positive_streak >= self.confirm_frames:
                self._positive_streak = 0
                self.is_present = True
                self.state = PresenceState.PRESENT
                if now - self._last_detected_event_at >= self.cooldown_seconds:
                    self._last_detected_event_at = now
                    events.append(
                        DetectionEvent(
                            event_type="PERSON_DETECTED",
                            occurred_at=timestamp,
                            frame_number=frame_number,
                            person_count=person_count,
                            max_confidence=max_confidence,
                        )
                    )
            return events

        self._positive_streak = 0
        if not self.is_present:
            self.state = PresenceState.ABSENT
            return events

        self._negative_streak += 1
        self.state = PresenceState.CLEARING
        if self._negative_streak >= self.clear_frames:
            self._negative_streak = 0
            self.is_present = False
            self.state = PresenceState.ABSENT
            events.append(
                DetectionEvent(
                    event_type="PERSON_CLEARED",
                    occurred_at=timestamp,
                    frame_number=frame_number,
                    person_count=0,
                    max_confidence=0.0,
                )
            )
        return events

