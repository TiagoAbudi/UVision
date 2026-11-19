from __future__ import annotations

import time
from types import SimpleNamespace

import cv2
import numpy as np

from camera_ai import desktop_ui
from camera_ai.web_dashboard import CameraFeed


def test_library_filters_camera_and_summary_highlights_any_camera(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    monkeypatch.setattr(desktop_ui, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(desktop_ui, "RECORDINGS", recordings)
    for channel in (1, 4):
        path = recordings / f"queda-ch{channel:02d}_2026-10-29_12-00-00.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 2, (64, 64))
        assert writer.isOpened()
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
        writer.release()

    window = desktop_ui.ProfessionalWindow(start_backend=False)
    try:
        assert window.recording_list.count() == 2
        feeds = [CameraFeed(channel, name, "") for channel, name in ((1, "Entrada"), (4, "Cozinha"), (9, "Adicional"))]
        for feed in feeds:
            feed._last_frame_monotonic = time.monotonic()
            feed.set_fall_status({"enabled": True, "state": "normal", "shadowMode": True})
        feeds[0].set_fall_status({"enabled": True, "state": "alert_pending", "shadowMode": True})
        window.backend = SimpleNamespace(
            feeds=feeds,
            feed=lambda channel: next((feed for feed in feeds if feed.channel == channel), None),
            detection_worker=SimpleNamespace(status=lambda: {"ready": True, "inferenceMs": 100}),
        )
        window._refresh_camera_filter()
        window._refresh_status()
        assert window.fall_card.value.text() == "Queda confirmada"
        assert window.fall_card.hint.text() == "CH 01 · Entrada"
        window.camera_filter.setCurrentIndex(window.camera_filter.findData(1))
        assert window.recording_list.count() == 1
        assert "CH 01 · Entrada" in window.video_description.text()
        window.camera_filter.setCurrentIndex(window.camera_filter.findData(9))
        assert window.recording_list.count() == 0
        assert window._current_clip is None
        assert not window.play_button.isEnabled()
    finally:
        window.backend = None
        window.close()
        app.processEvents()


def test_recording_snapshot_matches_sequence_and_skips_offline_frames() -> None:
    feed = CameraFeed(2, "Entrada", "")
    feed._frame = np.full((64, 64, 3), 120, dtype=np.uint8)
    feed._width = feed._height = 64
    feed._frame_number = 17
    feed._last_frame_monotonic = time.monotonic()
    jpeg, sequence = feed.recording_snapshot()
    assert sequence == 17
    assert cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR) is not None
    feed._last_frame_monotonic = time.monotonic() - 10
    assert feed.recording_snapshot() == (b"", 17)
