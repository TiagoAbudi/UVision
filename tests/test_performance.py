from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from camera_ai.performance import PerformanceProfile, analysis_frame, restore_coordinates
from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint


def test_analysis_resize_keeps_display_untouched_and_restores_pose() -> None:
    full = np.zeros((1080, 960, 3), np.uint8)
    small = analysis_frame(full, 540)
    assert full.shape == (1080, 960, 3)
    assert small.shape == (540, 480, 3)
    person = PersonDetection(10, 20, 100, 200, .9, (PoseKeypoint(40, 60, .8),), track_id=3)
    result = restore_coordinates([person], small, full)[0]
    assert (result.x1, result.y1, result.x2, result.y2) == (20, 40, 200, 400)
    assert result.keypoints[0] == PoseKeypoint(80, 120, .8)
    assert result.track_id == 3


def test_default_profile_preserves_existing_quality_and_persists_opt_in(tmp_path) -> None:
    path = tmp_path / "performance.json"
    settings = SimpleNamespace(yolo_image_size=640, detection_interval=.16)
    assert PerformanceProfile.load(path, settings).analysis_max_side == 0
    balanced = PerformanceProfile(640, .3, 1280)
    balanced.save(path)
    assert asdict(PerformanceProfile.load(path, settings)) == asdict(balanced)
    with pytest.raises(ValueError):
        PerformanceProfile(500, .3, 1280).save(path)


@pytest.mark.parametrize("stale", [False, True])
def test_worker_never_reanalyzes_repeated_or_stale_frame(tmp_path, monkeypatch, stale) -> None:
    import time
    from unittest.mock import Mock
    from camera_ai import web_dashboard
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    feed = web_dashboard.CameraFeed(1, "Entrada", "")
    feed._frame = np.zeros((64,64,3), np.uint8)
    feed._frame_number = 1
    feed._last_frame_monotonic = time.monotonic()-(10 if stale else 0)
    settings = web_dashboard.DVRSettings(host="localhost", user="test", password="test")
    worker = web_dashboard.DetectionWorker([feed], settings)
    detector = SimpleNamespace(device_label="CPU", detect_batch=Mock(return_value=[[]]), warmup=Mock())
    monkeypatch.setattr(web_dashboard, "YoloPersonDetector", lambda *args, **kwargs: detector)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if len(waits) >= 2:
            worker._stop.set()
    monkeypatch.setattr(worker._stop, "wait", wait)
    worker._run()
    assert detector.detect_batch.call_count == (0 if stale else 1)
    assert worker.status()["error"] is None


def test_smaller_worker_batches_serve_every_camera_without_starvation(tmp_path, monkeypatch) -> None:
    import time
    from unittest.mock import Mock
    from camera_ai import web_dashboard
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    feeds = [web_dashboard.CameraFeed(channel, str(channel), "") for channel in range(1, 10)]
    for feed in feeds:
        feed._frame = np.full((64, 64, 3), feed.channel, dtype=np.uint8)
        feed._frame_number = 1
        feed._last_frame_monotonic = time.monotonic()
    settings = web_dashboard.DVRSettings(host="localhost", user="test", password="test")
    worker = web_dashboard.DetectionWorker(feeds, settings)
    batches = []
    def detect(frames):
        batches.append([int(frame[0, 0, 0]) for frame in frames])
        return [[] for _ in frames]
    detector = SimpleNamespace(device_label="CPU", detect_batch=detect, warmup=Mock())
    monkeypatch.setattr(web_dashboard, "YoloPersonDetector", lambda *args, **kwargs: detector)
    def wait(_seconds):
        for feed in feeds:
            feed._frame_number += 1
            feed._last_frame_monotonic = time.monotonic()
        if len(batches) >= 9:
            worker._stop.set()
    monkeypatch.setattr(worker._stop, "wait", wait)
    worker._run()
    assert all(len(batch) == 4 for batch in batches)
    assert batches[0][0] == 4
    assert {channel: sum(channel in batch for batch in batches) for channel in range(1, 10)} == dict.fromkeys(range(1, 10), 4)
    assert worker.status()["error"] is None
