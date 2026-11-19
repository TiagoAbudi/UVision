from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, Request, build_opener, urlopen

os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0",
)

import cv2
import numpy as np
from dotenv import load_dotenv

from camera_ai.detection import FallMonitor, PersonDetection, YoloPersonDetector
from camera_ai.detection.visual_tracker import VisualPersonTracker
from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.people_monitor import PeopleFallMonitor
from camera_ai.detection.yolo_detector import GPU_BATCH_SIZE
from camera_ai.detection.zones import CameraZones
from camera_ai.camera.health import DVRRecovery, HEALTH_LABELS, probe_rtsp
from camera_ai.event_store import EventStore
from camera_ai.notifications import NotificationService
from camera_ai.performance import PerformanceProfile, analysis_frame, restore_coordinates
from camera_ai.recording import FallClipRecorder


PROJECT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_DIR.parent
WEB_DIR = PROJECT_DIR / "web"
LOGGER = logging.getLogger("camera_ai.web")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Valor booleano inválido em {name}: {value!r}")


def _fall_channels(value: str) -> tuple[int, ...] | None:
    if value.strip().lower() == "all":
        return None
    try:
        channels = tuple(int(item.strip()) for item in value.split(","))
    except ValueError:
        raise ValueError("FALL_MONITOR_CHANNELS deve ser all ou canais separados por vírgula.") from None
    if any(channel < 1 for channel in channels) or len(set(channels)) != len(channels):
        raise ValueError("FALL_MONITOR_CHANNELS deve conter canais positivos sem repetição.")
    return channels


@dataclass(frozen=True, slots=True)
class DVRSettings:
    host: str
    user: str
    password: str = field(repr=False)
    channel_count: int = 8
    extra_camera_host: str | None = None
    extra_camera_name: str = "Câmera adicional"
    yolo_model: str = "yolo26n.pt"
    yolo_device: str = "auto"
    yolo_image_size: int = 640
    animal_filter_enabled: bool = True
    confidence_threshold: float = 0.50
    detection_interval: float = 0.30
    mosaic_fps: float = 6.0
    fall_monitor_channel: int = 4
    fall_monitor_channels: tuple[int, ...] | None = None
    fall_shadow_mode: bool = True
    fall_camera_rotation: int = 0
    fall_camera_rotations: dict[int, int] = field(default_factory=dict)
    fall_ground_confirm_seconds: float = 3.0
    fall_geometry_profile: str = "calibration/ch04-geometry.json"
    fall_geometry_profiles: dict[int, str] = field(default_factory=dict)
    fall_video_enabled: bool = True
    fall_video_fps: float = 8.0
    fall_video_pre_seconds: float = 8.0
    fall_video_post_seconds: float = 8.0
    fall_video_max_files: int = 30
    bind_host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def load(cls, bind_host: str | None = None, port: int | None = None) -> "DVRSettings":
        load_dotenv(WORKSPACE_DIR / ".env", override=False)
        load_dotenv(PROJECT_DIR / ".env", override=True)
        host = os.getenv("DVR_HOST", "").strip()
        user = os.getenv("DVR_USER", "").strip()
        password = os.getenv("DVR_PASSWORD", "").strip()
        if not host or not user or not password:
            raise ValueError("Preencha DVR_HOST, DVR_USER e DVR_PASSWORD no .env da raiz.")
        channel_count = int(os.getenv("DVR_CHANNEL_COUNT", "8"))
        extra_camera_host = os.getenv("EXTRA_CAMERA_HOST", "").strip() or None
        channels = range(1, channel_count + int(bool(extra_camera_host)) + 1)
        return cls(
            host=host,
            user=user,
            password=password,
            channel_count=channel_count,
            extra_camera_host=extra_camera_host,
            extra_camera_name=os.getenv("EXTRA_CAMERA_NAME", "Câmera adicional").strip(),
            yolo_model=os.getenv("YOLO_MODEL", "yolo26n.pt").strip(),
            yolo_device=os.getenv("YOLO_DEVICE", "auto").strip(),
            yolo_image_size=int(os.getenv("YOLO_IMAGE_SIZE", "640")),
            animal_filter_enabled=_env_bool("ANIMAL_FILTER_ENABLED", True),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.50")),
            detection_interval=float(os.getenv("DVR_DETECTION_INTERVAL", "0.30")),
            mosaic_fps=float(os.getenv("DVR_MOSAIC_FPS", "6")),
            fall_monitor_channel=int(os.getenv("FALL_MONITOR_CHANNEL", "4")),
            fall_monitor_channels=_fall_channels(os.getenv("FALL_MONITOR_CHANNELS", "all")),
            fall_shadow_mode=_env_bool("FALL_SHADOW_MODE", True),
            fall_camera_rotation=int(os.getenv("FALL_CAMERA_ROTATION", "0")),
            fall_camera_rotations={
                channel: int(os.environ[f"FALL_CAMERA_ROTATION_CH{channel:02d}"])
                for channel in channels if f"FALL_CAMERA_ROTATION_CH{channel:02d}" in os.environ
            },
            fall_ground_confirm_seconds=float(os.getenv("FALL_GROUND_CONFIRM_SECONDS", "3")),
            fall_geometry_profile=os.getenv("FALL_GEOMETRY_PROFILE", "calibration/ch04-geometry.json").strip(),
            fall_geometry_profiles={
                channel: os.environ[f"FALL_GEOMETRY_PROFILE_CH{channel:02d}"].strip()
                for channel in channels if f"FALL_GEOMETRY_PROFILE_CH{channel:02d}" in os.environ
            },
            fall_video_enabled=_env_bool("FALL_VIDEO_ENABLED", True),
            fall_video_fps=float(os.getenv("FALL_VIDEO_FPS", "8")),
            fall_video_pre_seconds=float(os.getenv("FALL_VIDEO_PRE_SECONDS", "8")),
            fall_video_post_seconds=float(os.getenv("FALL_VIDEO_POST_SECONDS", "8")),
            fall_video_max_files=int(os.getenv("FALL_VIDEO_MAX_FILES", "30")),
            bind_host=bind_host or os.getenv("DVR_WEB_HOST", "127.0.0.1"),
            port=port or int(os.getenv("DVR_WEB_PORT", "8765")),
        )

    def fall_channels(self) -> tuple[int, ...]:
        available = self.channel_count + int(bool(self.extra_camera_host))
        channels = self.fall_monitor_channels
        if channels is None:
            channels = tuple(range(1, available + 1))
        if not channels or len(set(channels)) != len(channels) or any(not 1 <= channel <= available for channel in channels):
            raise ValueError("FALL_MONITOR_CHANNELS não corresponde aos canais disponíveis.")
        return channels

    def fall_rotation(self, channel: int) -> int:
        zones = self.camera_zones(channel)
        if zones is not None:
            return zones.rotation
        rotation = self.fall_camera_rotations.get(
            channel, self.fall_camera_rotation if channel == self.fall_monitor_channel else 0,
        )
        if rotation % 360 not in {0, 90, 180, 270}:
            raise ValueError(f"Rotação inválida para o CH {channel:02d}; use 0, 90, 180 ou 270 graus.")
        return rotation % 360

    def camera_zones(self, channel: int) -> CameraZones | None:
        path = PROJECT_DIR / "calibration" / f"ch{channel:02d}-zones.json"
        return CameraZones.load(path, channel) if path.exists() else None

    def ground_seconds(self, channel: int) -> float:
        zones = self.camera_zones(channel)
        return zones.ground_seconds if zones else self.fall_ground_confirm_seconds

    def fall_geometry(self, channel: int) -> CameraGeometry | None:
        profile = self.fall_geometry_profiles.get(channel)
        if profile is None:
            if channel == self.fall_monitor_channel:
                profile = self.fall_geometry_profile
            else:
                candidate = PROJECT_DIR / "calibration" / f"ch{channel:02d}-geometry.json"
                profile = str(candidate) if candidate.is_file() else ""
        geometry = CameraGeometry.load(PROJECT_DIR / profile) if profile else None
        if geometry is not None and geometry.channel != channel:
            raise ValueError(f"A calibração geométrica não corresponde ao CH {channel:02d}.")
        return geometry

    def rtsp_url(self, channel: int) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        return (
            f"rtsp://{user}:{password}@{self.host}:554/cam/realmonitor"
            f"?channel={channel}&subtype=0"
        )

    def extra_camera_rtsp_url(self) -> str | None:
        if not self.extra_camera_host:
            return None
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        return (
            f"rtsp://{user}:{password}@{self.extra_camera_host}:554/"
            f"user={user}_password={password}_channel=1_stream=0.sdp?real_stream"
        )


def discover_channel_titles(settings: DVRSettings) -> list[str]:
    base_url = f"http://{settings.host}"
    manager = HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, base_url, settings.user, settings.password)
    opener = build_opener(HTTPDigestAuthHandler(manager))
    url = f"{base_url}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
    titles = [f"Câmera {index}" for index in range(1, settings.channel_count + 1)]
    try:
        with opener.open(url, timeout=5) as response:
            body = response.read().decode("utf-8", "replace")
        for match in re.finditer(r"table\.ChannelTitle\[(\d+)]\.Name=(.*)", body):
            index = int(match.group(1))
            if 0 <= index < len(titles):
                titles[index] = match.group(2).strip() or titles[index]
    except (OSError, URLError) as exc:
        LOGGER.warning("Não foi possível consultar os nomes dos canais: %s", exc)
    return titles


def _draw_person_boxes(
    image: np.ndarray,
    detections: list[PersonDetection],
    source_width: int,
    source_height: int,
) -> None:
    if source_width <= 0 or source_height <= 0:
        return
    scale_x = image.shape[1] / source_width
    scale_y = image.shape[0] / source_height
    color = (235, 195, 120)
    thickness = 1 if image.shape[1] <= 800 else 2
    for detection in detections:
        x1 = max(0, round(detection.x1 * scale_x))
        y1 = max(0, round(detection.y1 * scale_y))
        x2 = min(image.shape[1] - 1, round(detection.x2 * scale_x))
        y2 = min(image.shape[0] - 1, round(detection.y2 * scale_y))
        if x2 <= x1 or y2 <= y1:
            continue
        corner = max(5, min(22, round(min(x2 - x1, y2 - y1) * 0.18)))
        for x, y, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(image, (x, y), (x + dx * corner, y), color, thickness, cv2.LINE_AA)
            cv2.line(image, (x, y), (x, y + dy * corner), color, thickness, cv2.LINE_AA)
        label = f"Pessoa #{detection.track_id}" if detection.track_id is not None else "Pessoa"
        font_scale = max(0.36, min(0.62, image.shape[1] / 2400))
        (label_width, label_height), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        badge_width, badge_height = label_width + 20, label_height + 12
        left = max(0, min(image.shape[1] - badge_width - 1, x1))
        top = y1 - badge_height - 5 if y1 >= badge_height + 45 else y1 + 7
        top = max(0, min(image.shape[0] - badge_height - 1, top))
        right, bottom = left + badge_width, top + badge_height
        radius = 5
        cv2.rectangle(image, (left + radius, top), (right - radius, bottom), (29, 23, 17), -1)
        cv2.rectangle(image, (left, top + radius), (right, bottom - radius), (29, 23, 17), -1)
        for center in ((left + radius, top + radius), (right - radius, top + radius), (left + radius, bottom - radius), (right - radius, bottom - radius)):
            cv2.circle(image, center, radius, (29, 23, 17), -1, cv2.LINE_AA)
        cv2.putText(
            image,
            label,
            (left + 10, bottom - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            color,
            1,
            cv2.LINE_AA,
        )


class CameraFeed:
    def __init__(self, channel: int, name: str, url: str, supports_ptz: bool = False) -> None:
        self.channel = channel
        self.name = name
        self.supports_ptz = supports_ptz
        self._url = url
        self._frame: np.ndarray | None = None
        self._frame_number = 0
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._last_frame_monotonic = 0.0
        self._last_frame_utc: str | None = None
        self._detections: list[PersonDetection] = []
        self._detection_frame_number = 0
        self._detection_version = 0
        self._empty_detection_streak = 0
        self._last_detection_monotonic = 0.0
        self._last_detection_utc: str | None = None
        self._fall_status: dict[str, object] = {"enabled": False}
        self._visual_tracker = VisualPersonTracker()
        self._detection_source_monotonic = 0.0
        self._result_age_ms = 0.0
        self._connection_state = "connecting"
        self._reconnect_count = 0
        self._stream_generation = 0
        self._analysis_times: deque[float] = deque(maxlen=120)
        self._reconnect = threading.Event()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"dvr-channel-{channel}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._reconnect.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        with self._lock:
            return self._url

    def reconnect(self, url: str | None = None) -> None:
        with self._lock:
            if url is not None:
                self._url = url
            self._connection_state = "reconnecting"
            self._last_frame_monotonic = 0.0
            self._detections = []
            self._visual_tracker = VisualPersonTracker()
        self._reconnect.set()

    def frame_snapshot(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._frame, self._frame_number

    def timed_frame_snapshot(self) -> tuple[np.ndarray | None, int, float]:
        with self._lock:
            return self._frame, self._frame_number, self._last_frame_monotonic

    def set_detections(
        self,
        detections: list[PersonDetection],
        frame_number: int,
        *,
        source_frame: np.ndarray | None = None,
        captured_at: float | None = None,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if detections:
                self._detections = detections
                self._empty_detection_streak = 0
            else:
                self._empty_detection_streak += 1
                if self._empty_detection_streak >= 2:
                    self._detections = []
            capture_time = now if captured_at is None else captured_at
            self._detection_source_monotonic = capture_time
            self._result_age_ms = max(0.0, (now - capture_time) * 1000)
            if source_frame is not None and self._frame is not None:
                self._visual_tracker.observe(
                    detections, source_frame, self._frame, self._frame_number, capture_time, now,
                )
            self._detection_frame_number = frame_number
            self._detection_version += 1
            self._last_detection_monotonic = now
            self._last_detection_utc = datetime.now(timezone.utc).isoformat()
            self._analysis_times.append(now)
            while self._analysis_times and now-self._analysis_times[0] > 10:
                self._analysis_times.popleft()

    def set_fall_status(self, status: dict[str, object]) -> None:
        with self._lock:
            self._fall_status = dict(status)

    def fall_status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._fall_status)

    def _visual_snapshot(
        self,
    ) -> tuple[np.ndarray | None, int, list[PersonDetection], int, int, bool]:
        with self._lock:
            frame = self._frame
            detections = self._visual_tracker.advance(frame, self._frame_number, time.monotonic()) if frame is not None else []
            online = bool(self._last_frame_monotonic and time.monotonic() - self._last_frame_monotonic < 5)
            return frame, self._frame_number, detections, self._width, self._height, online

    def display_snapshot(self) -> tuple[np.ndarray | None, int, list[PersonDetection], int, int, bool]:
        return self._visual_snapshot()

    def render_tile(self, width: int, height: int) -> np.ndarray:
        frame, _, detections, source_width, source_height, online = self._visual_snapshot()
        if frame is None:
            tile = np.full((height, width, 3), (14, 20, 29), dtype=np.uint8)
            cv2.putText(tile, "CONECTANDO...", (width // 2 - 88, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (165, 203, 61), 2, cv2.LINE_AA)
        else:
            tile = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            _draw_person_boxes(tile, detections, source_width, source_height)

        cv2.rectangle(tile, (0, 0), (width, 38), (9, 14, 21), -1)
        title = f"CH {self.channel:02d}  {self.name}"
        cv2.putText(tile, title, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (238, 243, 248), 2, cv2.LINE_AA)
        status_color = (165, 203, 61) if online else (114, 107, 255)
        cv2.circle(tile, (width - 18, 19), 5, status_color, -1, cv2.LINE_AA)
        if detections:
            badge = f"{len(detections)} PESSOA" if len(detections) == 1 else f"{len(detections)} PESSOAS"
            (text_width, _), _ = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
            left = width - text_width - 42
            cv2.rectangle(tile, (left - 8, height - 34), (width - 10, height - 8), (23, 79, 67), -1)
            cv2.putText(tile, badge, (left, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 255, 239), 1, cv2.LINE_AA)
        if self.supports_ptz:
            cv2.putText(tile, "PTZ", (12, height - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (165, 203, 61), 1, cv2.LINE_AA)
        self._draw_fall_badge(tile)
        return tile

    def _draw_fall_badge(self, image: np.ndarray) -> None:
        fall = self.fall_status()
        if not fall.get("enabled"):
            return
        state = str(fall.get("state", "normal"))
        labels = {
            "normal": "NORMAL",
            "possible_fall": "POSSIVEL QUEDA",
            "down": "NO CHAO",
            "alert_pending": "ALERTA PENDENTE",
        }
        features = fall.get("features") or {}
        score = float(features.get("score", 0.0)) if isinstance(features, dict) else 0.0
        colors = {
            "normal": (40, 110, 82),
            "possible_fall": (0, 150, 230),
            "down": (20, 80, 230),
            "alert_pending": (40, 40, 220),
        }
        text = f"QUEDA {labels.get(state, state.upper())} {score:.0%}"
        scale = max(0.44, image.shape[1] / 1600)
        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        x, y = 10, image.shape[0] - 10
        cv2.rectangle(
            image,
            (x - 4, y - text_height - 10),
            (x + text_width + 8, y + 4),
            colors.get(state, colors["normal"]),
            -1,
        )
        cv2.putText(image, text, (x, y - 3), cv2.FONT_HERSHEY_SIMPLEX, scale, (245, 250, 248), 1, cv2.LINE_AA)

    def _render_image_snapshot(self, annotate: bool = True) -> tuple[np.ndarray, int, bool]:
        frame, sequence, detections, source_width, source_height, online = self._visual_snapshot()
        if frame is None:
            image = cv2.imdecode(np.frombuffer(placeholder_frame(self.channel), dtype=np.uint8), cv2.IMREAD_COLOR)
            return image, sequence, online
        if source_width != 1920 or source_height != 1080:
            output = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        else:
            output = frame.copy()
        if annotate:
            _draw_person_boxes(output, detections, source_width, source_height)
            self._draw_fall_badge(output)
        return output, sequence, online

    def render_image(self, annotate: bool = True) -> np.ndarray:
        return self._render_image_snapshot(annotate)[0]

    def recording_snapshot(self) -> tuple[bytes, int]:
        output, sequence, online = self._render_image_snapshot()
        if not online:
            return b"", sequence
        encoded, buffer = cv2.imencode(".jpg", output, [cv2.IMWRITE_JPEG_QUALITY, 78])
        return (buffer.tobytes() if encoded else b""), sequence

    def render_jpeg(self, quality: int = 80, annotate: bool = True) -> bytes:
        output = self.render_image(annotate=annotate)
        encoded, buffer = cv2.imencode(".jpg", output, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buffer.tobytes() if encoded else placeholder_frame(self.channel)

    def status(self) -> dict[str, object]:
        with self._lock:
            frame_age = time.monotonic() - self._last_frame_monotonic if self._last_frame_monotonic else None
            detection_age = time.monotonic() - self._last_detection_monotonic if self._last_detection_monotonic else None
            online = frame_age is not None and frame_age < 5
            detection_fresh = detection_age is not None and detection_age < 0.75 and online
            person_count = len(self._detections) if detection_fresh else 0
            confidence = max((item.confidence for item in self._detections), default=0.0) if detection_fresh else 0.0
            fall = dict(self._fall_status)
            connection_state = "online" if online else self._connection_state
            analysis_rate = ((len(self._analysis_times)-1)/max(.01, self._analysis_times[-1]-self._analysis_times[0])
                             if online and len(self._analysis_times) > 1 and time.monotonic()-self._analysis_times[-1] < 2 else 0)
            if not online and connection_state == "online":
                connection_state = "stalled"
            return {
                "id": self.channel,
                "name": self.name,
                "online": online,
                "lastFrameAt": self._last_frame_utc,
                "width": self._width,
                "height": self._height,
                "fps": round(self._fps, 1) if online else 0.0,
                "ptz": self.supports_ptz,
                "personCount": person_count,
                "maxConfidence": round(confidence, 3),
                "detectionAt": self._last_detection_utc,
                "resultAgeMs": round(self._result_age_ms, 1),
                "connectionState": connection_state,
                "connectionMessage": HEALTH_LABELS.get(connection_state, "Verificando conexão"),
                "host": urlparse(self._url).hostname,
                "frameAgeSeconds": round(frame_age, 1) if frame_age is not None else None,
                "reconnectCount": self._reconnect_count,
                "analysisFps": round(analysis_rate, 1),
                "streamGeneration": self._stream_generation,
                "fall": fall,
            }

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            self._reconnect.clear()
            url = self.url
            # Nine independent streams must not each create a full CPU-sized
            # decoder pool and starve the pose analysis of fresh frames.
            capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000,
                cv2.CAP_PROP_N_THREADS, 2,
            ])
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not capture.isOpened():
                capture.release()
                state = probe_rtsp(url)
                with self._lock:
                    self._connection_state = state
                    self._reconnect_count += 1
                LOGGER.warning("Canal %s: %s", self.channel, HEALTH_LABELS[state])
                if state == "auth_error":
                    # Repeated bad logins can lock the DVR. Resume only on an
                    # explicit reconnect or after configuration is corrected.
                    while not self._stop.is_set() and not self._reconnect.wait(1):
                        pass
                else:
                    self._reconnect.wait(delay)
                    delay = min(30.0, delay * 2)
                continue

            LOGGER.info("Canal %s conectado (%s)", self.channel, self.name)
            with self._lock:
                self._stream_generation += 1
                self._last_frame_monotonic = 0.0
            sample_started = time.monotonic()
            sample_frames = 0
            failures = 0
            delay = 1.0
            while not self._stop.is_set() and not self._reconnect.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= 2:
                        break
                    self._stop.wait(0.03)
                    continue
                failures = 0
                now = time.monotonic()
                sample_frames += 1
                elapsed = now - sample_started
                measured_fps = None
                if elapsed >= 2:
                    measured_fps = sample_frames / elapsed
                    sample_started = now
                    sample_frames = 0
                height, width = frame.shape[:2]
                with self._lock:
                    self._connection_state = "online"
                    self._frame = frame
                    self._frame_number += 1
                    self._width = width
                    self._height = height
                    self._last_frame_monotonic = now
                    self._last_frame_utc = datetime.now(timezone.utc).isoformat()
                    if measured_fps is not None:
                        self._fps = measured_fps

            capture.release()
            if not self._stop.is_set():
                with self._lock:
                    self._connection_state = "reconnecting"
                    self._reconnect_count += 1
                LOGGER.warning("Canal %s desconectou; reconectando", self.channel)
                self._reconnect.wait(1)
        with self._lock:
            self._connection_state = "stopped"


class DetectionWorker:
    def __init__(
        self,
        feeds: list[CameraFeed],
        settings: DVRSettings,
        fall_monitors: dict[int, FallMonitor | PeopleFallMonitor] | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.feeds = feeds
        self.settings = settings
        self.fall_monitors = fall_monitors or {}
        self.notifications = notifications
        self._last_fall_frames: dict[int, int] = {}
        self._fall_generations: dict[int, int] = {}
        self.config_lock = threading.RLock()
        self.performance_path = PROJECT_DIR / "runtime" / "performance.json"
        self.performance = PerformanceProfile.load(self.performance_path, settings)
        self._analyzed_frames: dict[int, int] = {}
        self._last_feed_analysis: dict[int, int] = {}
        self._analysis_sequence = 0
        self._inference_times: deque[float] = deque(maxlen=120)
        self._last_stats_log_at = 0.0
        self._stale_fall_frames = 0
        self._last_stale_warning: dict[int, float] = {}
        self._last_slow_inference_warning = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="yolo-nine-camera-batch", daemon=True)
        self._lock = threading.Lock()
        self._status: dict[str, object] = {
            "ready": False,
            "warmingUp": True,
            "device": "Inicializando",
            "model": settings.yolo_model,
            "inferenceMs": 0.0,
            "totalPeople": 0,
            "occupied": False,
            "updatedAt": None,
            "error": None,
        }

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._status)

    def _update_status(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)

    def configure_performance(self, profile: PerformanceProfile) -> None:
        profile.save(self.performance_path)
        with self.config_lock:
            self.performance = profile

    def _update_fall(
        self, feed: CameraFeed, frame: np.ndarray, frame_number: int,
        captured_at: float, detections: list[PersonDetection],
    ) -> list[PersonDetection]:
        with self.config_lock:
            monitor = self.fall_monitors.get(feed.channel)
            if monitor is None:
                return detections
            generation = feed.status()["streamGeneration"]
            if self._fall_generations.get(feed.channel) != generation:
                if isinstance(monitor, PeopleFallMonitor):
                    monitor.reset()
                self._fall_generations[feed.channel] = generation
            height, width = frame.shape[:2]
            now = time.monotonic()
            age = max(0.0, now - captured_at)
            unique = self._last_fall_frames.get(feed.channel) != frame_number
            fresh = unique and age <= 1.0
            if unique and age > 1.0:
                self._stale_fall_frames += 1
                self._update_status(staleFallFrames=self._stale_fall_frames)
                if now - self._last_stale_warning.get(feed.channel, 0.0) >= 30:
                    LOGGER.warning("Postura no CH %02d: quadro com %.1fs de atraso descartado; aguardando evidência recente", feed.channel, age)
                    self._last_stale_warning[feed.channel] = now
            self._last_fall_frames[feed.channel] = frame_number
            status = monitor.update(detections if fresh else [], width, height)
            status.update(evidenceFresh=fresh, evidenceAgeSeconds=round(age, 2))
            feed.set_fall_status(status)
            if self.notifications is not None and fresh:
                try:
                    self.notifications.observe(feed.channel, feed.name, status, frame)
                except (OSError, ValueError):
                    self.notifications.message = "Não foi possível registrar o alerta; confira o armazenamento local."
                    LOGGER.warning("Falha ao registrar alerta do CH %02d", feed.channel)
            return monitor.detections if isinstance(monitor, PeopleFallMonitor) else detections

    def _run(self) -> None:
        try:
            model_path = Path(self.settings.yolo_model)
            if not model_path.is_absolute():
                model_path = PROJECT_DIR / model_path
            detector = YoloPersonDetector(
                str(model_path),
                confidence=self.settings.confidence_threshold,
                image_size=self.settings.yolo_image_size,
                device=self.settings.yolo_device,
                animal_filter=self.settings.animal_filter_enabled,
            )
            self._update_status(device=detector.device_label, model=model_path.name)
            LOGGER.info("YOLO %s carregado em %s; aguardando frames para aquecimento", model_path.name, detector.device_label)
            LOGGER.info("Verificação multiclasse de animais e veículos: %s", "ativa" if self.settings.animal_filter_enabled else "desativada")

            warmed_up = False
            while not self._stop.is_set():
                snapshots = [(feed, *feed.timed_frame_snapshot()) for feed in self.feeds]
                now = time.monotonic()
                ready = [(feed, frame, number, captured_at) for feed, frame, number, captured_at in snapshots
                         if frame is not None and now-captured_at <= 1 and self._analyzed_frames.get(feed.channel) != number]
                # Analyze the least recently served cameras first. Smaller
                # batches publish fresh evidence without waiting for all nine.
                ready.sort(key=lambda item: (self._last_feed_analysis.get(item[0].channel, 0),
                                             item[0].channel != self.settings.fall_monitor_channel))
                ready = ready[:GPU_BATCH_SIZE]
                for feed, frame, number, captured_at in snapshots:
                    if frame is not None and now-captured_at > 1:
                        self._update_fall(feed, frame, number, captured_at, [])
                if ready:
                    if not warmed_up:
                        detector.warmup(ready[0][1])
                        warmed_up = True
                        # Refresh the frames after CUDA initialization. The
                        # warmup image is never evidence of a fall.
                        continue
                    started = time.monotonic()
                    with self.config_lock:
                        profile = self.performance
                    detector.image_size = profile.image_size
                    prepared = [analysis_frame(frame, profile.analysis_max_side) for _, frame, _, _ in ready]
                    results = detector.detect_batch(prepared)
                    inference_ms = (time.monotonic() - started) * 1000
                    stages = getattr(detector, "last_timings", {})
                    self._update_status(**stages)
                    if inference_ms > 1000 and time.monotonic()-self._last_slow_inference_warning >= 15:
                        LOGGER.warning("Inferência lenta: pose %.0f ms; pós-processamento %.0f ms; verificação %.0f ms",
                                       stages.get("poseInferenceMs", 0), stages.get("posePostprocessMs", 0), stages.get("verificationMs", 0))
                        self._last_slow_inference_warning = time.monotonic()
                    self._inference_times.append(inference_ms)
                    for (feed, frame, frame_number, captured_at), analysis, detections in zip(ready, prepared, results, strict=True):
                        detections = restore_coordinates(detections, analysis, frame)
                        identified = self._update_fall(feed, frame, frame_number, captured_at, detections)
                        feed.set_detections(identified, frame_number, source_frame=frame, captured_at=captured_at)
                        self._analyzed_frames[feed.channel] = frame_number
                        self._analysis_sequence += 1
                        self._last_feed_analysis[feed.channel] = self._analysis_sequence
                    total_people = sum(int(feed.status()["personCount"]) for feed in self.feeds)
                    self._update_status(
                        ready=any(time.monotonic()-captured_at <= 1 for _, _, _, captured_at in ready),
                        warmingUp=not any(time.monotonic()-captured_at <= 1 for _, _, _, captured_at in ready),
                        inferenceMs=round(inference_ms, 1),
                        totalPeople=total_people,
                        occupied=total_people > 0,
                        updatedAt=datetime.now(timezone.utc).isoformat(),
                        error=None,
                        averageInferenceMs=round(sum(self._inference_times)/len(self._inference_times), 1),
                        p95InferenceMs=round(sorted(self._inference_times)[min(len(self._inference_times)-1, int(len(self._inference_times)*.95))], 1),
                        analyzedCameras=len(ready),
                        analysisImageSize=profile.image_size,
                        targetIntervalMs=round(profile.interval*1000),
                    )
                    elapsed = time.monotonic() - started
                    if time.monotonic()-self._last_stats_log_at >= 30:
                        self._last_stats_log_at = time.monotonic()
                        metrics = self.status()
                        LOGGER.info("Análise: %s câmeras no lote; média %.0f ms; p95 %.0f ms; IA %s; intervalo %.2f s",
                                    len(ready), metrics["averageInferenceMs"], metrics["p95InferenceMs"], profile.image_size, profile.interval)
                    self._stop.wait(max(0.02, profile.interval - elapsed))
                else:
                    self._stop.wait(0.2)
        except Exception as exc:
            LOGGER.exception("Falha no detector YOLO")
            self._update_status(ready=False, warmingUp=False, error=str(exc))


class MosaicComposer:
    WIDTH = 1920
    HEIGHT = 1080
    COLUMNS = 3
    ROWS = 3
    TILE_WIDTH = WIDTH // COLUMNS
    TILE_HEIGHT = HEIGHT // ROWS

    def __init__(self, feeds: list[CameraFeed], fps: float, *, encode_jpeg: bool = True) -> None:
        self.feeds = feeds
        self.encode_jpeg = encode_jpeg
        self.fps = max(1.0, fps)
        self._image: np.ndarray | None = None
        self._jpeg = placeholder_mosaic()
        self._sequence = 0
        self._render_fps = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera-mosaic-composer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def snapshot(self) -> tuple[bytes, int]:
        with self._lock:
            return self._jpeg, self._sequence

    def image_snapshot(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._image, self._sequence

    def status(self) -> dict[str, object]:
        with self._lock:
            return {"width": self.WIDTH, "height": self.HEIGHT, "fps": round(self._render_fps, 1), "sequence": self._sequence}

    def _run(self) -> None:
        interval = 1 / self.fps
        last_completed = time.monotonic()
        while not self._stop.is_set():
            started = time.monotonic()
            canvas = np.full((self.HEIGHT, self.WIDTH, 3), (8, 13, 20), dtype=np.uint8)
            for index, feed in enumerate(self.feeds[: self.COLUMNS * self.ROWS]):
                row, column = divmod(index, self.COLUMNS)
                tile = feed.render_tile(self.TILE_WIDTH, self.TILE_HEIGHT)
                y1, y2 = row * self.TILE_HEIGHT, (row + 1) * self.TILE_HEIGHT
                x1, x2 = column * self.TILE_WIDTH, (column + 1) * self.TILE_WIDTH
                canvas[y1:y2, x1:x2] = tile
                cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1), (36, 49, 64), 2)
            jpeg = None
            if self.encode_jpeg:
                encoded, buffer = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if encoded:
                    jpeg = buffer.tobytes()
            now = time.monotonic()
            measured = 1 / max(now - last_completed, 1e-9)
            last_completed = now
            with self._lock:
                self._image = canvas
                if jpeg is not None:
                    self._jpeg = jpeg
                self._sequence += 1
                self._render_fps = measured if self._render_fps == 0 else self._render_fps * 0.85 + measured * 0.15
            self._stop.wait(max(0.01, interval - (time.monotonic() - started)))


class ONVIFPTZController:
    PROFILE_TOKEN = "PROFILE_000"
    SERVICE_PATH = "/onvif/ptz_service"
    ACTIONS = {
        "left": (-0.35, 0.0),
        "right": (0.35, 0.0),
        "up": (0.0, 0.35),
        "down": (0.0, -0.35),
    }

    def __init__(self, settings: DVRSettings) -> None:
        if not settings.extra_camera_host:
            raise ValueError("A câmera PTZ não foi configurada.")
        self._url = f"http://{settings.extra_camera_host}:8899{self.SERVICE_PATH}"
        self._user = settings.user
        self._password = settings.password
        self._lock = threading.Lock()

    def control(self, action: str) -> None:
        with self._lock:
            if action in self.ACTIONS:
                pan, tilt = self.ACTIONS[action]
                body = (
                    "<tptz:ContinuousMove>"
                    f"<tptz:ProfileToken>{self.PROFILE_TOKEN}</tptz:ProfileToken>"
                    "<tptz:Velocity>"
                    f'<tt:PanTilt x="{pan}" y="{tilt}" '
                    'space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"/>'
                    "</tptz:Velocity><tptz:Timeout>PT1S</tptz:Timeout>"
                    "</tptz:ContinuousMove>"
                )
                self._post(body, "ContinuousMove")
                return
            if action == "stop":
                body = (
                    "<tptz:Stop>"
                    f"<tptz:ProfileToken>{self.PROFILE_TOKEN}</tptz:ProfileToken>"
                    "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>"
                    "</tptz:Stop>"
                )
                self._post(body, "Stop")
                return
            if action == "home":
                body = (
                    "<tptz:GotoHomePosition>"
                    f"<tptz:ProfileToken>{self.PROFILE_TOKEN}</tptz:ProfileToken>"
                    "</tptz:GotoHomePosition>"
                )
                self._post(body, "GotoHomePosition")
                return
            raise ValueError("Comando PTZ inválido.")

    def _security_header(self) -> str:
        nonce = os.urandom(16)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + self._password.encode()).digest()
        ).decode()
        encoded_nonce = base64.b64encode(nonce).decode()
        user = xml_escape(self._user)
        return (
            '<wsse:Security s:mustUnderstand="1" '
            'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
            'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{user}</wsse:Username>"
            '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{encoded_nonce}</wsse:Nonce><wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken></wsse:Security>"
        )

    def _post(self, body: str, operation: str) -> None:
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" '
            'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f"<s:Header>{self._security_header()}</s:Header><s:Body>{body}</s:Body>"
            "</s:Envelope>"
        ).encode()
        action = f"http://www.onvif.org/ver20/ptz/wsdl/{operation}"
        request = Request(
            self._url,
            data=envelope,
            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
        )
        with urlopen(request, timeout=4) as response:
            if response.status != HTTPStatus.OK:
                raise OSError(f"ONVIF retornou HTTP {response.status}")


class DashboardState:
    def __init__(self, settings: DVRSettings, *, native: bool = False, compose_mosaic: bool = True, event_store: EventStore | None = None) -> None:
        self.settings = settings
        self.event_store = event_store or EventStore(PROJECT_DIR / "runtime" / "events.json")
        self.notifications = NotificationService(self.event_store, PROJECT_DIR, WORKSPACE_DIR / ".env")
        self._compose_mosaic = compose_mosaic
        titles = discover_channel_titles(settings)
        self.feeds = [
            CameraFeed(channel, titles[channel - 1], settings.rtsp_url(channel))
            for channel in range(1, settings.channel_count + 1)
        ]
        self.ptz_controller: ONVIFPTZController | None = None
        extra_url = settings.extra_camera_rtsp_url()
        if extra_url:
            self.feeds.append(
                CameraFeed(len(self.feeds) + 1, settings.extra_camera_name, extra_url, supports_ptz=True)
            )
            self.ptz_controller = ONVIFPTZController(settings)
        channels = settings.fall_channels()
        # Validate every profile before opening any monitor logs.
        profiles = {channel: (settings.fall_rotation(channel), settings.fall_geometry(channel)) for channel in channels}
        self.fall_monitors: dict[int, FallMonitor | PeopleFallMonitor] = {}
        self.fall_recorders: dict[int, FallClipRecorder] = {}
        for channel, (rotation, geometry) in profiles.items():
            monitor = PeopleFallMonitor(
                channel,
                PROJECT_DIR / "logs",
                shadow_mode=settings.fall_shadow_mode,
                rotation_degrees=rotation,
                ground_confirm_seconds=settings.ground_seconds(channel),
                geometry=geometry,
                zones=settings.camera_zones(channel),
            )
            self.fall_monitors[channel] = monitor
            fall_feed = self.feeds[channel - 1]
            fall_feed.set_fall_status(monitor.status())
            self.fall_recorders[channel] = FallClipRecorder(
                channel=channel,
                frame_provider=fall_feed.recording_snapshot,
                status_provider=fall_feed.fall_status,
                output_dir=PROJECT_DIR / "recordings" / "falls",
                fps=settings.fall_video_fps,
                pre_seconds=settings.fall_video_pre_seconds,
                post_seconds=settings.fall_video_post_seconds,
                max_files=settings.fall_video_max_files,
                enabled=settings.fall_video_enabled,
                is_protected=self.event_store.is_protected,
                on_saved=self.notifications.clip_ready,
            )
        self.fall_monitor = self.fall_monitors.get(settings.fall_monitor_channel)
        self.fall_recorder = self.fall_recorders.get(settings.fall_monitor_channel)
        self.detection_worker = DetectionWorker(
            self.feeds,
            settings,
            fall_monitors=self.fall_monitors,
            notifications=self.notifications,
        )
        self.mosaic = MosaicComposer(self.feeds, settings.mosaic_fps, encode_jpeg=not native)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.recovery = DVRRecovery(settings, self.feeds, PROJECT_DIR / "runtime" / "dvr-identity.json")

    def start(self) -> None:
        self.recovery.start()
        self.notifications.start()
        for feed in self.feeds:
            feed.start()
        if self._compose_mosaic:
            self.mosaic.start()
        self.detection_worker.start()
        for recorder in self.fall_recorders.values():
            recorder.start()

    def stop(self) -> None:
        self.recovery.stop()
        self.detection_worker.stop()
        for recorder in self.fall_recorders.values():
            recorder.stop()
        self.notifications.stop()
        if self._compose_mosaic:
            self.mosaic.stop()
        for feed in self.feeds:
            feed.stop()
        for monitor in self.fall_monitors.values():
            monitor.close()

    def feed(self, channel: int) -> CameraFeed | None:
        if 1 <= channel <= len(self.feeds):
            return self.feeds[channel - 1]
        return None

    def reload_calibration(self, channel: int) -> None:
        if channel not in self.fall_monitors:
            raise ValueError("Este canal não está habilitado para análise de quedas.")
        replacement = PeopleFallMonitor(
            channel, PROJECT_DIR / "logs", shadow_mode=self.settings.fall_shadow_mode,
            rotation_degrees=self.settings.fall_rotation(channel),
            ground_confirm_seconds=self.settings.ground_seconds(channel),
            geometry=self.settings.fall_geometry(channel), zones=self.settings.camera_zones(channel),
        )
        with self.detection_worker.config_lock:
            previous = self.fall_monitors[channel]
            self.fall_monitors[channel] = replacement
            self.feeds[channel-1].set_fall_status(replacement.status())
            previous.close()

    def invalidate_view(self, channel: int) -> None:
        zones = self.settings.camera_zones(channel)
        if zones and zones.view_changed:
            return
        path = PROJECT_DIR / "calibration" / f"ch{channel:02d}-zones.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "channel": channel, "coordinateSystem": "raw_image_normalized_xy",
            "rotationDegrees": self.settings.fall_rotation(channel), "groundConfirmSeconds": self.settings.ground_seconds(channel),
        }
        data["viewChanged"] = True
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.reload_calibration(channel)


def placeholder_frame(channel: int) -> bytes:
    image = np.full((240, 352, 3), (14, 20, 29), dtype=np.uint8)
    cv2.putText(image, f"CANAL {channel}", (118, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (191, 203, 218), 1, cv2.LINE_AA)
    cv2.putText(image, "CONECTANDO...", (91, 139), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (61, 203, 165), 2, cv2.LINE_AA)
    _, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buffer.tobytes()


def placeholder_mosaic() -> bytes:
    image = np.full((MosaicComposer.HEIGHT, MosaicComposer.WIDTH, 3), (8, 13, 20), dtype=np.uint8)
    cv2.putText(image, "CONECTANDO AS 9 CAMERAS...", (650, 535), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (165, 203, 61), 3, cv2.LINE_AA)
    _, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buffer.tobytes()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "UStudioDVR/1.0"
    static_files = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
        "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    }

    @property
    def state(self) -> DashboardState:
        return self.server.dashboard_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in self.static_files:
            filename, content_type = self.static_files[path]
            self._send_bytes((WEB_DIR / filename).read_bytes(), content_type, cache="no-store")
            return
        if path == "/api/health":
            online = sum(1 for feed in self.state.feeds if feed.status()["online"])
            self._send_json(
                {
                    "ok": True,
                    "online": online,
                    "total": len(self.state.feeds),
                    "startedAt": self.state.started_at,
                    "ai": self.state.detection_worker.status(),
                    "mosaic": self.state.mosaic.status(),
                }
            )
            return
        if path == "/api/cameras":
            self._send_json(
                {
                    "cameras": [feed.status() for feed in self.state.feeds],
                    "ai": self.state.detection_worker.status(),
                    "mosaic": self.state.mosaic.status(),
                }
            )
            return
        if path == "/api/mosaic.jpg":
            frame, sequence = self.state.mosaic.snapshot()
            self._send_bytes(
                frame,
                "image/jpeg",
                cache="no-store",
                extra_headers={"X-Mosaic-Sequence": str(sequence)},
            )
            return
        if path == "/api/mosaic.mjpg":
            self._send_mjpeg(self.state.mosaic.snapshot, max_fps=self.state.settings.mosaic_fps)
            return
        stream_match = re.fullmatch(r"/api/cameras/(\d+)/stream\.mjpg", path)
        if stream_match:
            channel = int(stream_match.group(1))
            feed = self.state.feed(channel)
            if feed is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_mjpeg(
                lambda: (feed.render_jpeg(), feed.frame_snapshot()[1]),
                max_fps=10,
            )
            return
        match = re.fullmatch(r"/api/cameras/(\d+)/frame\.jpg", path)
        if match:
            channel = int(match.group(1))
            feed = self.state.feed(channel)
            if feed is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(feed.render_jpeg(), "image/jpeg", cache="no-store")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/cameras/(\d+)/ptz", path)
        if not match:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin.rstrip("/") != f"http://{host}".rstrip("/"):
            self._send_json_error(HTTPStatus.FORBIDDEN, "Origem não permitida.")
            return
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            self._send_json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Envie application/json.")
            return
        channel = int(match.group(1))
        feed = self.state.feed(channel)
        if feed is None or not feed.supports_ptz or self.state.ptz_controller is None:
            self._send_json_error(HTTPStatus.NOT_FOUND, "Este canal não possui PTZ.")
            return
        try:
            content_length = min(int(self.headers.get("Content-Length", "0")), 1024)
            payload = json.loads(self.rfile.read(content_length))
            action = payload.get("action") if isinstance(payload, dict) else None
            if action not in {*ONVIFPTZController.ACTIONS, "stop", "home"}:
                raise ValueError("Comando PTZ inválido.")
            self.state.ptz_controller.control(action)
            self._send_json({"ok": True, "action": action})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json_error(HTTPStatus.BAD_REQUEST, str(exc))
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            LOGGER.warning("Falha no PTZ: %s", exc)
            self._send_json_error(HTTPStatus.BAD_GATEWAY, "A câmera não respondeu ao comando PTZ.")

    def _send_json(self, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", cache="no-store")

    def _send_json_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_mjpeg(self, provider, max_fps: float) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        interval = 1 / max(1.0, max_fps)
        last_sequence = -1
        next_frame_at = 0.0
        try:
            while True:
                now = time.monotonic()
                if now < next_frame_at:
                    time.sleep(min(0.02, next_frame_at - now))
                    continue
                frame, sequence = provider()
                if sequence == last_sequence:
                    time.sleep(0.01)
                    continue
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                )
                self.wfile.write(header)
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                last_sequence = sequence
                next_frame_at = time.monotonic() + interval
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        cache: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: DashboardState) -> None:
        super().__init__(address, DashboardHandler)
        self.dashboard_state = state


def configure_logging() -> None:
    logs_dir = PROJECT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "web-dashboard.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", handlers=handlers)


def existing_dashboard(url: str) -> bool:
    try:
        with urlopen(f"{url}/api/health", timeout=1) as response:
            data = json.loads(response.read())
            return response.status == 200 and data.get("ok") is True
    except (OSError, URLError, ValueError):
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Painel web local do DVR Intelbras")
    parser.add_argument("--host", help="Endereço de escuta; padrão: 127.0.0.1")
    parser.add_argument("--port", type=int, help="Porta HTTP; padrão: 8765")
    parser.add_argument("--open-browser", action="store_true", help="Abre o painel no navegador")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        settings = DVRSettings.load(bind_host=args.host, port=args.port)
    except (ValueError, TypeError) as exc:
        LOGGER.error("Configuração inválida: %s", exc)
        return 2

    local_url = f"http://127.0.0.1:{settings.port}"
    if existing_dashboard(local_url):
        LOGGER.info("O painel já está em execução em %s", local_url)
        if args.open_browser:
            webbrowser.open(local_url)
        return 0

    state = DashboardState(settings)
    try:
        server = DashboardServer((settings.bind_host, settings.port), state)
    except OSError as exc:
        LOGGER.error("Não foi possível abrir %s:%s: %s", settings.bind_host, settings.port, exc)
        return 3

    state.start()
    LOGGER.info("Painel disponível em %s", local_url)
    if args.open_browser:
        threading.Timer(0.8, webbrowser.open, args=(local_url,)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        LOGGER.info("Encerrando painel")
    finally:
        server.shutdown()
        server.server_close()
        state.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
