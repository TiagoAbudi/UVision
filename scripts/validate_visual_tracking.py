"""Compare a held YOLO box to frame-rate optical flow on a saved clip.

Reference is fresh YOLO inference, not manually annotated ground truth. The
simulated detection schedule is 4 Hz with 125 ms latency on an 8 FPS clip.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from camera_ai.detection import YoloPersonDetector
from camera_ai.detection.visual_tracker import VisualPersonTracker
from camera_ai.web_dashboard import DVRSettings, PROJECT_DIR


def center(box) -> np.ndarray:
    return np.array([(box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip", type=Path)
    args = parser.parse_args()
    settings = DVRSettings.load()
    detector = YoloPersonDetector(
        str(PROJECT_DIR / settings.yolo_model), confidence=settings.confidence_threshold,
        image_size=settings.yolo_image_size, device=settings.yolo_device,
        animal_filter=settings.animal_filter_enabled,
    )
    capture = cv2.VideoCapture(str(args.clip))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open {args.clip}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 8.0
    detections = []
    batch = []
    while True:
        ok, image = capture.read()
        if not ok:
            break
        batch.append(cv2.resize(image, (960, 1080)))
        if len(batch) == 8:
            detections.extend(detector.detect_batch(batch))
            batch.clear()
    if batch:
        detections.extend(detector.detect_batch(batch))
    capture.release()
    tracker = VisualPersonTracker()
    capture = cv2.VideoCapture(str(args.clip))
    previous = deque(maxlen=2)
    held = []
    misses = 0
    errors_before, errors_after, tracker_ms = [], [], []
    comparisons = []
    for index, reference in enumerate(detections):
        ok, frame = capture.read()
        if not ok:
            break
        frame = cv2.resize(frame, (960, 1080))
        now = 1000.0 + index / fps
        started = time.perf_counter()
        tracker.advance(frame, index, now)
        # A detection for an even source frame becomes available one frame later.
        if index % 2 == 1 and previous:
            source_index, source = previous[-1]
            observed = detections[source_index]
            if observed:
                held, misses = observed, 0
            else:
                misses += 1
                if misses >= 3:
                    held = []
            tracker.observe(observed, source, frame, index, 1000.0 + source_index / fps, now)
        predicted = tracker.advance(frame, index, now)
        tracker_ms.append((time.perf_counter() - started) * 1000)
        previous.append((index, frame))
        if reference and held and predicted:
            target = max(reference, key=lambda item: (item.x2 - item.x1) * (item.y2 - item.y1))
            error_before = min(float(np.linalg.norm(center(box) - center(target))) for box in held)
            error_after = min(float(np.linalg.norm(center(box) - center(target))) for box in predicted)
            errors_before.append(error_before)
            errors_after.append(error_after)
            comparisons.append({"seconds": round(index / fps, 3), "beforePx": round(error_before, 2), "afterPx": round(error_after, 2)})
    capture.release()
    result = {
        "clip": args.clip.name,
        "reference": "Fresh YOLO boxes, not manual ground truth; shared frames with both overlays visible.",
        "simulatedInferenceHz": fps / 2,
        "simulatedLatencyMs": 1000 / fps,
        "framesCompared": len(errors_before),
        "meanCenterErrorBeforePx": round(float(np.mean(errors_before)), 2) if errors_before else None,
        "meanCenterErrorAfterPx": round(float(np.mean(errors_after)), 2) if errors_after else None,
        "meanTrackingMs": round(float(np.mean(tracker_ms)), 2),
        "p95TrackingMs": round(float(np.percentile(tracker_ms, 95)), 2),
        "comparisons": comparisons,
    }
    output = PROJECT_DIR / "logs" / "visual-tracking-validation.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "comparisons"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
