from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from camera_ai.recording import video_export
from camera_ai.recording.video_export import VideoExportError, prepare_telegram_video, telegram_video_metadata


def test_export_is_h264_decodable_preserves_frames_and_original_and_can_be_reused(tmp_path, monkeypatch) -> None:
    ffprobe = shutil.which("ffprobe")
    if shutil.which("ffmpeg") is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe required for real video conversion")
    source = tmp_path / "fall.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 8, (96, 64))
    assert writer.isOpened()
    frames = []
    try:
        for i in range(16):
            frame = np.full((64, 96, 3), (40, 60, 180), dtype=np.uint8)
            cv2.rectangle(frame, (i*3, 10), (i*3+12, 30), (190, 120, 20), -1)
            frames.append(frame)
            writer.write(frame)
    finally:
        writer.release()
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    exported = prepare_telegram_video(source, tmp_path / "cache")
    probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(exported)],
                           capture_output=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert "Baseline" in stream["profile"]
    assert (stream["width"], stream["height"]) == (96, 64)
    assert int(stream["nb_frames"]) == len(frames)
    assert float(stream["duration"]) == pytest.approx(2.0, abs=.01)
    assert telegram_video_metadata(exported) == {"width": 96, "height": 64, "duration": 2}
    data = exported.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
    capture = cv2.VideoCapture(str(exported))
    try:
        for expected in frames:
            ok, decoded = capture.read()
            assert ok
            assert np.abs(decoded.astype(float)-expected).mean() < 10
        assert not capture.read()[0]
    finally:
        capture.release()
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    no_conversion = Mock(side_effect=AssertionError("Cached clip was converted again"))
    monkeypatch.setattr(video_export.subprocess, "run", no_conversion)
    assert prepare_telegram_video(source, tmp_path / "cache") == exported
    no_conversion.assert_not_called()


def test_missing_ffmpeg_never_sends_original_as_compatible_video(tmp_path, monkeypatch) -> None:
    source = tmp_path / "fall.mp4"
    source.write_bytes(b"original-media")
    monkeypatch.setattr(video_export.shutil, "which", lambda _name: None)
    with pytest.raises(VideoExportError, match="FFmpeg"):
        prepare_telegram_video(source, tmp_path / "cache")
    assert source.read_bytes() == b"original-media"


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_conversion_preserves_original_and_removes_partial_output(tmp_path, monkeypatch, timeout) -> None:
    source = tmp_path / "fall.mp4"
    source.write_bytes(b"original-media")
    monkeypatch.setattr(video_export.shutil, "which", lambda _name: "ffmpeg")
    def fail(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"partial-output")
        if timeout:
            raise subprocess.TimeoutExpired(command, 180)
        return subprocess.CompletedProcess(command, 1, stderr=b"invalid input")
    monkeypatch.setattr(video_export.subprocess, "run", fail)
    with pytest.raises(VideoExportError):
        prepare_telegram_video(source, tmp_path / "cache")
    assert source.read_bytes() == b"original-media"
    assert list((tmp_path / "cache").iterdir()) == []
