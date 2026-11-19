from datetime import datetime, timezone
from types import SimpleNamespace

from camera_ai.actions import NodeLightAction
from camera_ai.events import DetectionEvent


def _event(event_type: str) -> DetectionEvent:
    return DetectionEvent(
        event_type=event_type,
        occurred_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
        frame_number=3,
        person_count=1 if event_type == "PERSON_DETECTED" else 0,
        max_confidence=0.9 if event_type == "PERSON_DETECTED" else 0.0,
    )


def test_runs_idempotent_light_commands(tmp_path, monkeypatch) -> None:
    script = tmp_path / "casa.js"
    script.write_text("// test", encoding="utf-8")
    calls = []

    monkeypatch.setattr("camera_ai.actions.node_light_action.shutil.which", lambda _name: "node.exe")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="Luz ligada", stderr="")

    monkeypatch.setattr("camera_ai.actions.node_light_action.subprocess.run", fake_run)
    action = NodeLightAction(script)

    action.handle(_event("PERSON_DETECTED"), None)
    action.handle(_event("PERSON_CLEARED"), None)

    assert len(calls) == 2
    assert calls[0][0][-2:] == ["luz", "ligar"]
    assert calls[1][0][-2:] == ["luz", "desligar"]
    assert all("shell" not in kwargs for _command, kwargs in calls)

