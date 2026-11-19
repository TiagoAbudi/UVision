import json
import time

import pytest

from camera_ai.detection.zones import CameraZones, fit_vertical_point
from camera_ai.detection.yolo_detector import PersonDetection
from camera_ai.detection.fall_monitor import FallMonitor, FallFeatures


def test_rest_region_blocks_fall_confirmation_but_does_not_hide_people(tmp_path) -> None:
    zones = CameraZones(1, floors=(((0, 0), (1, 0), (1, 1), (0, 1)),), rest=(((.4, .4), (.6, .4), (.6, .6), (.4, .6)),))
    person = PersonDetection(40, 40, 60, 60, .9)
    assert not zones.allows(person, 100, 100)
    assert zones.allows(PersonDetection(10, 10, 30, 30, .9), 100, 100)
    monitor = FallMonitor(1, tmp_path, zones=zones)
    try:
        started = time.monotonic()
        for index in range(30):
            monitor._advance(FallFeatures(.8, 1.9, 85, .01, .8, 15, .9, zone_eligible=False), started+index*.25)
            assert monitor.status()["state"] not in {"down", "alert_pending"}
    finally:
        monitor.close()


def test_invalid_zone_cannot_be_loaded_for_another_camera(tmp_path) -> None:
    path = tmp_path / "zones.json"
    path.write_text(json.dumps({"channel": 2, "coordinateSystem": "raw_image_normalized_xy"}))
    with pytest.raises(ValueError):
        CameraZones.load(path, 1)


def test_vertical_references_fit_and_reject_parallel_lines() -> None:
    assert fit_vertical_point([[(0, 1), (.5, 0)], [(1, 1), (.5, 0)]]) == pytest.approx((.5, 0))
    with pytest.raises(ValueError):
        fit_vertical_point([[(0, 1), (0, 0)], [(1, 1), (1, 0)]])


def test_ptz_view_change_blocks_confirmation_until_recalibration(tmp_path) -> None:
    zones = CameraZones(9, view_changed=True)
    assert not zones.allows(PersonDetection(10, 10, 50, 80, .9), 100, 100)
    monitor = FallMonitor(9, tmp_path, zones=zones)
    try:
        assert monitor.status()["calibrationRequired"]
        assert not monitor.status()["perspectiveGuard"]
    finally:
        monitor.close()
