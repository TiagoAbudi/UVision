from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import torch
from ultralytics import YOLO

GPU_BATCH_SIZE = 4


@dataclass(frozen=True, slots=True)
class PoseKeypoint:
    x: float
    y: float
    confidence: float


@dataclass(frozen=True, slots=True)
class PersonDetection:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    keypoints: tuple[PoseKeypoint, ...] = ()
    track_id: int | None = None


class YoloPersonDetector:
    PERSON_CLASS_ID = 0
    MIN_PERSON_HEIGHT_TO_WIDTH = 0.75
    # COCO: bicycle, car, motorcycle, bus, truck, cat, dog.
    NON_PERSON_CLASSES = (1, 2, 3, 5, 7, 15, 16)

    def __init__(
        self,
        model_name: str,
        *,
        confidence: float = 0.50,
        image_size: int = 640,
        device: str = "auto",
        animal_filter: bool = True,
        animal_model_name: str | None = None,
    ) -> None:
        self.confidence = confidence
        self.image_size = image_size
        self.device = self._resolve_device(device)
        if self.device != "cpu":
            # GPU inference still uses CPU preprocessing and dispatch. Keep
            # these pools small beside the camera decoders and video buffers.
            torch.set_num_threads(2)
        self.quantization = 16 if self.device != "cpu" else None
        self.last_timings: dict[str, float] = {}
        self.model = YOLO(model_name)
        self.animal_model = (
            YOLO(animal_model_name or str(Path(model_name).with_name("yolo26s.pt")))
            if animal_filter else None
        )

    @staticmethod
    def _resolve_device(requested: str) -> str | int:
        normalized = requested.strip().lower()
        if normalized == "auto":
            return 0 if torch.cuda.is_available() else "cpu"
        if normalized == "cpu":
            return "cpu"
        if normalized.isdigit():
            if not torch.cuda.is_available():
                raise RuntimeError("YOLO_DEVICE pede GPU, mas CUDA não está disponível.")
            return int(normalized)
        return requested

    @property
    def device_label(self) -> str:
        if self.device == "cpu":
            return "CPU"
        return torch.cuda.get_device_name(int(self.device))

    def detect(self, frame) -> list[PersonDetection]:
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: list) -> list[list[PersonDetection]]:
        if not frames:
            return []
        if self.device != "cpu" and len(frames) > GPU_BATCH_SIZE:
            output = []
            timings = []
            for offset in range(0, len(frames), GPU_BATCH_SIZE):
                output.extend(self.detect_batch(frames[offset:offset+GPU_BATCH_SIZE]))
                timings.append(self.last_timings)
            self.last_timings = {key: sum(item[key] for item in timings) for key in timings[0]}
            return output
        started = time.perf_counter()
        inputs = frames if self.device == "cpu" else frames + [frames[-1]] * (GPU_BATCH_SIZE-len(frames))
        results = self.model.predict(
            source=inputs,
            classes=[self.PERSON_CLASS_ID],
            conf=self.confidence,
            imgsz=self.image_size,
            device=self.device,
            quantize=self.quantization,
            batch=len(inputs),
            verbose=False,
        )
        pose_done = time.perf_counter()
        detections: list[list[PersonDetection]] = []
        for result in results[:len(frames)]:
            if result.boxes is None or len(result.boxes) == 0:
                detections.append([])
                continue
            coordinates = result.boxes.xyxy.detach().cpu().tolist()
            confidences = result.boxes.conf.detach().cpu().tolist()
            valid_pose_indices: set[int] | None = None
            pose_keypoint_counts: list[int] | None = None
            pose_coordinates: list[list[list[float]]] | None = None
            pose_confidences: list[list[float]] | None = None
            keypoints = getattr(result, "keypoints", None)
            if keypoints is not None and getattr(keypoints, "conf", None) is not None:
                pose_confidences = keypoints.conf.detach().cpu().tolist()
                pose_coordinates = keypoints.xy.detach().cpu().tolist()
                pose_keypoint_counts = [
                    sum(value >= 0.30 for value in values) for values in pose_confidences
                ]
                valid_pose_indices = {
                    index
                    for index, count in enumerate(pose_keypoint_counts)
                    if count >= 5
                }
            camera_detections: list[PersonDetection] = []
            for index, ((x1, y1, x2, y2), score) in enumerate(
                zip(coordinates, confidences, strict=True)
            ):
                if valid_pose_indices is not None and index not in valid_pose_indices:
                    continue
                human_aspect = (y2 - y1) / max(x2 - x1, 1.0)
                strong_horizontal_pose = (
                    pose_keypoint_counts is not None and pose_keypoint_counts[index] >= 9
                )
                if human_aspect < self.MIN_PERSON_HEIGHT_TO_WIDTH and not strong_horizontal_pose:
                    continue
                pose = ()
                if pose_coordinates is not None and pose_confidences is not None:
                    pose = tuple(
                        PoseKeypoint(float(x), float(y), float(point_confidence))
                        for (x, y), point_confidence in zip(
                            pose_coordinates[index], pose_confidences[index], strict=True
                        )
                    )
                camera_detections.append(
                    PersonDetection(
                        x1=int(x1),
                        y1=int(y1),
                        x2=int(x2),
                        y2=int(y2),
                        confidence=float(score),
                        keypoints=pose,
                    )
                )
            detections.append(camera_detections)
        verification_started = time.perf_counter()
        if self.animal_model is not None:
            self._filter_non_people(frames, detections)
        completed = time.perf_counter()
        self.last_timings = {
            "poseInferenceMs": (pose_done-started)*1000,
            "posePostprocessMs": (verification_started-pose_done)*1000,
            "verificationMs": (completed-verification_started)*1000,
        }
        return detections

    def _filter_non_people(self, frames: list, detections: list[list[PersonDetection]]) -> None:
        # The pose network only knows people, and can hallucinate skeletons on
        # pets or parked vehicles. Verify candidates with the existing general
        # detector, retaining overlap checks for people beside these objects.
        crops = []
        candidates = []
        for frame_index, people in enumerate(detections):
            height, width = frames[frame_index].shape[:2]
            for detection in people:
                padding = round(max(detection.x2 - detection.x1, detection.y2 - detection.y1) * 0.20)
                left = max(0, detection.x1 - padding)
                top = max(0, detection.y1 - padding)
                right = min(width, detection.x2 + padding)
                bottom = min(height, detection.y2 + padding)
                if right <= left or bottom <= top:
                    continue
                crops.append(frames[frame_index][top:bottom, left:right])
                candidates.append((frame_index, detection, left, top))
        if not crops:
            return
        # Variable crop shapes and batch sizes can stall CUDA for seconds when
        # a new person appears. Keep the GPU verifier tensor shape fixed; only
        # real candidates below are consumed, never the padding outputs.
        batch_limit = GPU_BATCH_SIZE if self.device != "cpu" else 8
        for offset in range(0, len(crops), batch_limit):
            subset = crops[offset:offset + batch_limit]
            inputs = subset
            if self.device != "cpu":
                inputs = subset + [subset[-1]] * (batch_limit-len(subset))
            results = self.animal_model.predict(
                source=inputs,
                classes=[self.PERSON_CLASS_ID, *self.NON_PERSON_CLASSES],
                conf=0.20,
                imgsz=640,
                device=self.device,
                quantize=self.quantization,
                batch=len(inputs),
                rect=self.device == "cpu",
                verbose=False,
            )
            for (frame_index, detection, left, top), result in zip(
                candidates[offset:offset + batch_limit], results[:len(subset)], strict=True
            ):
                observations = []
                if result.boxes is not None:
                    coordinates = result.boxes.xyxy.detach().cpu().tolist()
                    scores = result.boxes.conf.detach().cpu().tolist()
                    classes = result.boxes.cls.detach().cpu().tolist()
                    observations = [
                        ((x1 + left, y1 + top, x2 + left, y2 + top), score, int(label))
                        for (x1, y1, x2, y2), score, label in zip(coordinates, scores, classes, strict=True)
                    ]
                if self._nonperson_dominates(detection, observations) or self._unsupported_pose(detection, observations):
                    detections[frame_index].remove(detection)

    @classmethod
    def _verification_scores(cls, detection: PersonDetection, observations: list) -> tuple[float, float]:
        person_score = 0.0
        nonperson_score = 0.0
        person_area = max(1, (detection.x2 - detection.x1) * (detection.y2 - detection.y1))
        for (x1, y1, x2, y2), score, label in observations:
            intersection = max(0.0, min(detection.x2, x2) - max(detection.x1, x1)) * max(
                0.0, min(detection.y2, y2) - max(detection.y1, y1)
            )
            area = max(1.0, (x2 - x1) * (y2 - y1))
            overlap = intersection / max(1.0, person_area + area - intersection)
            if overlap < 0.30 or intersection / person_area < 0.55:
                continue
            if label == 0:
                person_score = max(person_score, score)
            elif label in cls.NON_PERSON_CLASSES:
                nonperson_score = max(nonperson_score, score)
        return person_score, nonperson_score

    @classmethod
    def _nonperson_dominates(cls, detection: PersonDetection, observations: list) -> bool:
        person_score, nonperson_score = cls._verification_scores(detection, observations)
        # Compare scores within the same model; pose scores are not calibrated
        # against the independent object detector. An adjacent object must not
        # suppress a person, and horizontal people remain eligible.
        return nonperson_score >= 0.28 and nonperson_score >= person_score + 0.10

    @classmethod
    def _unsupported_pose(cls, detection: PersonDetection, observations: list) -> bool:
        if not detection.keypoints:
            return False
        # Even complete skeletons can be hallucinated on parked motorcycles.
        # Require independent human evidence; partial poses need stronger
        # support, while complete lying poses keep the verifier's lower floor.
        complete_leg = any(
            all(index < len(detection.keypoints) and detection.keypoints[index].confidence >= 0.30 for index in chain)
            for chain in ((11, 13, 15), (12, 14, 16))
        )
        person_score, _ = cls._verification_scores(detection, observations)
        return person_score < (0.20 if complete_leg else 0.40)

    def warmup(self, frame) -> None:
        self.detect(frame)
        if self.animal_model is not None and self.device != "cpu":
            self.animal_model.predict(
                source=[frame] * GPU_BATCH_SIZE, classes=[self.PERSON_CLASS_ID, *self.NON_PERSON_CLASSES],
                conf=0.20, imgsz=640, device=self.device, quantize=self.quantization,
                batch=GPU_BATCH_SIZE, rect=False, verbose=False,
            )

