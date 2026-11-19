from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from camera_ai import web_dashboard as dashboard
from camera_ai.detection import FallMonitor, PersonDetection, PoseKeypoint
from camera_ai.recording.fall_clip_recorder import BufferedFrame, FallClipRecorder
from camera_ai.web_dashboard import CameraFeed, DashboardState, DetectionWorker, DVRSettings, _fall_channels


def _settings(**overrides) -> DVRSettings:
    return DVRSettings(host="localhost", user="test", password="test", **overrides)


@pytest.mark.parametrize(("value", "expected"), [("all", None), (" ALL ", None), ("1,4,9", (1, 4, 9)), ("4", (4,))])
def test_parse_selected_channels(value, expected) -> None:
    assert _fall_channels(value) == expected


@pytest.mark.parametrize("value", ["", "1,", "0,4", "1,1", "none"])
def test_reject_invalid_channel_selection(value) -> None:
    with pytest.raises(ValueError, match="FALL_MONITOR_CHANNELS"):
        _fall_channels(value)


def test_all_channels_includes_extra_camera_and_legacy_rotation_stays_on_pilot() -> None:
    settings = _settings(extra_camera_host="localhost", fall_camera_rotation=90)
    assert settings.fall_channels() == tuple(range(1, 10))
    assert [settings.fall_rotation(channel) for channel in settings.fall_channels()] == [0, 0, 0, 90, 0, 0, 0, 0, 0]
    with pytest.raises(ValueError, match="disponíveis"):
        _settings(fall_monitor_channels=(1, 9)).fall_channels()


def test_load_per_camera_overrides(monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "load_dotenv", lambda *_args, **_kwargs: None)
    for key, value in {
        "DVR_HOST": "localhost", "DVR_USER": "test", "DVR_PASSWORD": "test",
        "DVR_CHANNEL_COUNT": "8", "EXTRA_CAMERA_HOST": "localhost",
        "FALL_MONITOR_CHANNELS": "1,4,9", "FALL_CAMERA_ROTATION_CH01": "270",
        "FALL_GEOMETRY_PROFILE_CH01": "calibration/ch01-geometry.json",
    }.items():
        monkeypatch.setenv(key, value)
    settings = DVRSettings.load()
    assert settings.fall_channels() == (1, 4, 9)
    assert settings.fall_rotation(1) == 270
    assert settings.fall_geometry_profiles[1] == "calibration/ch01-geometry.json"


def test_geometry_is_loaded_only_for_matching_camera(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "PROJECT_DIR", tmp_path)
    folder = tmp_path / "calibration"
    folder.mkdir()
    profile = {
        "channel": 4, "coordinateSystem": "raw_image_normalized_xy",
        "verticalVanishingPoint": [0.5, -2.0],
        "minLegDeviationDegrees": 8, "minTorsoDeviationDegrees": 25,
    }
    (folder / "ch04-geometry.json").write_text(json.dumps(profile), encoding="utf-8")
    settings = _settings()
    assert settings.fall_geometry(4).channel == 4
    assert settings.fall_geometry(1) is None
    profile["channel"] = 1
    (folder / "ch01-geometry.json").write_text(json.dumps(profile), encoding="utf-8")
    assert settings.fall_geometry(1).channel == 1
    with pytest.raises(ValueError, match="CH 01"):
        _settings(fall_geometry_profiles={1: "calibration/ch04-geometry.json"}).fall_geometry(1)
    assert _settings(fall_geometry_profiles={1: ""}).fall_geometry(1) is None
    with pytest.raises(ValueError, match="Rotação"):
        _settings(fall_camera_rotations={1: 45}).fall_rotation(1)


def test_nine_monitors_and_recorders_use_their_own_feed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "discover_channel_titles", lambda settings: [f"Câmera {i}" for i in range(1, 9)])
    monkeypatch.setattr(CameraFeed, "recording_snapshot", lambda feed: (bytes([feed.channel]), feed.channel))
    state = DashboardState(_settings(extra_camera_host="localhost", fall_geometry_profile="", fall_camera_rotation=90), native=True, compose_mosaic=False)
    try:
        assert list(state.fall_monitors) == list(range(1, 10))
        assert list(state.fall_recorders) == list(range(1, 10))
        assert state.detection_worker.fall_monitors is state.fall_monitors
        for channel, recorder in state.fall_recorders.items():
            assert recorder.frame_provider() == (bytes([channel]), channel)
            status = recorder.status_provider()
            assert status["enabled"] and status["shadowMode"]
            assert status["channel"] == channel
            assert status["rotationDegrees"] == (90 if channel == 4 else 0)
    finally:
        for monitor in state.fall_monitors.values():
            monitor.close()


def test_freshness_is_independent_per_channel_and_rejects_stale_frames() -> None:
    feeds = [CameraFeed(i, str(i), "") for i in (1, 2)]
    monitors = {i: Mock(spec=FallMonitor) for i in (1, 2)}
    for monitor in monitors.values():
        monitor.update.return_value = {"enabled": True}
    worker = DetectionWorker(feeds, _settings(), monitors)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    detection = PersonDetection(10, 10, 50, 90, 0.9)
    for feed in feeds:
        worker._update_fall(feed, frame, 7, time.monotonic(), [detection])
        assert monitors[feed.channel].update.call_args.args[0] == [detection]
    worker._update_fall(feeds[0], frame, 7, time.monotonic(), [detection])
    assert monitors[1].update.call_args.args[0] == []
    worker._update_fall(feeds[1], frame, 8, time.monotonic(), [detection])
    assert monitors[2].update.call_args.args[0] == [detection]
    worker._update_fall(feeds[1], frame, 9, time.monotonic() - 5, [detection])
    assert monitors[2].update.call_args.args[0] == []


def _pose(horizontal: bool) -> PersonDetection:
    points = [PoseKeypoint(50, 50, 0.9) for _ in range(17)]
    if horizontal:
        coordinates = ((45, 75), (45, 85), (125, 75), (125, 85))
        box = (20, 65, 180, 145)
    else:
        coordinates = ((75, 35), (85, 35), (75, 95), (85, 95))
        box = (60, 20, 105, 170)
    for index, (x, y) in zip((5, 6, 11, 12), coordinates, strict=True):
        points[index] = PoseKeypoint(x, y, 0.9)
    return PersonDetection(*box, 0.9, tuple(points))


def test_fall_on_one_camera_cannot_advance_another_and_logs_are_separate(tmp_path) -> None:
    monitors = [FallMonitor(channel, tmp_path) for channel in (1, 2)]
    started = time.monotonic()
    try:
        for index in range(21):
            now = started + index * 0.25
            first = monitors[0].update([_pose(index > 0)], 200, 200, now=now)
            second = monitors[1].update([_pose(False)], 200, 200, now=now)
        assert first["state"] == "alert_pending"
        assert second["state"] == "normal"
    finally:
        for monitor in monitors:
            monitor.close()
    events = [json.loads(line) for line in (tmp_path / "fall-events-ch01.jsonl").read_text().splitlines()]
    assert events and all(event["channel"] == 1 for event in events)
    assert (tmp_path / "fall-events-ch02.jsonl").read_text() == ""
    for channel in (1, 2):
        samples = [json.loads(line) for line in (tmp_path / f"fall-calibration-ch{channel:02d}.jsonl").read_text().splitlines()]
        assert samples and all(sample["channel"] == channel for sample in samples)


def test_simultaneous_clips_and_retention_stay_separate_by_channel(tmp_path) -> None:
    ok, encoded = cv2.imencode(".jpg", np.zeros((64, 64, 3), dtype=np.uint8))
    assert ok
    jpeg = encoded.tobytes()

    def save_clip(channel):
        recorder = FallClipRecorder(channel=channel, frame_provider=lambda: (jpeg, 1), status_provider=lambda: {}, output_dir=tmp_path, fps=2, post_seconds=1, max_files=1)
        for sequence, state in enumerate(("normal", "possible_fall", "alert_pending", "normal", "normal", "normal")):
            recorder._observe(BufferedFrame(sequence * 0.5, sequence, jpeg), state)
        return recorder

    with ThreadPoolExecutor(max_workers=2) as pool:
        recorders = list(pool.map(save_clip, (1, 2)))
    for recorder in recorders:
        assert recorder.last_saved is not None and recorder.last_saved.is_file()
        capture = cv2.VideoCapture(str(recorder.last_saved))
        try:
            assert capture.isOpened() and capture.get(cv2.CAP_PROP_FRAME_COUNT) >= 5
        finally:
            capture.release()
    old = tmp_path / "queda-ch01_2000-01-01_00-00-00.mp4"
    old.write_bytes(b"old")
    os.utime(old, (1, 1))
    recorders[0]._prune_old_files()
    assert not old.exists()
    assert all(recorder.last_saved.is_file() for recorder in recorders)
