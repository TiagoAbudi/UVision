from __future__ import annotations

import time

from camera_ai.detection.people_monitor import PeopleFallMonitor
from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint
from camera_ai.detection.fall_monitor import FallFeatures


def test_person_id_survives_order_and_box_size_changes(tmp_path) -> None:
    monitor = PeopleFallMonitor(1, tmp_path)
    now = time.monotonic()
    try:
        monitor.update([PersonDetection(10, 10, 70, 150, .9), PersonDetection(220, 20, 260, 120, .9)], 400, 200, now=now)
        first, second = [d.track_id for d in monitor.detections]
        monitor.update([PersonDetection(210, 10, 285, 180, .9), PersonDetection(15, 15, 65, 145, .9)], 400, 200, now=now+.25)
        assert [d.track_id for d in monitor.detections] == [second, first]
    finally:
        monitor.close()


def test_larger_person_cannot_replace_smaller_person_fall_history(tmp_path) -> None:
    monitor = PeopleFallMonitor(1, tmp_path)
    now = time.monotonic()
    try:
        monitor.update([PersonDetection(10, 10, 70, 150, .9)], 400, 200, now=now)
        first = monitor.detections[0].track_id
        engine = monitor._tracks[first].monitor
        engine._advance(FallFeatures(.8, 1.8, 85, .1, .8, 15, .9), now)
        for index in range(1, 16):
            engine._advance(FallFeatures(.55, 1.8, 85, .01, .01, 15, .9), now+index*.25)
        monitor.update([PersonDetection(210, 5, 395, 195, .9)], 400, 200, now=now+4)
        assert monitor.detections[0].track_id != first
        new = monitor._tracks[monitor.detections[0].track_id].monitor
        assert new.status()["state"] == "normal"
    finally:
        monitor.close()


def test_missing_detections_expire_tracks_and_cannot_confirm(tmp_path) -> None:
    monitor = PeopleFallMonitor(1, tmp_path)
    now = time.monotonic()
    try:
        monitor.update([PersonDetection(10, 10, 70, 150, .9)], 400, 200, now=now)
        first = monitor.detections[0].track_id
        status = monitor.update([], 400, 200, now=now+4)
        assert status["state"] == "normal" and status["people"] == []
        monitor.update([PersonDetection(10, 10, 70, 150, .9)], 400, 200, now=now+4.25)
        assert monitor.detections[0].track_id != first
    finally:
        monitor.close()


def test_smaller_person_fall_continues_when_larger_person_enters(tmp_path) -> None:
    def person(down, other=False):
        points = [PoseKeypoint(50, 50, .9) for _ in range(17)]
        if other:
            box, shoulder, hip = (220, 0, 400, 195), (300, 30), (300, 100)
        elif down:
            box, shoulder, hip = (10, 50, 150, 100), (30, 60), (120, 60)
        else:
            box, shoulder, hip = (40, 10, 70, 150), (50, 30), (50, 90)
        for index in (5, 6): points[index] = PoseKeypoint(*shoulder, .9)
        for index in (11, 12): points[index] = PoseKeypoint(*hip, .9)
        return PersonDetection(*box, .9, tuple(points))
    monitor = PeopleFallMonitor(1, tmp_path)
    started = time.monotonic()
    try:
        monitor.update([person(False)], 400, 200, now=started)
        first = monitor.detections[0].track_id
        for index in range(1, 24):
            detections = [person(True)] + ([person(False, True)] if index >= 4 else [])
            status = monitor.update(detections, 400, 200, now=started+index*.25)
        assert monitor.detections[0].track_id == first
        assert status["state"] == "alert_pending" and status["trackId"] == first
        assert monitor._tracks[monitor.detections[1].track_id].monitor.status()["state"] == "normal"
    finally:
        monitor.close()
