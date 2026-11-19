from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from camera_ai.detection.yolo_detector import PersonDetection


@dataclass(frozen=True, slots=True)
class CameraZones:
    channel: int
    floors: tuple[tuple[tuple[float, float], ...], ...] = ()
    rest: tuple[tuple[tuple[float, float], ...], ...] = ()
    rotation: int = 0
    ground_seconds: float = 3.0
    view_changed: bool = False

    @classmethod
    def load(cls, path: Path, channel: int) -> "CameraZones":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("channel") != channel or data.get("coordinateSystem") != "raw_image_normalized_xy":
            raise ValueError("As zonas não correspondem ao canal ou enquadramento esperado.")
        def polygons(key):
            output = []
            for polygon in data.get(key, []):
                if len(polygon) < 3:
                    raise ValueError("Cada região precisa de pelo menos três pontos.")
                points = tuple(tuple(float(value) for value in point) for point in polygon)
                if any(len(point) != 2 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in point) for point in points):
                    raise ValueError("Região contém coordenadas inválidas.")
                area = abs(cv2.contourArea(np.asarray(points, dtype=np.float32)))
                if area < 0.0001:
                    raise ValueError("Região sem área útil.")
                output.append(points)
            return tuple(output)
        rotation, seconds = int(data.get("rotationDegrees", 0)), float(data.get("groundConfirmSeconds", 3))
        if rotation not in {0, 90, 180, 270} or not math.isfinite(seconds) or not 1 <= seconds <= 30:
            raise ValueError("Orientação ou tempo de confirmação inválido.")
        changed = data.get("viewChanged", False)
        if not isinstance(changed, bool):
            raise ValueError("Estado de enquadramento inválido.")
        return cls(channel, polygons("floor"), polygons("rest"), rotation, seconds, changed)

    def allows(self, detection: PersonDetection, width: int, height: int) -> bool:
        if self.view_changed:
            return False
        hips = [point for index, point in enumerate(detection.keypoints) if index in (11, 12) and point.confidence >= .4]
        point = ((sum(p.x for p in hips) / len(hips) / width, sum(p.y for p in hips) / len(hips) / height)
                 if hips else ((detection.x1+detection.x2)/(2*width), (detection.y1+detection.y2)/(2*height)))
        def inside(polygons):
            return any(cv2.pointPolygonTest(np.asarray(polygon, dtype=np.float32), point, False) >= 0 for polygon in polygons)
        return (not self.floors or inside(self.floors)) and not inside(self.rest)


def fit_vertical_point(lines: list) -> tuple[float, float]:
    if len(lines) < 2:
        raise ValueError("Marque pelo menos duas verticais do ambiente, em posições diferentes.")
    normals, constants = [], []
    for first, second in lines:
        dx, dy = second[0]-first[0], second[1]-first[1]
        length = math.hypot(dx, dy)
        if length < .04:
            raise ValueError("As linhas verticais precisam ser mais longas.")
        a, b = dy/length, -dx/length
        normals.append((a, b))
        constants.append(a*first[0]+b*first[1])
    matrix = np.asarray(normals)
    if np.linalg.cond(matrix) > 200:
        raise ValueError("Linhas quase paralelas; use referências em posições mais separadas.")
    point = np.linalg.lstsq(matrix, np.asarray(constants), rcond=None)[0]
    if not np.all(np.isfinite(point)) or np.max(np.abs(point)) > 10:
        raise ValueError("Perspectiva fora do intervalo suportado; revise as referências.")
    return float(point[0]), float(point[1])
