from __future__ import annotations

import time

from camera_ai.detection import FallMonitor, PersonDetection, PoseKeypoint
from camera_ai.detection.fall_monitor import FallFeatures


def _pose(horizontal: bool) -> tuple[PoseKeypoint, ...]:
    points = [PoseKeypoint(50, 50, 0.9) for _ in range(17)]
    if horizontal:
        points[5] = PoseKeypoint(45, 75, 0.9)
        points[6] = PoseKeypoint(45, 85, 0.9)
        points[11] = PoseKeypoint(125, 75, 0.9)
        points[12] = PoseKeypoint(125, 85, 0.9)
    else:
        points[5] = PoseKeypoint(75, 35, 0.9)
        points[6] = PoseKeypoint(85, 35, 0.9)
        points[11] = PoseKeypoint(75, 95, 0.9)
        points[12] = PoseKeypoint(85, 95, 0.9)
    return tuple(points)


def _detection(horizontal: bool) -> PersonDetection:
    if horizontal:
        return PersonDetection(20, 65, 180, 145, 0.9, _pose(True))
    return PersonDetection(60, 20, 105, 170, 0.9, _pose(False))


def test_fall_monitor_reaches_pending_alert_after_temporal_confirmation(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, shadow_mode=True)
    started = time.monotonic()
    try:
        monitor.update([_detection(False)], 200, 200, now=started)
        status = monitor.update([_detection(True)], 200, 200, now=started + 0.25)
        assert status["state"] == "possible_fall"

        for index in range(2, 7):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
        assert status["state"] == "down"

        for index in range(7, 20):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
        assert status["state"] == "alert_pending"
        assert status["shadowMode"] is True
    finally:
        monitor.close()


def test_missing_person_cannot_confirm_a_fall(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path)
    started = time.monotonic()
    try:
        monitor.update([_detection(False)], 200, 200, now=started)
        monitor.update([_detection(True)], 200, 200, now=started + 0.25)
        states = [
            monitor.update([], 200, 200, now=started + index * 0.25)["state"]
            for index in range(2, 24)
        ]
        assert "down" not in states
        assert "alert_pending" not in states
        assert states[-1] == "normal"
        assert monitor.status()["features"] is None
    finally:
        monitor.close()


def test_interrupted_ground_evidence_restarts_confirmation(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path)
    started = time.monotonic()
    try:
        monitor.update([_detection(False)], 200, 200, now=started)
        for index in range(1, 10):
            monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
        monitor.update([], 200, 200, now=started + 2.5)
        # Time in DOWN before losing the pose must not count after it returns.
        for index in range(11, 22):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
            assert status["state"] != "alert_pending"
        for index in range(22, 25):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
        assert status["state"] == "alert_pending"
    finally:
        monitor.close()


def test_fall_monitor_does_not_trigger_for_normal_upright_motion(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, shadow_mode=True)
    started = time.monotonic()
    try:
        for index in range(8):
            status = monitor.update(
                [_detection(False)],
                200,
                200,
                now=started + index * 0.25,
            )
        assert status["state"] == "normal"
        assert status["features"]["score"] < 0.52
    finally:
        monitor.close()


def test_fall_monitor_compensates_quarter_turn_camera(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, shadow_mode=True, rotation_degrees=90)
    try:
        status = monitor.update([_detection(True)], 200, 200)
        assert status["state"] == "normal"
        assert status["rotationDegrees"] == 90
        assert status["features"]["bbox_aspect"] == 0.5
        assert status["features"]["torso_angle"] < 10
    finally:
        monitor.close()


def test_wide_box_at_counter_is_not_ground_posture(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path)
    started = time.monotonic()
    try:
        suspect = FallFeatures(0.75, 1.9, 55, 0.14, 0.8, 14, 0.8)
        stationary = FallFeatures(0.45, 1.9, 55, 0.01, 0.05, 14, 0.8)
        monitor._advance(suspect, started)
        for index in range(1, 40):
            monitor._advance(stationary, started + index * 0.25)
        assert monitor.status()["state"] not in {"down", "alert_pending"}
    finally:
        monitor.close()


def test_ground_duration_is_configurable(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, ground_confirm_seconds=6)
    started = time.monotonic()
    try:
        monitor.update([_detection(False)], 200, 200, now=started)
        for index in range(1, 24):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
            assert status["state"] != "alert_pending"
        for index in range(24, 29):
            status = monitor.update([_detection(True)], 200, 200, now=started + index * 0.25)
        assert status["state"] == "alert_pending"
    finally:
        monitor.close()
