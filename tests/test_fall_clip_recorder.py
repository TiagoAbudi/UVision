from __future__ import annotations

from camera_ai.recording.fall_clip_recorder import BufferedFrame, FallClipRecorder


def _recorder(tmp_path) -> FallClipRecorder:
    return FallClipRecorder(
        channel=4,
        frame_provider=lambda: (b"jpeg", 1),
        status_provider=lambda: {"state": "normal"},
        output_dir=tmp_path,
        fps=2,
        pre_seconds=2,
        post_seconds=2,
    )


def test_recorder_keeps_pre_event_and_waits_for_post_event(tmp_path, monkeypatch) -> None:
    recorder = _recorder(tmp_path)
    saved: list[list[BufferedFrame]] = []
    monkeypatch.setattr(recorder, "_write_video", lambda frames, _started: saved.append(frames) or tmp_path / "clip.mp4")
    monkeypatch.setattr(recorder, "_prune_old_files", lambda: None)

    recorder._observe(BufferedFrame(0.0, 1, b"1"), "normal")
    recorder._observe(BufferedFrame(0.5, 2, b"2"), "normal")
    recorder._observe(BufferedFrame(1.0, 3, b"3"), "possible_fall")
    recorder._observe(BufferedFrame(1.5, 4, b"4"), "alert_pending")
    recorder._observe(BufferedFrame(2.0, 5, b"5"), "normal")
    recorder._observe(BufferedFrame(3.5, 6, b"6"), "normal")
    assert saved == []
    recorder._observe(BufferedFrame(4.1, 7, b"7"), "normal")

    assert len(saved) == 1
    assert [frame.sequence for frame in saved[0]] == [1, 2, 3, 4, 5, 6, 7]


def test_recorder_does_not_start_for_normal_activity(tmp_path, monkeypatch) -> None:
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(recorder, "_write_video", lambda *_: (_ for _ in ()).throw(AssertionError()))
    for index in range(10):
        recorder._observe(BufferedFrame(index * 0.5, index, b"x"), "normal")
    assert recorder._active_frames is None


def test_unconfirmed_candidates_are_discarded_even_on_shutdown(tmp_path, monkeypatch) -> None:
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(recorder, "_write_video", lambda *_: (_ for _ in ()).throw(AssertionError("Unconfirmed video saved")))
    # Running: possible fall followed by normal activity.
    recorder._observe(BufferedFrame(0, 1, b"x"), "possible_fall")
    recorder._observe(BufferedFrame(0.5, 2, b"x"), "normal")
    recorder._observe(BufferedFrame(3, 3, b"x"), "normal")
    assert recorder._active_frames is None
    assert recorder.last_saved is None
    # Walking near the edge can briefly be classified DOWN, but is unconfirmed.
    recorder._observe(BufferedFrame(4, 4, b"x"), "possible_fall")
    recorder._observe(BufferedFrame(5, 5, b"x"), "down")
    recorder._finish_clip(reason="encerramento")
    assert recorder.last_saved is None
