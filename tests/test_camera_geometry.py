from __future__ import annotations

import time

from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.fall_monitor import FallFeatures, FallMonitor
from camera_ai.detection.yolo_detector import PoseKeypoint


def _points(shoulders, hips, ankles):
    points = [PoseKeypoint(0, 0, 0) for _ in range(17)]
    for indices, value in (((5, 6), shoulders), ((11, 12), hips), ((15, 16), ankles)):
        for index in indices:
            points[index] = PoseKeypoint(*value, 0.9)
    return tuple(points)


def test_perspective_distinguishes_standing_bending_and_lying() -> None:
    geometry = CameraGeometry(4, (0.5, -2.0), 8, 25)
    assert geometry.evaluate(_points((100, 50), (100, 100), (100, 160)), 200, 200)[0] is False
    assert geometry.evaluate(_points((150, 90), (100, 100), (100, 160)), 200, 200)[0] is False
    # Both axes mildly tilted still describe ordinary work at the sink.
    assert geometry.evaluate(_points((140, 96), (120, 120), (100, 160)), 200, 200)[0] is False
    assert geometry.evaluate(_points((40, 100), (100, 100), (150, 120)), 200, 200)[0] is True


def test_missing_lower_body_does_not_confirm_ground_contact() -> None:
    geometry = CameraGeometry(4, (0.5, -2.0), 8, 25)
    assert geometry.evaluate(tuple(PoseKeypoint(100, 100, 0.9) for _ in range(13)), 200, 200)[0] is None


def test_image_horizontal_posture_cannot_override_standing_geometry(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, geometry=CameraGeometry(4, (0.5, -2)))
    started = time.monotonic()
    try:
        for index in range(60):
            monitor._advance(FallFeatures(0.8, 2.2, 85, 0.05, 0.6, 15, 0.9, False, 2.0, 5.0), started + index * 0.25)
            assert monitor.status()["state"] not in {"down", "alert_pending"}
    finally:
        monitor.close()


def test_candidate_expires_before_unrelated_later_ground_pose(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, geometry=CameraGeometry(4, (0.5, -2)))
    started = time.monotonic()
    try:
        monitor._advance(FallFeatures(0.8, 1.5, 80, 0.3, 0.8, 14, 0.9, False), started)
        for index in range(1, 16):
            monitor._advance(FallFeatures(0.5, 1.5, 80, 0.01, 0.01, 14, 0.9, False), started + index * 0.25)
        assert monitor.status()["state"] == "normal"
        monitor._advance(FallFeatures(0.55, 1.9, 85, 0.01, 0.01, 14, 0.9, True), started + 4)
        assert monitor.status()["state"] == "normal"
    finally:
        monitor.close()


def test_supported_geometry_still_reaches_confirmation(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, geometry=CameraGeometry(4, (0.5, -2)))
    started = time.monotonic()
    try:
        monitor._advance(FallFeatures(0.8, 1.9, 85, 0.1, 0.8, 14, 0.9, True), started)
        for index in range(1, 16):
            monitor._advance(FallFeatures(0.55, 1.9, 85, 0.01, 0.01, 14, 0.9, True), started + index * 0.25)
        assert monitor.status()["state"] == "alert_pending"
    finally:
        monitor.close()


def test_confirmation_clock_starts_at_candidate_not_old_static_posture(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, geometry=CameraGeometry(4, (0.5, -2)))
    started = time.monotonic()
    try:
        for index in range(40):
            monitor._advance(FallFeatures(0.55, 1.9, 85, 0.01, 0.01, 14, 0.9, True), started + index * 0.25)
        assert monitor.status()["state"] == "normal"
        monitor._advance(FallFeatures(0.8, 1.9, 85, 0.1, 0.8, 14, 0.9, True), started + 10)
        for index in range(1, 12):
            monitor._advance(FallFeatures(0.55, 1.9, 85, 0.01, 0.01, 14, 0.9, True), started + 10 + index * 0.25)
            assert monitor.status()["state"] != "alert_pending"
        monitor._advance(FallFeatures(0.55, 1.9, 85, 0.01, 0.01, 14, 0.9, True), started + 13)
        assert monitor.status()["state"] == "alert_pending"
    finally:
        monitor.close()
