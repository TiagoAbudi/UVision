from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


LOGGER = logging.getLogger("camera_ai.fall_video")


@dataclass(frozen=True, slots=True)
class BufferedFrame:
    captured_at: float
    sequence: int
    jpeg: bytes


class FallClipRecorder:
    """Keeps a compressed pre-event buffer and writes local MP4 fall clips."""

    ACTIVE_STATES = {"possible_fall", "down", "alert_pending"}

    def __init__(
        self,
        *,
        channel: int,
        frame_provider: Callable[[], tuple[bytes, int]],
        status_provider: Callable[[], dict[str, object]],
        output_dir: Path,
        fps: float = 8.0,
        pre_seconds: float = 8.0,
        post_seconds: float = 8.0,
        max_files: int = 30,
        enabled: bool = True,
        is_protected: Callable[[Path], bool] | None = None,
        on_saved: Callable[[int, Path, datetime], None] | None = None,
    ) -> None:
        self.channel = channel
        self.frame_provider = frame_provider
        self.status_provider = status_provider
        self.output_dir = output_dir
        self.fps = max(1.0, min(float(fps), 15.0))
        self.pre_seconds = max(0.0, min(float(pre_seconds), 30.0))
        self.post_seconds = max(1.0, min(float(post_seconds), 60.0))
        self.max_files = max(1, int(max_files))
        self.enabled = enabled
        self.is_protected = is_protected or (lambda path: False)
        self.on_saved = on_saved
        buffer_size = max(1, round(self.fps * self.pre_seconds))
        self._prebuffer: deque[BufferedFrame] = deque(maxlen=buffer_size)
        self._active_frames: list[BufferedFrame] | None = None
        self._event_started_at: datetime | None = None
        self._confirmed = False
        self._normal_deadline: float | None = None
        self._last_sequence = -1
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"fall-video-recorder-ch{channel:02d}", daemon=True)
        self._last_saved: Path | None = None
        self._lock = threading.Lock()

    @property
    def last_saved(self) -> Path | None:
        with self._lock:
            return self._last_saved

    def start(self) -> None:
        if not self.enabled:
            LOGGER.info("Gravação de quedas desativada")
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()
        LOGGER.info(
            "Buffer de vídeo ativo no CH %02d: %.0fs antes, %.0fs depois, %.1f FPS",
            self.channel,
            self.pre_seconds,
            self.post_seconds,
            self.fps,
        )

    def stop(self) -> None:
        if not self.enabled or not self._thread.is_alive():
            return
        self._stop.set()
        self._thread.join(timeout=max(10.0, self.post_seconds + 5.0))

    def _run(self) -> None:
        interval = 1.0 / self.fps
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                jpeg, sequence = self.frame_provider()
                if jpeg and sequence != self._last_sequence:
                    self._last_sequence = sequence
                    status = self.status_provider()
                    self._observe(
                        BufferedFrame(started, sequence, jpeg),
                        str(status.get("state", "normal")),
                    )
                self._stop.wait(max(0.01, interval - (time.monotonic() - started)))
        except Exception:
            LOGGER.exception("Falha no gravador de queda do CH %02d", self.channel)
        finally:
            if self._active_frames:
                self._finish_clip(reason="encerramento")

    def _observe(self, frame: BufferedFrame, state: str) -> None:
        self._prebuffer.append(frame)
        active_before = self._active_frames is not None
        if state in self.ACTIVE_STATES and self._active_frames is None:
            self._active_frames = list(self._prebuffer)
            self._event_started_at = datetime.now().astimezone()
            self._normal_deadline = None
            self._confirmed = False
            LOGGER.info("Armazenando suspeita em memória no CH %02d", self.channel)
        elif active_before and self._active_frames is not None:
            self._active_frames.append(frame)

        if self._active_frames is None:
            return
        if state == "alert_pending":
            self._confirmed = True
        if state in self.ACTIVE_STATES:
            self._normal_deadline = None
        elif self._normal_deadline is None:
            self._normal_deadline = frame.captured_at + self.post_seconds
        elif frame.captured_at >= self._normal_deadline:
            self._finish_clip(reason="recuperação")
            return

        if self._event_started_at and len(self._active_frames) >= round(self.fps * 120):
            self._finish_clip(reason="limite de duração")

    def _finish_clip(self, *, reason: str) -> None:
        frames = self._active_frames or []
        event_started_at = self._event_started_at or datetime.now().astimezone()
        confirmed = self._confirmed
        self._active_frames = None
        self._event_started_at = None
        self._normal_deadline = None
        self._confirmed = False
        if not confirmed:
            LOGGER.info("Suspeita descartada sem confirmação no CH %02d (%s)", self.channel, reason)
            return
        if len(frames) < 2:
            return
        path = self._write_video(frames, event_started_at)
        if path is None:
            return
        with self._lock:
            self._last_saved = path
        LOGGER.info("Vídeo de queda salvo (%s): %s", reason, path)
        if self.on_saved is not None:
            try:
                self.on_saved(self.channel, path, event_started_at)
            except Exception:
                LOGGER.exception("Não foi possível registrar a entrega do clipe do CH %02d", self.channel)
        self._prune_old_files()

    def _write_video(self, frames: list[BufferedFrame], started_at: datetime) -> Path | None:
        first = cv2.imdecode(np.frombuffer(frames[0].jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            LOGGER.warning("Primeiro quadro do clipe não pôde ser decodificado")
            return None
        height, width = first.shape[:2]
        stamp = started_at.strftime("%Y-%m-%d_%H-%M-%S")
        target = self.output_dir / f"queda-ch{self.channel:02d}_{stamp}.mp4"
        temporary = self.output_dir / f".{target.stem}.tmp.mp4"
        writer = cv2.VideoWriter(
            str(temporary),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.fps,
            (width, height),
        )
        if not writer.isOpened():
            LOGGER.error("Codec MP4 não pôde ser aberto para %s", target)
            return None
        try:
            for item in frames:
                image = cv2.imdecode(np.frombuffer(item.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                if image.shape[1] != width or image.shape[0] != height:
                    image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
                writer.write(image)
        finally:
            writer.release()
        if not temporary.exists() or temporary.stat().st_size == 0:
            temporary.unlink(missing_ok=True)
            return None
        temporary.replace(target)
        return target

    def _prune_old_files(self) -> None:
        recordings = sorted(
            self.output_dir.glob(f"queda-ch{self.channel:02d}_*.mp4"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        unprotected = [path for path in recordings if not self.is_protected(path)]
        for stale in unprotected[self.max_files :]:
            try:
                stale.unlink()
                LOGGER.info("Vídeo antigo removido pela retenção: %s", stale)
            except OSError as exc:
                LOGGER.warning("Não foi possível remover %s: %s", stale, exc)
