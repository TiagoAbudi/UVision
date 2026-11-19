"""Explore the camera's projected standing direction using cached pose sequences."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from camera_ai.web_dashboard import PROJECT_DIR


ROOT = PROJECT_DIR / "datasets" / "ch04-calibration"


def midpoint(points: list[dict], indices: tuple[int, int]) -> np.ndarray | None:
    valid = [points[index] for index in indices if index < len(points) and points[index]["confidence"] >= 0.4]
    if not valid:
        return None
    return np.mean([[point["x"] / 960, point["y"] / 1080] for point in valid], axis=0)


def segments(person: dict):
    points = person["keypoints"]
    shoulder = midpoint(points, (5, 6))
    hip = midpoint(points, (11, 12))
    foot = midpoint(points, (15, 16))
    return shoulder, hip, foot


def angle(first: np.ndarray, second: np.ndarray) -> float:
    cosine = float(np.dot(first, second) / max(1e-8, np.linalg.norm(first) * np.linalg.norm(second)))
    return math.degrees(math.acos(float(np.clip(abs(cosine), 0, 1))))


def fit() -> tuple[np.ndarray, int]:
    source = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    normals, offsets, starts, ends = [], [], [], []
    for item in source["clips"]:
        if item["fall"]:
            continue
        cached = ROOT / "poses" / (Path(item["file"]).stem + ".json")
        for row in json.loads(cached.read_text())["samples"]:
            for person in row["people"]:
                _shoulder, hip, foot = segments(person)
                if hip is None or foot is None or np.linalg.norm(hip - foot) < 0.06:
                    continue
                direction = hip - foot
                normal = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction)
                normals.append(normal)
                offsets.append(float(normal @ foot))
                starts.append(foot)
                ends.append(hip)
    normals, offsets, starts, ends = map(np.asarray, (normals, offsets, starts, ends))
    rng = np.random.default_rng(4)
    best, best_score = None, -1
    for _ in range(2000):
        indices = rng.choice(len(normals), 2, replace=False)
        if abs(np.linalg.det(normals[indices])) < 0.02:
            continue
        point = np.linalg.solve(normals[indices], offsets[indices])
        if np.linalg.norm(point) > 10:
            continue
        vectors = point - starts
        body = ends - starts
        cosines = np.abs(np.sum(vectors * body, axis=1)) / np.maximum(1e-8, np.linalg.norm(vectors, axis=1) * np.linalg.norm(body, axis=1))
        inliers = cosines > math.cos(math.radians(12))
        score = int(inliers.sum())
        if score > best_score:
            best, best_score = point, score
    if best is None:
        raise RuntimeError("No stable projected vertical direction found")
    return best, best_score


def main() -> None:
    point, inliers = fit()
    print("projected_vertical", point.tolist(), "inliers", inliers)
    for cached in sorted((ROOT / "poses").glob("*.json")):
        values = []
        for row in json.loads(cached.read_text())["samples"]:
            if not row["people"]:
                continue
            person = max(row["people"], key=lambda p: (p["x2"] - p["x1"]) * (p["y2"] - p["y1"]))
            shoulder, hip, foot = segments(person)
            if shoulder is None or hip is None or foot is None:
                continue
            leg = angle(hip - foot, point - foot)
            torso = angle(shoulder - hip, point - hip)
            if row["seconds"] in [8, 10, 11, 12, 13, 14, 16, 20, 25, 30, 40, 50]:
                values.append((row["seconds"], round(leg, 1), round(torso, 1)))
        print(cached.stem, values)


if __name__ == "__main__":
    main()
