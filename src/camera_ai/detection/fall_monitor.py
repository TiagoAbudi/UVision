from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path

from camera_ai.detection.yolo_detector import PersonDetection, PoseKeypoint
from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.zones import CameraZones


class FallState(str, Enum):
    NORMAL = "normal"
    POSSIBLE_FALL = "possible_fall"
    DOWN = "down"
    ALERT_PENDING = "alert_pending"


@dataclass(frozen=True, slots=True)
class FallFeatures:
    score: float
    bbox_aspect: float
    torso_angle: float
    center_speed: float
    posture_change: float
    keypoints: int
    confidence: float
    geometry_ground: bool | None = None
    leg_vertical_deviation: float | None = None
    torso_vertical_deviation: float | None = None
    zone_eligible: bool = True


class FallMonitor:
    """Temporal fall candidate monitor intended for calibration before alerting."""

    def __init__(
        self,
        channel: int,
        logs_dir: Path,
        *,
        shadow_mode: bool = True,
        rotation_degrees: int = 0,
        ground_confirm_seconds: float = 3.0,
        geometry: CameraGeometry | None = None,
        track_id: int | None = None,
        shared_loggers: tuple[logging.Logger, logging.Logger] | None = None,
        zones: CameraZones | None = None,
    ) -> None:
        self.channel = channel
        self.track_id = track_id
        self.zones = zones
        self._owns_loggers = shared_loggers is None
        self.shadow_mode = shadow_mode
        if not math.isfinite(ground_confirm_seconds) or ground_confirm_seconds < 1.0:
            raise ValueError("FALL_GROUND_CONFIRM_SECONDS deve ser pelo menos 1 segundo.")
        self.ground_confirm_seconds = ground_confirm_seconds
        if geometry is not None and geometry.channel != channel:
            raise ValueError("A calibração geométrica não corresponde ao canal monitorado")
        self.geometry = geometry
        normalized_rotation = rotation_degrees % 360
        if normalized_rotation not in {0, 90, 180, 270}:
            raise ValueError("A rotação da câmera deve ser 0, 90, 180 ou 270 graus.")
        self.rotation_degrees = normalized_rotation
        self._state = FallState.NORMAL
        self._state_since = time.monotonic()
        self._last_person_at = 0.0
        self._last_sample_at = 0.0
        self._last_center: tuple[float, float] | None = None
        self._last_aspect: float | None = None
        self._last_torso_angle: float | None = None
        self._features: FallFeatures | None = None
        self._last_log_at = 0.0
        self._down_since: float | None = None
        self._still_since: float | None = None
        self._last_evidence_at: float | None = None
        self._last_reliable_down_at: float | None = None
        self._lock = threading.Lock()
        logs_dir.mkdir(parents=True, exist_ok=True)
        if shared_loggers is not None:
            self._sample_logger, self._event_logger = shared_loggers
        else:
            self._sample_logger = self._build_logger(
                f"camera_ai.fall.samples.{channel}", logs_dir / f"fall-calibration-ch{channel:02d}.jsonl"
            )
            self._event_logger = self._build_logger(
                f"camera_ai.fall.events.{channel}", logs_dir / f"fall-events-ch{channel:02d}.jsonl"
            )

    @staticmethod
    def _build_logger(name: str, path: Path) -> logging.Logger:
        logger = logging.Logger(name, logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            path,
            maxBytes=10_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        return logger

    def close(self) -> None:
        if not self._owns_loggers:
            return
        for logger in (self._sample_logger, self._event_logger):
            for handler in list(logger.handlers):
                handler.flush()
                handler.close()
                logger.removeHandler(handler)

    def update(
        self,
        detections: list[PersonDetection],
        frame_width: int,
        frame_height: int,
        *,
        now: float | None = None,
    ) -> dict[str, object]:
        current = time.monotonic() if now is None else now
        detection = max(
            detections,
            key=lambda item: (item.x2 - item.x1) * (item.y2 - item.y1),
            default=None,
        )
        with self._lock:
            if detection is None or frame_width <= 0 or frame_height <= 0:
                self._handle_missing(current)
            else:
                self._last_person_at = current
                features = self._extract_features(detection, frame_width, frame_height, current)
                self._features = features
                self._advance(features, current)
                self._write_sample(current, person_detected=True)
            return self._status_unlocked(current)

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status_unlocked(time.monotonic())

    def _extract_features(
        self,
        detection: PersonDetection,
        frame_width: int,
        frame_height: int,
        now: float,
    ) -> FallFeatures:
        box_width = max(1.0, detection.x2 - detection.x1)
        box_height = max(1.0, detection.y2 - detection.y1)
        raw_center = (
            ((detection.x1 + detection.x2) / 2) / frame_width,
            ((detection.y1 + detection.y2) / 2) / frame_height,
        )
        if self.rotation_degrees in {90, 270}:
            aspect = box_height / box_width
            center = (raw_center[1], raw_center[0])
        else:
            aspect = box_width / box_height
            center = raw_center
        elapsed = max(0.05, now - self._last_sample_at) if self._last_sample_at else 1.0
        center_speed = 0.0
        if self._last_center is not None:
            center_speed = math.dist(center, self._last_center) / elapsed

        torso_angle = self._torso_angle(
            detection.keypoints,
            frame_width,
            frame_height,
            self.rotation_degrees,
        )
        aspect_change = abs(aspect - self._last_aspect) if self._last_aspect is not None else 0.0
        angle_change = (
            abs(torso_angle - self._last_torso_angle) / 90
            if self._last_torso_angle is not None
            else 0.0
        )
        posture_change = min(1.0, aspect_change / 0.7 + angle_change)

        horizontal_score = self._scale(aspect, 0.8, 1.5)
        torso_score = self._scale(torso_angle, 35.0, 75.0)
        motion_score = self._scale(center_speed, 0.08, 0.45)
        score = min(
            1.0,
            0.30 * horizontal_score
            + 0.25 * torso_score
            + 0.30 * motion_score
            + 0.15 * posture_change,
        )

        self._last_center = center
        self._last_aspect = aspect
        self._last_torso_angle = torso_angle
        self._last_sample_at = now
        geometry_ground, leg_deviation, torso_deviation = (
            self.geometry.evaluate(detection.keypoints, frame_width, frame_height)
            if self.geometry is not None else (None, None, None)
        )
        return FallFeatures(
            score=round(score, 3),
            bbox_aspect=round(aspect, 3),
            torso_angle=round(torso_angle, 1),
            center_speed=round(center_speed, 3),
            posture_change=round(posture_change, 3),
            keypoints=sum(point.confidence >= 0.30 for point in detection.keypoints),
            confidence=round(detection.confidence, 3),
            geometry_ground=geometry_ground,
            leg_vertical_deviation=leg_deviation,
            torso_vertical_deviation=torso_deviation,
            zone_eligible=self.zones.allows(detection, frame_width, frame_height) if self.zones else True,
        )

    @staticmethod
    def _scale(value: float, lower: float, upper: float) -> float:
        return min(1.0, max(0.0, (value - lower) / max(upper - lower, 1e-9)))

    @staticmethod
    def _midpoint(
        points: tuple[PoseKeypoint, ...],
        first: int,
        second: int,
    ) -> tuple[float, float] | None:
        available = [
            points[index]
            for index in (first, second)
            if index < len(points) and points[index].confidence >= 0.30
        ]
        if not available:
            return None
        return (
            sum(point.x for point in available) / len(available),
            sum(point.y for point in available) / len(available),
        )

    @classmethod
    def _torso_angle(
        cls,
        points: tuple[PoseKeypoint, ...],
        frame_width: int,
        frame_height: int,
        rotation_degrees: int = 0,
    ) -> float:
        shoulders = cls._midpoint(points, 5, 6)
        hips = cls._midpoint(points, 11, 12)
        if shoulders is None or hips is None:
            return 0.0
        dx = abs(hips[0] - shoulders[0]) / frame_width
        dy = abs(hips[1] - shoulders[1]) / frame_height
        if rotation_degrees in {90, 270}:
            dx, dy = dy, dx
        return math.degrees(math.atan2(dx, max(dy, 1e-6)))

    def _advance(self, features: FallFeatures, now: float) -> None:
        # Calibrated on CH04: a wide box alone also describes someone standing
        # near the fridge under this overhead camera. Require both shape and
        # torso inclination, rather than either one independently.
        down_posture = features.bbox_aspect >= 1.25 and features.torso_angle >= 65
        rapid_change = features.center_speed >= 0.12 or features.posture_change >= 0.38
        possible = features.score >= 0.52 and rapid_change and (
            features.bbox_aspect >= 0.90 or features.torso_angle >= 55
        )

        # A transit through the doorway can distort the box and torso for one
        # frame. Require fresh, repeated pose evidence before reporting DOWN.
        if self._last_evidence_at is not None and now - self._last_evidence_at > 1.0:
            self._down_since = None
            self._still_since = None
        self._last_evidence_at = now
        reliable_down = (
            down_posture
            and features.confidence >= 0.50
            and features.keypoints >= 9
            and features.center_speed <= 0.20
            and (self.geometry is None or features.geometry_ground is True)
            and features.zone_eligible
        )
        # After a capture/inference gap, a new track may already be lying
        # motionless. Acquire fresh evidence instead of requiring it to fall
        # again. Both body axes must strongly contradict standing geometry;
        # a wide box or a static, mildly bent pose is insufficient.
        stationary_ground = (
            reliable_down and self.geometry is not None
            and features.bbox_aspect >= 1.75
            and features.confidence >= 0.70 and features.keypoints >= 12
            and features.center_speed <= 0.08
            and features.leg_vertical_deviation is not None
            and features.leg_vertical_deviation >= max(45.0, self.geometry.strong_leg_deviation)
            and features.torso_vertical_deviation is not None
            and features.torso_vertical_deviation >= max(45.0, self.geometry.min_torso_deviation)
        )
        if reliable_down:
            self._last_reliable_down_at = now
            if self._down_since is None:
                self._down_since = now
        else:
            self._down_since = None
        if reliable_down and features.center_speed <= 0.12:
            if self._still_since is None:
                self._still_since = now
        else:
            self._still_since = None

        if self._state is FallState.NORMAL:
            if possible:
                self._transition(FallState.POSSIBLE_FALL, now, features)
                self._down_since = now if reliable_down else None
                self._still_since = now if reliable_down and features.center_speed <= 0.12 else None
                self._last_reliable_down_at = now if reliable_down else None
            elif stationary_ground:
                if self._down_since is not None and now - self._down_since >= 0.75:
                    self._transition(FallState.POSSIBLE_FALL, now, features)
            else:
                self._down_since = None
                self._still_since = None
                self._last_reliable_down_at = None
            return
        elapsed = now - self._state_since
        if self._state is FallState.POSSIBLE_FALL:
            if self._down_since is not None and now - self._down_since >= 0.75:
                self._transition(FallState.DOWN, now, features)
            elif elapsed >= 3.0 or (features.score < 0.28 and elapsed >= 1.0):
                self._transition(FallState.NORMAL, now, features)
            return
        if self._state is FallState.DOWN:
            if (
                self._down_since is not None
                and now - self._down_since >= self.ground_confirm_seconds
                and self._still_since is not None
                and now - self._still_since >= 2.0
            ):
                self._transition(FallState.ALERT_PENDING, now, features)
            elif self._last_reliable_down_at is not None and now - self._last_reliable_down_at >= 1.0:
                self._transition(FallState.NORMAL, now, features)
            return
        if self._state is FallState.ALERT_PENDING:
            upright_geometry = self.geometry is not None and features.geometry_ground is False
            if (upright_geometry or (not down_posture and features.score < 0.35)) and elapsed >= 2.0:
                self._transition(FallState.NORMAL, now, features)

    def _handle_missing(self, now: float) -> None:
        self._down_since = None
        self._still_since = None
        self._last_evidence_at = None
        self._write_sample(now, person_detected=False)
        if self._last_person_at and now - self._last_person_at > 2.0:
            self._last_center = None
            self._last_aspect = None
            self._last_torso_angle = None
            self._last_sample_at = 0.0
            if self._state is not FallState.NORMAL:
                self._transition(FallState.NORMAL, now, self._features)
            self._features = None
            self._last_reliable_down_at = None

    def _transition(
        self,
        target: FallState,
        now: float,
        features: FallFeatures | None,
    ) -> None:
        previous = self._state
        if previous is target:
            return
        self._state = target
        self._state_since = now
        if target is FallState.NORMAL:
            self._down_since = None
            self._still_since = None
            self._last_reliable_down_at = None
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": self.channel,
            "rotationDegrees": self.rotation_degrees,
            "from": previous.value,
            "trackId": self.track_id,
            "to": target.value,
            "shadowMode": self.shadow_mode,
            "features": asdict(features) if features else None,
        }
        self._event_logger.info(json.dumps(payload, ensure_ascii=False))

    def _write_sample(self, now: float, *, person_detected: bool) -> None:
        if now - self._last_log_at < 0.45:
            return
        self._last_log_at = now
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": self.channel,
            "rotationDegrees": self.rotation_degrees,
            "state": self._state.value,
            "shadowMode": self.shadow_mode,
            "personDetected": person_detected,
            "trackId": self.track_id,
            "features": asdict(self._features) if self._features else None,
        }
        self._sample_logger.info(json.dumps(payload, ensure_ascii=False))

    def _status_unlocked(self, now: float) -> dict[str, object]:
        features = asdict(self._features) if self._features else None
        return {
            "enabled": True,
            "trackId": self.track_id,
            "groundConfirmSeconds": self.ground_confirm_seconds,
            "perspectiveGuard": self.geometry is not None and not (self.zones and self.zones.view_changed),
            "calibrationRequired": bool(self.zones and self.zones.view_changed),
            "zonesGuard": self.zones is not None and bool(self.zones.floors or self.zones.rest),
            "channel": self.channel,
            "rotationDegrees": self.rotation_degrees,
            "state": self._state.value,
            "stateSeconds": round(now - self._state_since, 1),
            "shadowMode": self.shadow_mode,
            "features": features,
        }
