from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from camera_ai.detection.yolo_detector import PoseKeypoint


@dataclass(frozen=True, slots=True)
class CameraGeometry:
    channel: int
    vertical_point: tuple[float, float]
    min_leg_deviation: float = 12.0
    min_torso_deviation: float = 25.0
    strong_leg_deviation: float = 30.0
    strong_torso_deviation: float = 60.0

    @classmethod
    def load(cls, path: Path) -> "CameraGeometry":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("coordinateSystem") != "raw_image_normalized_xy":
            raise ValueError("Sistema de coordenadas da calibração inválido")
        point = tuple(float(value) for value in data["verticalVanishingPoint"])
        if len(point) != 2 or not all(math.isfinite(value) and abs(value) <= 10 for value in point):
            raise ValueError("Ponto de fuga da câmera inválido")
        leg = float(data["minLegDeviationDegrees"])
        torso = float(data["minTorsoDeviationDegrees"])
        strong_leg = float(data.get("strongLegDeviationDegrees", 30.0))
        strong_torso = float(data.get("strongTorsoDeviationDegrees", 60.0))
        if not all(math.isfinite(value) and 0 < value < 90 for value in (leg, torso, strong_leg, strong_torso)):
            raise ValueError("Limiares da calibração geométrica inválidos")
        return cls(
            channel=int(data["channel"]), vertical_point=point,
            min_leg_deviation=leg,
            min_torso_deviation=torso,
            strong_leg_deviation=strong_leg,
            strong_torso_deviation=strong_torso,
        )

    @staticmethod
    def _midpoint(points: tuple[PoseKeypoint, ...], first: int, second: int, width: int, height: int) -> tuple[float, float] | None:
        usable = [points[index] for index in (first, second) if index < len(points) and points[index].confidence >= 0.4]
        if not usable:
            return None
        return sum(point.x for point in usable) / len(usable) / width, sum(point.y for point in usable) / len(usable) / height

    @staticmethod
    def _deviation(first: tuple[float, float], second: tuple[float, float]) -> float | None:
        denominator = math.hypot(*first) * math.hypot(*second)
        if denominator < 1e-7:
            return None
        cosine = abs((first[0] * second[0] + first[1] * second[1]) / denominator)
        return math.degrees(math.acos(min(1.0, max(0.0, cosine))))

    def evaluate(self, points: tuple[PoseKeypoint, ...], width: int, height: int) -> tuple[bool | None, float | None, float | None]:
        shoulders = self._midpoint(points, 5, 6, width, height)
        hips = self._midpoint(points, 11, 12, width, height)
        ankles = self._midpoint(points, 15, 16, width, height)
        if shoulders is None or hips is None or ankles is None:
            return None, None, None
        leg_angle = self._deviation(
            (hips[0] - ankles[0], hips[1] - ankles[1]),
            (self.vertical_point[0] - ankles[0], self.vertical_point[1] - ankles[1]),
        )
        torso_angle = self._deviation(
            (shoulders[0] - hips[0], shoulders[1] - hips[1]),
            (self.vertical_point[0] - hips[0], self.vertical_point[1] - hips[1]),
        )
        if leg_angle is None or torso_angle is None:
            return None, leg_angle, torso_angle
        # Mild lean in both axes is common at the counter. At least one body
        # axis must substantially depart from the projected standing direction;
        # allow either legs or torso so folded and extended lying poses qualify.
        supported = (
            leg_angle >= self.min_leg_deviation
            and torso_angle >= self.min_torso_deviation
            and (leg_angle >= self.strong_leg_deviation or torso_angle >= self.strong_torso_deviation)
        )
        return supported, round(leg_angle, 1), round(torso_angle, 1)
