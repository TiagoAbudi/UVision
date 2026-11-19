"""Extract reusable pose sequences and evaluate user-labeled CH04 recordings.

Cache entries are model predictions, not verified box or pose training labels.
Splitting frames from one recording into train/test would cause data leakage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import cv2

from camera_ai.detection import FallMonitor, PersonDetection, PoseKeypoint, YoloPersonDetector
from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.web_dashboard import DVRSettings, PROJECT_DIR


DATASET_DIR = PROJECT_DIR / "datasets" / "ch04-calibration"


def read_person(item: dict) -> PersonDetection:
    return PersonDetection(**{
        **item,
        "keypoints": tuple(PoseKeypoint(**point) for point in item.get("keypoints", [])),
    })


def extract(path: Path, detector: YoloPersonDetector) -> list[dict]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 8.0
    step = max(1, round(fps / 4))
    number = 0
    batch = []
    samples = []

    def process() -> None:
        if not batch:
            return
        results = detector.detect_batch([image for _, image in batch])
        for (seconds, _), people in zip(batch, results, strict=True):
            samples.append({"seconds": seconds, "people": [asdict(person) for person in people]})
        batch.clear()

    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if number % step == 0:
                batch.append((number / fps, cv2.resize(image, (960, 1080))))
                if len(batch) >= 8:
                    process()
            number += 1
        process()
    finally:
        capture.release()
    return samples


def evaluate(samples: list[dict], settings: DVRSettings, ground_seconds: float) -> dict:
    traces = []
    features = []
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ch04-calibration-") as directory:
        monitor = FallMonitor(
            4, Path(directory), rotation_degrees=settings.fall_camera_rotation,
            ground_confirm_seconds=ground_seconds,
            geometry=CameraGeometry.load(PROJECT_DIR / settings.fall_geometry_profile) if settings.fall_geometry_profile else None,
        )
        try:
            for sample in samples:
                people = [read_person(item) for item in sample["people"]]
                seconds = sample["seconds"]
                status = monitor.update(people, 960, 1080, now=start + seconds)
                state = status["state"]
                if not traces or traces[-1]["state"] != state:
                    traces.append({"seconds": seconds, "state": state})
                features.append({
                    "seconds": seconds, "personDetected": bool(people),
                    "state": state, "features": status["features"] if people else None,
                })
        finally:
            monitor.close()
    return {
        "confirmed": any(item["state"] == "alert_pending" for item in traces),
        "states": traces, "frames": len(samples), "features": features,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cached-only", action="store_true")
    parser.add_argument("--ground-seconds", type=float, default=None)
    parser.add_argument("--report", default="evaluation.json")
    parser.add_argument("--manifest", type=Path, default=DATASET_DIR / "manifest.json")
    args = parser.parse_args()
    settings = DVRSettings.load()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    code = PROJECT_DIR / "src" / "camera_ai" / "detection" / "yolo_detector.py"
    signature = {
        "codeHash": hashlib.sha256(code.read_bytes()).hexdigest(),
        "model": settings.yolo_model, "confidence": settings.confidence_threshold,
        "imageSize": settings.yolo_image_size, "animalFilter": settings.animal_filter_enabled,
        "samplingFps": 4, "sourceSize": [960, 1080],
    }
    detector = None
    results = []
    cache_dir = DATASET_DIR / "poses"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ground_seconds = settings.fall_ground_confirm_seconds if args.ground_seconds is None else args.ground_seconds
    for item in manifest["clips"]:
        path = PROJECT_DIR / "recordings" / "falls" / item["file"]
        if not path.exists():
            path = DATASET_DIR / "reference-videos" / item["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        cache_path = cache_dir / (path.stem + ".json")
        cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
        if cached and (digest is None or cached.get("sourceHash") == digest) and cached.get("signature") == signature:
            samples = cached["samples"]
        else:
            if digest is None:
                raise RuntimeError(f"Vídeo não disponível e sem cache compatível: {item['file']}")
            if args.cached_only:
                raise RuntimeError(f"Missing or stale cache: {item['file']}")
            if detector is None:
                detector = YoloPersonDetector(
                    str(PROJECT_DIR / settings.yolo_model), confidence=settings.confidence_threshold,
                    image_size=settings.yolo_image_size, device=settings.yolo_device,
                    animal_filter=settings.animal_filter_enabled,
                )
            samples = extract(path, detector)
            cache_path.write_text(json.dumps({
                "sourceHash": digest, "signature": signature, "samples": samples,
            }), encoding="utf-8")
        result = {**item, **evaluate(samples, settings, ground_seconds)}
        result["sourceVideoAvailable"] = path.exists()
        result["passed"] = result["confirmed"] == result["fall"]
        results.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "features"}), flush=True)
    summary = {
        "clips": len(results), "truePositives": sum(item["fall"] and item["confirmed"] for item in results),
        "falsePositives": sum(not item["fall"] and item["confirmed"] for item in results),
        "falseNegatives": sum(item["fall"] and not item["confirmed"] for item in results),
        "trueNegatives": sum(not item["fall"] and not item["confirmed"] for item in results),
    }
    output = {
        "scope": "Calibration on these labeled clips only; not independent validation of real-world accuracy.",
        "groundConfirmSeconds": ground_seconds, "signature": signature,
        "geometryProfile": settings.fall_geometry_profile,
        "geometryProfileHash": hashlib.sha256((PROJECT_DIR / settings.fall_geometry_profile).read_bytes()).hexdigest() if settings.fall_geometry_profile else None,
        "monitorCodeHash": hashlib.sha256((PROJECT_DIR / "src/camera_ai/detection/fall_monitor.py").read_bytes()).hexdigest(),
        "summary": summary, "results": results,
    }
    report_path = DATASET_DIR / Path(args.report).name
    report_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"summary": summary, "report": str(report_path)}), flush=True)
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
