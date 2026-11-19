from __future__ import annotations

import argparse
import logging
import sys
import time

import cv2

from camera_ai.actions import ActionDispatcher, JsonLogAction, NodeLightAction, SnapshotAction
from camera_ai.camera import CameraReadError, RtspCamera
from camera_ai.config import Settings
from camera_ai.detection import PersonDetection, YoloPersonDetector
from camera_ai.events import PersonPresenceTracker


WINDOW_NAME = "Intelbras iM1 - Person Detection"


def _draw_overlay(frame, detections: list[PersonDetection], fps: float, state: str, device: str) -> None:
    for detection in detections:
        cv2.rectangle(frame, (detection.x1, detection.y1), (detection.x2, detection.y2), (50, 220, 50), 2)
        label = f"person {detection.confidence:.0%}"
        cv2.putText(
            frame,
            label,
            (detection.x1, max(24, detection.y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (50, 220, 50),
            2,
            cv2.LINE_AA,
        )

    status = f"FPS {fps:.1f} | pessoas {len(detections)} | estado {state} | {device}"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 36), (20, 20, 20), -1)
    cv2.putText(frame, status, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)


def run(*, no_display: bool = False, max_seconds: float | None = None) -> int:
    settings = Settings.load()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)

    detector = YoloPersonDetector(
        settings.yolo_model,
        confidence=settings.confidence_threshold,
        image_size=settings.yolo_image_size,
        device=settings.yolo_device,
    )
    tracker = PersonPresenceTracker(
        confirm_frames=settings.presence_confirm_frames,
        clear_frames=settings.absence_confirm_frames,
        cooldown_seconds=settings.detection_cooldown,
    )
    dispatcher = ActionDispatcher([JsonLogAction(settings.logs_dir / "events.jsonl")])
    if settings.snapshot_on_event:
        dispatcher.register(SnapshotAction(settings.snapshots_dir))
    if settings.light_automation_enabled:
        dispatcher.register(NodeLightAction(settings.light_script, timeout=settings.light_command_timeout))

    logging.info("Modelo=%s | dispositivo=%s", settings.yolo_model, detector.device_label)
    display = settings.display_enabled and not no_display
    frame_number = 0
    smoothed_fps = 0.0

    logging.info("Abrindo stream RTSP por TCP...")

    try:
        with RtspCamera(
            settings.authenticated_camera_url(),
            reconnect_delay=settings.reconnect_delay,
            max_read_failures=settings.max_read_failures,
        ) as camera:
            first_frame = None
            while first_frame is None:
                first_frame = camera.read()

            warmup_started = time.monotonic()
            logging.info("Aquecendo o modelo com um frame real...")
            detector.warmup(first_frame)
            logging.info("Modelo pronto em %.1f s; renovando o stream ao vivo...", time.monotonic() - warmup_started)
            camera.close()
            camera.open()

            started_at = time.monotonic()
            last_frame_at = started_at
            while True:
                frame = camera.read()
                if frame is None:
                    continue

                frame_number += 1
                detections = detector.detect(frame)
                maximum = max((item.confidence for item in detections), default=0.0)
                events = tracker.update(len(detections), maximum, frame_number)
                for event in events:
                    dispatcher.dispatch(event, frame)

                now = time.monotonic()
                instantaneous = 1.0 / max(now - last_frame_at, 1e-9)
                smoothed_fps = instantaneous if smoothed_fps == 0 else (0.90 * smoothed_fps + 0.10 * instantaneous)
                last_frame_at = now

                if display:
                    _draw_overlay(frame, detections, smoothed_fps, tracker.state.value, detector.device_label)
                    cv2.imshow(WINDOW_NAME, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key in {27, ord("q")}:
                        break

                if max_seconds is not None and now - started_at >= max_seconds:
                    break
    except CameraReadError as exc:
        logging.error("Falha de câmera: %s", exc)
        return 2
    finally:
        cv2.destroyAllWindows()

    elapsed = time.monotonic() - started_at
    logging.info("Finalizado: %d frames em %.1f s (%.1f FPS médios)", frame_number, elapsed, frame_number / max(elapsed, 1e-9))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detecção local de pessoas na Intelbras iM1")
    parser.add_argument("--no-display", action="store_true", help="Executa sem abrir a janela de vídeo")
    parser.add_argument("--max-seconds", type=float, help="Encerra automaticamente após o tempo informado")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = build_parser().parse_args()
    try:
        return run(no_display=args.no_display, max_seconds=args.max_seconds)
    except (ValueError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.info("Interrompido pelo usuário.")
        return 130

