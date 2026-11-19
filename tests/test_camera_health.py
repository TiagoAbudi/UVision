from __future__ import annotations

import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

from camera_ai.camera import health
from camera_ai.web_dashboard import CameraFeed


def test_health_distinguishes_never_connected_stale_and_live() -> None:
    feed = CameraFeed(1, "Entrada", "rtsp://test:private@192.168.1.2:554/live")
    assert feed.status()["connectionState"] == "connecting"
    feed._connection_state = "online"
    feed._last_frame_monotonic = time.monotonic() - 10
    assert feed.status()["connectionState"] == "stalled"
    feed._last_frame_monotonic = time.monotonic()
    assert feed.status()["connectionState"] == "online"
    assert "private" not in str(feed.status())


def test_unreachable_rtsp_is_network_error(monkeypatch) -> None:
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=OSError))
    assert health.probe_rtsp("rtsp://test:private@192.168.1.2:554/live") == "network_error"


def test_rejected_rtsp_credentials_are_distinguished(monkeypatch) -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.recv.side_effect = [
        b'RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: Digest realm="cam", nonce="one"\r\n\r\n',
        b'RTSP/1.0 401 Unauthorized\r\n\r\n',
    ]
    monkeypatch.setattr(socket, "create_connection", Mock(return_value=connection))
    assert health.probe_rtsp("rtsp://test:private@192.168.1.2:554/live") == "auth_error"


def test_recovery_changes_only_host_and_invalidates_old_frames(tmp_path) -> None:
    feed = CameraFeed(1, "Entrada", "rtsp://test:private@192.168.1.2:554/live?channel=1")
    feed._last_frame_monotonic = time.monotonic()
    settings = SimpleNamespace(host="192.168.1.2", channel_count=1)
    recovery = health.DVRRecovery(settings, [feed], tmp_path / "identity.json")
    recovery.identity = "hashed-device-identity"
    recovery._apply("192.168.1.8")
    recovery._save()
    assert feed.url == "rtsp://test:private@192.168.1.8:554/live?channel=1"
    assert not feed.status()["online"]
    assert "private" not in recovery.state_path.read_text()


def test_changed_ip_is_recovered_only_for_same_identity(tmp_path, monkeypatch) -> None:
    feed = CameraFeed(1, "Entrada", "rtsp://test:private@192.168.1.2:554/live")
    settings = SimpleNamespace(host="192.168.1.2", channel_count=1, user="test", password="private")
    recovery = health.DVRRecovery(settings, [feed], tmp_path / "identity.json")
    recovery.identity = "known"
    monkeypatch.setattr(health, "discover_onvif", lambda host: ["192.168.1.7", "192.168.1.8"])
    monkeypatch.setattr(health, "dvr_identity", lambda host, *_: {"192.168.1.2": "another", "192.168.1.7": "unknown", "192.168.1.8": "known"}[host])
    recovery.check_once()
    assert recovery.host == "192.168.1.8"
    assert feed.status()["host"] == "192.168.1.8"
