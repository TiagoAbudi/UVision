from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import cv2

from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    image_size: int = 640
    interval: float = .16
    analysis_max_side: int = 0

    def validate(self) -> None:
        if not isinstance(self.image_size, int) or not 320 <= self.image_size <= 960 or self.image_size % 32:
            raise ValueError("A resolução da IA deve ser múltipla de 32 entre 320 e 960.")
        if not math.isfinite(self.interval) or not .1 <= self.interval <= .75:
            raise ValueError("O intervalo da IA deve ficar entre 0,1 e 0,75 segundos para preservar amostras de postura.")
        if not isinstance(self.analysis_max_side, int) or self.analysis_max_side not in {0, 640, 960, 1280, 1920}:
            raise ValueError("Tamanho da imagem de análise inválido.")

    @classmethod
    def load(cls, path: Path, settings) -> "PerformanceProfile":
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "image_size": settings.yolo_image_size, "interval": settings.detection_interval,
        }
        result = cls(**data)
        result.validate()
        return result

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


def analysis_frame(frame, maximum: int):
    if not maximum or max(frame.shape[:2]) <= maximum:
        return frame
    ratio = maximum/max(frame.shape[:2])
    return cv2.resize(frame, (round(frame.shape[1]*ratio), round(frame.shape[0]*ratio)), interpolation=cv2.INTER_AREA)


def restore_coordinates(people: list[PersonDetection], analysis, original) -> list[PersonDetection]:
    if analysis.shape[:2] == original.shape[:2]:
        return people
    sx, sy = original.shape[1]/analysis.shape[1], original.shape[0]/analysis.shape[0]
    return [replace(person, x1=round(person.x1*sx), y1=round(person.y1*sy), x2=round(person.x2*sx), y2=round(person.y2*sy),
                    keypoints=tuple(PoseKeypoint(point.x*sx, point.y*sy, point.confidence) for point in person.keypoints))
            for person in people]
