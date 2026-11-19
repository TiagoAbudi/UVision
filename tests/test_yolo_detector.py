from __future__ import annotations

import numpy as np
import pytest

from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint, YoloPersonDetector


class _ListValue:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class _Boxes:
    def __init__(self, coordinates, confidences):
        self.xyxy = _ListValue(coordinates)
        self.conf = _ListValue(confidences)

    def __len__(self):
        return len(self.conf.value)


class _Result:
    def __init__(self, boxes, keypoints=None):
        self.boxes = boxes
        self.keypoints = keypoints


class _Keypoints:
    def __init__(self, confidences):
        self.conf = _ListValue(confidences)
        self.xy = _ListValue(
            [
                [[float(index), float(index * 2)] for index, _ in enumerate(person)]
                for person in confidences
            ]
        )


class _Model:
    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [
            _Result(_Boxes([[10.2, 20.7, 80.9, 160.1]], [0.81])),
            _Result(_Boxes([], [])),
        ]


def _detector() -> YoloPersonDetector:
    detector = object.__new__(YoloPersonDetector)
    detector.confidence = 0.4
    detector.image_size = 640
    detector.device = 0
    detector.quantization = 16
    detector.model = _Model()
    detector.animal_model = None
    return detector


def test_detect_batch_preserves_camera_order_and_coordinates() -> None:
    detector = _detector()

    results = detector.detect_batch(["frame-one", "frame-two"])

    assert len(results) == 2
    assert len(results[0]) == 1
    assert results[0][0].x1 == 10
    assert results[0][0].y2 == 160
    assert results[0][0].confidence == 0.81
    assert results[1] == []
    assert detector.model.calls[0]["batch"] == 4
    assert detector.model.calls[0]["classes"] == [0]


def test_detect_batch_skips_model_for_empty_input() -> None:
    detector = _detector()

    assert detector.detect_batch([]) == []
    assert detector.model.calls == []


def test_pose_requires_five_human_keypoints() -> None:
    detector = _detector()
    detector.model.predict = lambda **_: [
        _Result(
            _Boxes(
                [[1, 2, 30, 40], [5, 6, 25, 20]],
                [0.9, 0.8],
            ),
            _Keypoints(
                [
                    [0.8, 0.7, 0.6, 0.5, 0.4, 0.1],
                    [0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
                ]
            ),
        )
    ]

    results = detector.detect_batch(["frame"])

    assert len(results[0]) == 1
    assert results[0][0].confidence == 0.9


def test_animal_verifier_rejects_matching_dog() -> None:
    person = PersonDetection(10, 10, 110, 110, 0.85)
    assert YoloPersonDetector._nonperson_dominates(person, [((10, 10, 110, 110), 0.9, 16)])


def test_animal_verifier_preserves_person_beside_pet() -> None:
    person = PersonDetection(10, 10, 110, 210, 0.85)
    assert not YoloPersonDetector._nonperson_dominates(person, [((90, 170, 120, 210), 0.95, 16)])


def test_animal_verifier_preserves_lying_person_when_person_class_wins() -> None:
    person = PersonDetection(10, 10, 210, 80, 0.8)
    observations = [((10, 10, 210, 80), 0.82, 0), ((10, 10, 210, 80), 0.60, 16)]
    assert not YoloPersonDetector._nonperson_dominates(person, observations)


def test_animal_verifier_rejects_small_pet_with_ambiguous_person_score() -> None:
    # CH04 pet crops were predicted as cat 0.298 / person 0.161 by the
    # independent detector, while the pose model hallucinated a human skeleton.
    person = PersonDetection(10, 10, 110, 210, 0.75)
    observations = [((10, 10, 110, 210), 0.298, 15), ((10, 10, 110, 210), 0.161, 0)]
    assert YoloPersonDetector._nonperson_dominates(person, observations)


def test_animal_upper_body_hallucination_requires_independent_person_support() -> None:
    pose = tuple(PoseKeypoint(index, index, 0.9 if index < 15 else 0.0) for index in range(17))
    person = PersonDetection(10, 10, 110, 210, 0.75, pose)
    assert YoloPersonDetector._unsupported_pose(person, [])
    # Seated or occluded people remain valid with independent human evidence.
    assert not YoloPersonDetector._unsupported_pose(person, [((10, 10, 110, 210), 0.75, 0)])


def test_complete_skeleton_also_requires_independent_person_evidence() -> None:
    pose = tuple(PoseKeypoint(index, index, 0.9) for index in range(17))
    person = PersonDetection(10, 10, 110, 210, 0.95, pose)
    assert YoloPersonDetector._unsupported_pose(person, [])


def test_complete_lying_pose_accepts_lower_independent_person_confidence() -> None:
    pose = tuple(PoseKeypoint(index, index, 0.9) for index in range(17))
    person = PersonDetection(10, 10, 210, 80, 0.85, pose)
    assert not YoloPersonDetector._unsupported_pose(person, [((10, 10, 210, 80), 0.25, 0)])


def test_vehicle_verifier_rejects_motorcycle_with_hallucinated_pose() -> None:
    pose = tuple(PoseKeypoint(index, index, 0.9) for index in range(17))
    person = PersonDetection(10, 10, 110, 210, 0.85, pose)
    assert YoloPersonDetector._nonperson_dominates(person, [((10, 10, 110, 210), 0.8, 3)])


def test_vehicle_verifier_preserves_person_beside_motorcycle() -> None:
    person = PersonDetection(10, 10, 110, 210, 0.85)
    assert not YoloPersonDetector._nonperson_dominates(person, [((90, 90, 230, 210), 0.95, 3)])


def test_vehicle_verifier_preserves_person_when_human_evidence_wins() -> None:
    person = PersonDetection(10, 10, 110, 210, 0.85)
    observations = [((10, 10, 110, 210), 0.85, 0), ((10, 10, 110, 210), 0.70, 3)]
    assert not YoloPersonDetector._nonperson_dominates(person, observations)


def test_vehicle_verification_requests_motorcycle_and_removes_candidate() -> None:
    detector = _detector()
    detector.model.predict = lambda **_: [
        _Result(_Boxes([[10, 10, 110, 210]], [0.85]), _Keypoints([[0.9] * 17]))
    ]
    verifier = _Model()

    def verify(**kwargs):
        verifier.calls.append(kwargs)
        boxes = _Boxes([[10, 10, 110, 210]], [0.8])
        boxes.cls = _ListValue([3])
        return [_Result(boxes)]

    verifier.predict = verify
    detector.animal_model = verifier
    assert detector.detect_batch([np.zeros((240, 140, 3), dtype=np.uint8)]) == [[]]
    assert 0 in verifier.calls[0]["classes"]
    assert 3 in verifier.calls[0]["classes"]


@pytest.mark.parametrize("missing_boxes", [False, True])
def test_pose_is_rejected_when_verifier_finds_nothing(missing_boxes) -> None:
    detector = _detector()
    detector.model.predict = lambda **_: [
        _Result(_Boxes([[10, 10, 110, 210]], [0.95]), _Keypoints([[0.9] * 17]))
    ]
    boxes = None if missing_boxes else _Boxes([], [])
    if boxes is not None:
        boxes.cls = _ListValue([])
    verifier = _Model()
    verifier.predict = lambda **_: [_Result(boxes)]
    detector.animal_model = verifier
    assert detector.detect_batch([np.zeros((240, 140, 3), dtype=np.uint8)]) == [[]]


@pytest.mark.parametrize("device", [0, "cpu"])
def test_verifier_padding_outputs_never_become_real_candidates(device) -> None:
    detector = _detector()
    detector.device = device
    detector.model.predict = lambda **_: [
        _Result(_Boxes([[10, 10, 110, 210]], [.95]), _Keypoints([[.9]*17]))
    ]
    verifier = _Model()
    def verify(**kwargs):
        verifier.calls.append(kwargs)
        results = []
        for index in range(len(kwargs["source"])):
            boxes = _Boxes([[10, 10, 110, 210]], [.9])
            boxes.cls = _ListValue([0 if index == 0 else 16])
            results.append(_Result(boxes))
        return results
    verifier.predict = verify
    detector.animal_model = verifier
    people = detector.detect_batch([np.zeros((240, 140, 3), dtype=np.uint8)])
    assert len(people) == 1 and len(people[0]) == 1
    assert len(verifier.calls[0]["source"]) == (4 if device != "cpu" else 1)
    assert verifier.calls[0]["rect"] is (device == "cpu")


def test_fixed_gpu_batches_preserve_all_camera_results_in_order() -> None:
    detector = _detector()
    calls = []
    def predict(**kwargs):
        calls.append([int(frame[0, 0, 0]) for frame in kwargs["source"]])
        return [_Result(_Boxes([[value*10, 0, value*10+30, 100]], [.9])) for value in calls[-1]]
    detector.model.predict = predict
    frames = [np.full((120, 120, 3), index, dtype=np.uint8) for index in range(9)]
    results = detector.detect_batch(frames)
    assert [people[0].x1 for people in results] == list(range(0, 90, 10))
    assert calls == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 8, 8, 8]]
