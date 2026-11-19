from __future__ import annotations

import os

import pytest

from camera_ai.event_store import EventStore
from camera_ai.recording import FallClipRecorder


def test_review_survives_restart_and_unsure_is_distinct_from_unreviewed(tmp_path) -> None:
    store = EventStore(tmp_path / "events.json")
    store.review("queda-ch01_test.mp4", "unsure")
    reloaded = EventStore(store.path)
    assert reloaded.reviews()["queda-ch01_test.mp4"]["label"] == "unsure"
    assert reloaded.is_protected("queda-ch01_test.mp4")
    with pytest.raises(ValueError):
        store.review("../outside.mp4", "fall")


def test_retention_keeps_reviewed_examples_and_limits_unprotected_clips(tmp_path) -> None:
    store = EventStore(tmp_path / "events.json")
    paths = [tmp_path / f"queda-ch01_{index}.mp4" for index in range(3)]
    for index, path in enumerate(paths):
        path.write_bytes(b"test")
        os.utime(path, (index+1, index+1))
    store.review(paths[0], "normal")
    recorder = FallClipRecorder(channel=1, frame_provider=lambda: (b"", 0), status_provider=lambda: {},
                                output_dir=tmp_path, max_files=1, is_protected=store.is_protected)
    recorder._prune_old_files()
    assert paths[0].exists() and paths[2].exists()
    assert not paths[1].exists()


def test_acknowledgement_is_durable_and_releases_alert_protection(tmp_path) -> None:
    store = EventStore(tmp_path / "events.json")
    event = store.create_alert(4, 2, "Cozinha", shadow=False)
    store.update_alert(event["id"], clip="queda-ch04_example.mp4")
    assert store.is_protected("queda-ch04_example.mp4")
    store.acknowledge(event["id"], "Familiar")
    reloaded = EventStore(store.path)
    assert reloaded.alerts()[0]["status"] == "acknowledged"
    assert not reloaded.is_protected("queda-ch04_example.mp4")


def test_delivery_updates_merge_receipts_and_sender_messages_without_losing_ids(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    store = EventStore(tmp_path / "events.json")
    event = store.create_alert(4, 2, "Cozinha", shadow=False)
    def update(index):
        store.update_delivery(event["id"], "123", messages={str(index): "photo"}, initialSent=1)
        store.acknowledge(event["id"])
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(update, range(1, 9)))
    store.update_delivery(event["id"], "123", clipSent=2)
    reloaded = EventStore(store.path)
    saved = reloaded.alert(event["id"])
    assert len(saved["deliveries"]["123"]["messages"]) == 8
    assert saved["deliveries"]["123"]["clipSent"] == 2
    assert saved["status"] == "acknowledged"
    acknowledged_at = saved["acknowledgedAt"]
    reloaded.acknowledge(event["id"], "Segundo clique")
    assert reloaded.alert(event["id"])["acknowledgedAt"] == acknowledged_at
