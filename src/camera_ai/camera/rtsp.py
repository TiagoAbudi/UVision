from __future__ import annotations

import os
import time

import cv2


class CameraReadError(RuntimeError):
    """Raised when the RTSP stream cannot be recovered."""


class RtspCamera:
    def __init__(
        self,
        url: str,
        *,
        reconnect_delay: float = 2.0,
        max_read_failures: int = 5,
        open_timeout_ms: int = 10_000,
        read_timeout_ms: int = 10_000,
    ) -> None:
        self._url = url
        self._reconnect_delay = reconnect_delay
        self._max_read_failures = max_read_failures
        self._open_timeout_ms = open_timeout_ms
        self._read_timeout_ms = read_timeout_ms
        self._capture: cv2.VideoCapture | None = None
        self._failures = 0

    def open(self) -> None:
        self.close()
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            self._open_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            self._read_timeout_ms,
        ]
        self._capture = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, params)
        if not self._capture.isOpened():
            self.close()
            raise CameraReadError("Não foi possível abrir o stream RTSP.")
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._failures = 0

    def read(self):
        if self._capture is None or not self._capture.isOpened():
            self.open()

        assert self._capture is not None
        ok, frame = self._capture.read()
        if ok and frame is not None:
            self._failures = 0
            return frame

        self._failures += 1
        if self._failures < self._max_read_failures:
            return None

        self.close()
        time.sleep(self._reconnect_delay)
        try:
            self.open()
        except CameraReadError as exc:
            raise CameraReadError("O stream RTSP caiu e a reconexão falhou.") from exc
        return None

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "RtspCamera":
        self.open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

