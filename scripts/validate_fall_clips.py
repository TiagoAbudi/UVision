"""Replay labeled CH04 clips through the current detector and confirmation rules."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import cv2

from camera_ai.detection import YoloPersonDetector
from camera_ai.detection.people_monitor import PeopleFallMonitor
from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.performance import analysis_frame, restore_coordinates
from camera_ai.web_dashboard import DVRSettings, PROJECT_DIR


def replay(path: Path, detector: YoloPersonDetector, settings: DVRSettings, *, trace: bool = False, sampling_fps: float = 4, maximum: int = 0) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Não foi possível ler {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 8.0
    step = max(1, round(fps / sampling_fps))
    frame_number = 0
    traces = []
    confirmed = False
    detected_frames = 0
    sampled_frames = 0
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fall-clip-replay-") as directory:
        monitor = PeopleFallMonitor(
            4, Path(directory), rotation_degrees=settings.fall_camera_rotation,
            ground_confirm_seconds=settings.ground_seconds(4),
            geometry=CameraGeometry.load(PROJECT_DIR / settings.fall_geometry_profile) if settings.fall_geometry_profile else None,
            zones=settings.camera_zones(4),
        )
        pending = []

        def evaluate() -> None:
            nonlocal confirmed, detected_frames, sampled_frames
            if not pending:
                return
            prepared = [analysis_frame(image, maximum) for _, image in pending]
            results = detector.detect_batch(prepared)
            for (seconds, image), analysis, people in zip(pending, prepared, results, strict=True):
                people = restore_coordinates(people, analysis, image)
                status = monitor.update(people, image.shape[1], image.shape[0], now=started + seconds)
                sampled_frames += 1
                detected_frames += bool(people)
                state = status["state"]
                if trace and people:
                    print(json.dumps({"seconds": seconds, "state": state, "features": status["features"]}), flush=True)
                if not traces or traces[-1]["state"] != state:
                    traces.append({"seconds": round(seconds, 2), "state": state})
                confirmed |= state == "alert_pending"
            pending.clear()

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_number % step == 0:
                    # Dashboard recordings are stretched to 1920x1080. CH04
                    # inference uses the native 960x1080 stream proportions.
                    pending.append((frame_number / fps, cv2.resize(frame, (960, 1080))))
                    if len(pending) == 8:
                        evaluate()
                frame_number += 1
            evaluate()
        finally:
            capture.release()
            monitor.close()
    return {
        "file": path.name, "confirmed": confirmed, "states": traces,
        "frames": sampled_frames, "personFrames": detected_frames,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fall", type=Path, action="append", default=[])
    parser.add_argument("--activity", type=Path, action="append", default=[])
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--image-size", type=int, default=None)
    parser.add_argument("--sampling-fps", type=float, default=4)
    parser.add_argument("--analysis-max-side", type=int, default=0)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    if not args.fall and not args.activity:
        parser.error("Informe pelo menos um clipe rotulado")
    settings = DVRSettings.load()
    model_path = PROJECT_DIR / settings.yolo_model
    detector = YoloPersonDetector(
        str(model_path), confidence=settings.confidence_threshold,
        image_size=args.image_size or settings.yolo_image_size, device=settings.yolo_device,
        animal_filter=settings.animal_filter_enabled,
    )
    passed = True
    results = []
    for path, expected in [(path, True) for path in args.fall] + [(path, False) for path in args.activity]:
        started = time.monotonic()
        result = replay(path, detector, settings, trace=args.trace, sampling_fps=args.sampling_fps, maximum=args.analysis_max_side)
        result["processingSeconds"] = round(time.monotonic()-started, 2)
        result["expectedConfirmed"] = expected
        result["passed"] = result["confirmed"] == expected
        passed &= result["passed"]
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"scope": "Regression on these calibration clips only; not independent accuracy validation.",
                                         "imageSize": args.image_size or settings.yolo_image_size, "samplingFps": args.sampling_fps,
                                         "analysisMaxSide": args.analysis_max_side, "passed": passed, "results": results}, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
