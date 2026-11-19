from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from unittest.mock import Mock
from urllib.error import HTTPError

import numpy as np
import pytest

from camera_ai.event_store import EventStore
from camera_ai.notifications import NotificationError, NotificationService, TelegramClient, TelegramSettings


def service(tmp_path, **overrides):
    settings = TelegramSettings(token="123456:abcdefghijklmnopqrstuvwxyz", chats=("123",), enabled=True, **overrides)
    return NotificationService(EventStore(tmp_path/"events.json"), tmp_path, tmp_path/".env", settings)


def test_disabled_and_shadow_events_do_not_send_or_create_alerts(tmp_path) -> None:
    notifier = service(tmp_path)
    notifier.observe(4, "Cozinha", {"state": "alert_pending", "shadowMode": True}, np.zeros((10,10,3), np.uint8))
    assert notifier.store.alerts() == []


def test_confirmed_event_is_deduplicated_retried_and_acknowledged(tmp_path) -> None:
    notifier = service(tmp_path)
    status = {"state": "alert_pending", "shadowMode": False, "trackId": 2}
    frame = np.zeros((10,10,3), np.uint8)
    notifier.observe(4, "Cozinha", status, frame)
    notifier.observe(4, "Cozinha", status, frame)
    assert len(notifier.store.alerts()) == 1
    event = notifier.store.alerts()[0]
    client = Mock()
    client.call.side_effect = NotificationError("Sem conexão")
    now = time.time()
    notifier.process_once(client, now=now)
    delivery = notifier.store.alerts()[0]["deliveries"]["123"]
    assert delivery["failures"] == 1 and delivery["nextAttempt"] > now
    client.call.side_effect = None
    client.call.return_value = {"message_id": 42}
    notifier.process_once(client, now=now+31)
    assert client.call.call_args.args[0] == "sendPhoto"
    count = client.call.call_count
    notifier.process_once(client, now=now+32)
    assert client.call.call_count == count
    notifier.handle_updates(client, [{"update_id": 5, "callback_query": {"id": "callback", "data": "ack:"+event["id"], "message": {"chat": {"id": 123}, "message_id": 42}, "from": {"id": 1}}}])
    assert notifier.store.alerts()[0]["status"] == "acknowledged"
    notifier.process_once(client, now=now+300)
    count = client.call.call_count
    notifier.process_once(client, now=now+301)
    assert client.call.call_count == count
    assert notifier.store.telegram_offset == 6


def test_unauthorized_chat_cannot_acknowledge(tmp_path) -> None:
    notifier = service(tmp_path)
    event = notifier.store.create_alert(4, 2, "Cozinha", shadow=False)
    notifier.handle_updates(Mock(), [{"update_id": 1, "callback_query": {"id": "other", "data": "ack:"+event["id"], "message": {"chat": {"id": 999}}}}])
    assert notifier.store.alerts()[0]["status"] == "pending"


def test_transport_error_does_not_reveal_bot_token(monkeypatch) -> None:
    from camera_ai import notifications
    token = "123456:abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(notifications, "urlopen", Mock(side_effect=HTTPError("https://api.telegram.org/bot"+token, 401, "invalid", {}, None)))
    with pytest.raises(NotificationError) as error:
        TelegramClient(token).call("sendMessage", {"chat_id": "123", "text": "test"})
    assert token not in str(error.value)


def test_clip_upload_can_exceed_the_old_twelve_second_deadline(tmp_path, monkeypatch) -> None:
    from camera_ai import notifications
    clip = tmp_path / "fall.mp4"
    clip.write_bytes(b"test-media")

    def upload(request, *, timeout):
        # Model a slow uplink without sleeping or contacting Telegram.
        if timeout < 30:
            raise TimeoutError("upload still in progress")
        assert b"test-media" in request.data
        return io.BytesIO(b'{"ok": true, "result": {"message_id": 1}}')

    monkeypatch.setattr(notifications, "urlopen", upload)
    result = TelegramClient("123456:abcdefghijklmnopqrstuvwxyz").call(
        "sendDocument", {"chat_id": "123"}, attachment=("document", clip),
    )
    assert result["message_id"] == 1


def test_late_delivery_cannot_undo_local_acknowledgement(tmp_path) -> None:
    notifier = service(tmp_path)
    event = notifier.store.create_alert(4, 2, "Cozinha", shadow=False)
    notifier.store.acknowledge(event["id"])
    notifier.store.update_alert(event["id"], status="sent", deliveries={"123": {"initialSent": time.time()}})
    assert notifier.store.alerts()[0]["status"] == "acknowledged"


def test_receipt_stops_reminders_but_clip_is_still_delivered(tmp_path, monkeypatch) -> None:
    from camera_ai import notifications
    notifier = service(tmp_path)
    client = Mock()
    client.call.return_value = {"message_id": 42}
    notifier.observe(4, "Cozinha", {"state": "alert_pending", "shadowMode": False, "trackId": 1}, np.zeros((10,10,3), np.uint8))
    event = notifier.store.alerts()[0]
    now = time.time()
    notifier.process_once(client, now=now)
    notifier.store.acknowledge(event["id"])
    folder = tmp_path / "recordings/falls"
    folder.mkdir(parents=True)
    clip = folder / "queda-ch04_test.mp4"
    clip.write_bytes(b"test-media")
    compatible = tmp_path / "compatible.mp4"
    compatible.write_bytes(b"converted-media")
    export = Mock(return_value=compatible)
    monkeypatch.setattr(notifications, "prepare_telegram_video", export)
    monkeypatch.setattr(notifications, "telegram_video_metadata", lambda _path: {"width": 1920, "height": 1080, "duration": 43})
    notifier.clip_ready(4, clip, datetime.fromtimestamp(now-5, timezone.utc))
    assert notifier.store.is_protected(clip)
    notifier.process_once(client, now=now+10)
    export.assert_called_once_with(clip, tmp_path / "runtime" / "telegram-videos")
    assert client.call.call_args.args[0] == "editMessageMedia"
    assert client.call.call_args.kwargs["attachment"] == ("video", compatible)
    media = client.call.call_args.args[1]["media"]
    assert media["supports_streaming"] is True
    assert media["width"] == 1920
    assert media["height"] == 1080
    assert media["duration"] == 43
    assert "Recebimento confirmado" in media["caption"]
    assert client.call.call_args.args[1]["reply_markup"] == {"inline_keyboard": []}
    assert notifier.store.alerts()[0]["status"] == "acknowledged"
    assert not notifier.store.is_protected(clip)


def test_failed_video_conversion_keeps_clip_pending_and_never_sends_original(tmp_path, monkeypatch) -> None:
    from camera_ai import notifications
    from camera_ai.recording.video_export import VideoExportError
    notifier = service(tmp_path)
    event = notifier.store.create_alert(4, 1, "Cozinha", shadow=False)
    now = time.time()
    folder = tmp_path / "recordings/falls"
    folder.mkdir(parents=True)
    clip = folder / "fall.mp4"
    clip.write_bytes(b"original-media")
    notifier.store.update_alert(event["id"], clip=clip.name, deliveries={"123": {"initialSent": now}})
    monkeypatch.setattr(notifications, "prepare_telegram_video", Mock(side_effect=VideoExportError("FFmpeg indisponível")))
    client = Mock()
    notifier.process_once(client, now=now)
    client.call.assert_not_called()
    delivery = notifier.store.alerts()[0]["deliveries"]["123"]
    assert not delivery.get("clipSent")
    assert delivery["failures"] == 1 and delivery["nextAttempt"] > now
    assert notifier.store.is_protected(clip)


def test_receipt_updates_clicked_button_and_repeated_click_is_idempotent(tmp_path) -> None:
    notifier = service(tmp_path)
    event = notifier.store.create_alert(4, 1, "Cozinha", shadow=False)
    notifier.store.update_alert(event["id"], deliveries={"123": {"initialSent": time.time(), "messages": {"42": "text"}}})
    callback = {"id": "callback", "data": "ack:"+event["id"],
                "message": {"chat": {"id": 123}, "message_id": 42}, "from": {"id": 1}}
    client = Mock()
    notifier.handle_updates(client, [{"update_id": 6, "callback_query": callback}])
    confirmed = notifier.store.alerts()[0]
    assert confirmed["status"] == "acknowledged"
    assert "encerrados" in notifier.message
    assert "show_alert" not in client.call.call_args_list[0].args[1]
    notifier.process_once(client)
    edit = client.call.call_args
    assert edit.args[0] == "editMessageText"
    assert edit.args[1]["message_id"] == 42
    assert edit.args[1]["reply_markup"] == notifier.keyboard(event["id"], acknowledged=True)
    notifier.handle_updates(client, [{"update_id": 7, "callback_query": callback}])
    assert notifier.store.alerts()[0]["acknowledgedAt"] == confirmed["acknowledgedAt"]
    count = client.call.call_count
    notifier.process_once(client, now=time.time()+1000)
    assert client.call.call_count == count


def test_failed_callback_reply_keeps_receipt_and_advances_offset(tmp_path) -> None:
    notifier = service(tmp_path)
    event = notifier.store.create_alert(4, 1, "Cozinha", shadow=False)
    notifier.store.update_alert(event["id"], deliveries={"123": {"initialSent": time.time(), "messages": {"42": "text"}}})
    client = Mock()
    client.call.side_effect = [NotificationError("Consulta expirada"), None]
    notifier.handle_updates(client, [{"update_id": 6, "callback_query": {
        "id": "callback", "data": "ack:"+event["id"], "message": {"chat": {"id": 123}, "message_id": 42},
    }}])
    assert notifier.store.alerts()[0]["status"] == "acknowledged"
    assert notifier.store.telegram_offset == 7
    notifier.process_once(client)
    assert client.call.call_args.args[0] == "editMessageText"


def test_video_multipart_uses_lowercase_json_boolean(tmp_path, monkeypatch) -> None:
    from camera_ai import notifications
    clip = tmp_path / "fall.mp4"
    clip.write_bytes(b"test-media")
    request = Mock(return_value=io.BytesIO(b'{"ok": true, "result": {}}'))
    monkeypatch.setattr(notifications, "urlopen", request)
    TelegramClient("123456:abcdefghijklmnopqrstuvwxyz").call(
        "sendVideo", {"supports_streaming": True}, attachment=("video", clip),
    )
    assert b'name="supports_streaming"\r\n\r\ntrue\r\n' in request.call_args.args[0].data


def test_reminders_are_bounded(tmp_path) -> None:
    notifier = service(tmp_path, max_reminders=1)
    client = Mock()
    client.call.side_effect = [{"message_id": 1}, {"message_id": 2}]
    notifier.store.create_alert(4, 1, "Cozinha", shadow=False)
    now = time.time()
    for seconds in (0, 91, 200, 300):
        notifier.process_once(client, now=now+seconds)
    assert client.call.call_count == 2


def test_configuration_save_preserves_unrelated_local_settings(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("DVR_HOST=192.168.1.2\nDVR_PASSWORD='test-only-value'\n", encoding="utf-8")
    TelegramSettings().save(env)
    from dotenv import dotenv_values
    values = dotenv_values(env)
    assert values["DVR_HOST"] == "192.168.1.2"
    assert values["DVR_PASSWORD"] == "test-only-value"
    assert TelegramSettings.load(env).enabled is False
