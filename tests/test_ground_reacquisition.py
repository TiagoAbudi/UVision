from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.fall_monitor import FallFeatures, FallMonitor
from camera_ai.detection.people_monitor import PeopleFallMonitor
from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint


def stationary_floor() -> FallFeatures:
    return FallFeatures(.54, 3.28, 72, .003, .03, 16, .85, True, 75, 60)


def test_live_missed_fall_features_can_reacquire_without_another_posture_change(tmp_path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/ch04-reacquired-ground-20261119.json").read_text(encoding="utf-8"))
    samples = fixture["samples"]
    geometry = CameraGeometry(4, (.145, .955), 8, 25)
    monitor = FallMonitor(4, tmp_path, geometry=geometry)
    origin = datetime.fromisoformat(samples[0]["timestamp"]).timestamp()
    started = time.monotonic()
    confirmed_at = None
    try:
        for sample in samples:
            seconds = datetime.fromisoformat(sample["timestamp"]).timestamp()-origin
            monitor._advance(FallFeatures(**sample["features"]), started+seconds)
            if monitor.status()["state"] == "alert_pending" and confirmed_at is None:
                confirmed_at = seconds
        assert confirmed_at is not None
        assert 3.0 <= confirmed_at <= 4.0
    finally:
        monitor.close()


@pytest.mark.parametrize("overrides", [
    {"geometry_ground": False}, {"geometry_ground": None},
    {"leg_vertical_deviation": 25}, {"torso_vertical_deviation": 30},
    {"leg_vertical_deviation": None}, {"confidence": .6},
    {"keypoints": 8}, {"zone_eligible": False}, {"center_speed": .10},
])
def test_static_reacquisition_requires_strong_calibrated_ground_evidence(tmp_path, overrides) -> None:
    monitor = FallMonitor(4, tmp_path, geometry=CameraGeometry(4, (.145, .955), 8, 25))
    started = time.monotonic()
    features = replace(stationary_floor(), **overrides)
    try:
        for index in range(30):
            monitor._advance(features, started+index*.25)
            assert monitor.status()["state"] == "normal"
    finally:
        monitor.close()


def test_camera_without_geometry_cannot_acquire_static_floor_from_box_shape(tmp_path) -> None:
    monitor = FallMonitor(1, tmp_path)
    started = time.monotonic()
    try:
        for index in range(30):
            monitor._advance(stationary_floor(), started+index*.25)
        assert monitor.status()["state"] == "normal"
    finally:
        monitor.close()


def test_reacquisition_respects_ground_time_and_resets_after_missing_evidence(tmp_path) -> None:
    monitor = FallMonitor(4, tmp_path, ground_confirm_seconds=6, geometry=CameraGeometry(4, (.145, .955), 8, 25))
    started = time.monotonic()
    try:
        for index in range(16):
            monitor._advance(stationary_floor(), started+index*.25)
            assert monitor.status()["state"] != "alert_pending"
        monitor._handle_missing(started+8)
        for index in range(24):
            monitor._advance(stationary_floor(), started+9+index*.25)
            assert monitor.status()["state"] != "alert_pending"
        monitor._advance(stationary_floor(), started+15)
        assert monitor.status()["state"] == "alert_pending"
    finally:
        monitor.close()


def test_new_person_track_after_six_second_gap_can_confirm_a_motionless_person_on_floor(tmp_path) -> None:
    points = [PoseKeypoint(50, 50, .9) for _ in range(17)]
    for indices, xy in (((5, 6), (40, 100)), ((11, 12), (100, 100)), ((15, 16), (150, 120))):
        for index in indices:
            points[index] = PoseKeypoint(*xy, .9)
    lying = PersonDetection(20, 65, 180, 145, .9, tuple(points))
    monitor = PeopleFallMonitor(4, tmp_path, geometry=CameraGeometry(4, (.5, -2), 8, 25))
    started = time.monotonic()
    try:
        monitor.update([lying], 200, 200, now=started)
        previous = monitor.detections[0].track_id
        monitor.update([], 200, 200, now=started+6)
        for index in range(14):
            status = monitor.update([lying], 200, 200, now=started+6.25+index*.25)
            if index < 12:
                assert status["state"] != "alert_pending"
        assert monitor.detections[0].track_id != previous
        assert status["state"] == "alert_pending"
    finally:
        monitor.close()
