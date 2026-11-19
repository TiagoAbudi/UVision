from datetime import datetime, timezone

from camera_ai.events import PersonPresenceTracker, PresenceState


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def test_requires_consecutive_positive_frames() -> None:
    tracker = PersonPresenceTracker(confirm_frames=3, clear_frames=2, cooldown_seconds=30)

    assert tracker.update(1, 0.8, 1, monotonic_time=0, occurred_at=NOW) == []
    assert tracker.state == PresenceState.CONFIRMING
    assert tracker.update(1, 0.8, 2, monotonic_time=1, occurred_at=NOW) == []
    events = tracker.update(1, 0.8, 3, monotonic_time=2, occurred_at=NOW)

    assert [event.event_type for event in events] == ["PERSON_DETECTED"]
    assert tracker.state == PresenceState.PRESENT


def test_continuous_presence_does_not_repeat_event() -> None:
    tracker = PersonPresenceTracker(confirm_frames=1, clear_frames=2, cooldown_seconds=5)

    first = tracker.update(1, 0.9, 1, monotonic_time=0, occurred_at=NOW)
    repeated = tracker.update(1, 0.9, 2, monotonic_time=100, occurred_at=NOW)

    assert len(first) == 1
    assert repeated == []


def test_clear_requires_consecutive_negative_frames() -> None:
    tracker = PersonPresenceTracker(confirm_frames=1, clear_frames=2, cooldown_seconds=0)
    tracker.update(1, 0.9, 1, monotonic_time=0, occurred_at=NOW)

    assert tracker.update(0, 0.0, 2, monotonic_time=1, occurred_at=NOW) == []
    assert tracker.state == PresenceState.CLEARING
    events = tracker.update(0, 0.0, 3, monotonic_time=2, occurred_at=NOW)

    assert [event.event_type for event in events] == ["PERSON_CLEARED"]
    assert tracker.state == PresenceState.ABSENT


def test_cooldown_blocks_a_quick_reentry() -> None:
    tracker = PersonPresenceTracker(confirm_frames=1, clear_frames=1, cooldown_seconds=30)
    assert len(tracker.update(1, 0.9, 1, monotonic_time=0, occurred_at=NOW)) == 1
    tracker.update(0, 0.0, 2, monotonic_time=1, occurred_at=NOW)

    assert tracker.update(1, 0.9, 3, monotonic_time=10, occurred_at=NOW) == []
    tracker.update(0, 0.0, 4, monotonic_time=11, occurred_at=NOW)
    assert len(tracker.update(1, 0.9, 5, monotonic_time=31, occurred_at=NOW)) == 1

