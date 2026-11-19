from __future__ import annotations

import cv2
import numpy as np

from camera_ai import desktop_ui
from camera_ai.event_store import EventStore


def test_review_buttons_persist_classification_and_filter_uncertain(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    path = recordings / "queda-ch04_2026-11-19_12-00-00.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 2, (64, 64))
    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    monkeypatch.setattr(desktop_ui, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(desktop_ui, "RECORDINGS", recordings)
    window = desktop_ui.ProfessionalWindow(start_backend=False)
    try:
        window.review_current("unsure")
        assert EventStore(tmp_path / "runtime/events.json").reviews()[path.name]["label"] == "unsure"
        assert window.protect_clip.isChecked()
        window.filter_box.setCurrentIndex(4)
        assert window.recording_list.count() == 1
        window.filter_box.setCurrentIndex(3)
        assert window.recording_list.count() == 0
        assert all(not button.isEnabled() for button in window.review_buttons)
    finally:
        window.close()
        app.processEvents()
