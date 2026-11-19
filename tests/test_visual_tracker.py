from __future__ import annotations

import cv2
import numpy as np

from camera_ai.detection.visual_tracker import VisualPersonTracker
from camera_ai.detection.yolo_detector import PersonDetection


def _scene(shift: int = 0) -> np.ndarray:
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    texture = np.random.default_rng(7).integers(30, 240, size=(100, 70, 3), dtype=np.uint8)
    image[70:170, 80 + shift:150 + shift] = texture
    return image


def test_box_follows_image_motion_between_yolo_results() -> None:
    tracker = VisualPersonTracker()
    source = _scene()
    tracker.observe([PersonDetection(80, 70, 150, 170, 0.9)], source, source, 0, 100.0, 100.0)
    output = tracker.advance(_scene(10), 1, 100.1)
    assert len(output) == 1
    assert abs(output[0].x1 - 90) <= 2
    # Re-rendering the same frame must not apply displacement twice.
    repeated = tracker.advance(_scene(10), 1, 100.12)
    assert repeated[0].x1 == output[0].x1


def test_late_yolo_box_is_aligned_to_newer_display_frame() -> None:
    tracker = VisualPersonTracker()
    source = _scene()
    tracker.observe([PersonDetection(80, 70, 150, 170, 0.9)], source, _scene(12), 2, 100.0, 100.2)
    output = tracker.advance(_scene(12), 2, 100.2)
    assert abs(output[0].x1 - 92) <= 2


def test_stale_result_and_missing_detections_do_not_leave_ghost_boxes() -> None:
    tracker = VisualPersonTracker()
    source = _scene()
    person = PersonDetection(80, 70, 150, 170, 0.9)
    tracker.observe([person], source, source, 0, 100.0, 100.0)
    assert tracker.advance(source, 0, 100.6) == []
    tracker.observe([person], source, source, 1, 100.0, 100.7)
    assert tracker.advance(source, 1, 100.7) == []
    tracker.observe([person], source, source, 2, 101.0, 101.0)
    tracker.observe([], source, source, 3, 101.1, 101.1)
    tracker.observe([], source, source, 4, 101.2, 101.2)
    assert tracker.advance(source, 4, 101.2) == []


def test_size_jitter_is_smoothed_without_delaying_large_pose_changes() -> None:
    tracker = VisualPersonTracker()
    source = _scene()
    tracker.observe([PersonDetection(80, 70, 150, 170, 0.9)], source, source, 0, 100.0, 100.0)
    tracker.observe([PersonDetection(78, 68, 152, 172, 0.9)], source, source, 1, 100.1, 100.1)
    output = tracker.advance(source, 1, 100.1)[0]
    assert 70 < output.x2 - output.x1 < 74
    tracker.observe([PersonDetection(65, 90, 175, 150, 0.9)], source, source, 2, 100.2, 100.2)
    output = tracker.advance(source, 2, 100.2)[0]
    assert output.x2 - output.x1 > 95
