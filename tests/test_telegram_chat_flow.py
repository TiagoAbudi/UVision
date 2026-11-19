from __future__ import annotations

import copy
import io
import json
import queue
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError

import numpy as np
import pytest

from camera_ai import notifications
from camera_ai.event_store import EventStore
from camera_ai.notifications import NotificationError, NotificationService, TelegramClient, TelegramSettings
from camera_ai.telegram_layout import event_caption, reminder_text


class TelegramChat:
    """A chat with real message identity, edits and deletions, without network."""

    def __init__(self):
        self.messages = {}
        self.calls = []
        self.updates = queue.Queue()
        self.next_id = 100
        self.failures = {}

    def call(self, method, payload, *, attachment=None):
        self.calls.append((method, copy.deepcopy(payload)))
        failures = self.failures.get(method, [])
        if failures:
            raise failures.pop(0)
        if method == "getUpdates":
            try:
                return [self.updates.get(timeout=.03)]
            except queue.Empty:
                return []
        if method == "answerCallbackQuery":
            return True
        chat = str(payload["chat_id"])
        if method.startswith("send"):
            self.next_id += 1
            message = {"message_id": self.next_id, "chat": {"id": int(chat)}, "from": {"id": 123456, "is_bot": True}}
            if method == "sendVideo":
                message["video"] = {key: payload[key] for key in ("width", "height", "duration")}
            elif method == "sendPhoto":
                message["photo"] = [{}]
            message.update({key: copy.deepcopy(payload[key]) for key in ("caption", "text", "reply_markup") if key in payload})
            self.messages[chat, self.next_id] = message
            return copy.deepcopy(message)
        if method == "deleteMessages":
            for message_id in payload["message_ids"]:
                self.messages.pop((chat, message_id), None)
            return True
        key = (chat, payload["message_id"])
        if key not in self.messages:
            raise NotificationError("Mensagem não encontrada", code=400, reason="not_found")
        message = self.messages[key]
        if method == "editMessageMedia":
            media = payload["media"]
            for field in ("photo", "text", "document"):
                message.pop(field, None)
            message["video"] = {field: media[field] for field in ("width", "height", "duration")}
            message["caption"] = media["caption"]
        elif method == "editMessageCaption":
            message["caption"] = payload["caption"]
        elif method == "editMessageText":
            message["text"] = payload["text"]
        else:
            raise AssertionError(method)
        message["reply_markup"] = copy.deepcopy(payload["reply_markup"])
        return copy.deepcopy(message)


@pytest.fixture
def flow(tmp_path, monkeypatch):
    settings = TelegramSettings(token="123456:abcdefghijklmnopqrstuvwxyz", chats=("123",), enabled=True, allow_calibration=True)
    service = NotificationService(EventStore(tmp_path / "events.json"), tmp_path, tmp_path / ".env", settings)
    client = TelegramChat()
    compatible = tmp_path / "compatible.mp4"
    compatible.write_bytes(b"converted-video")
    monkeypatch.setattr(notifications, "prepare_telegram_video", lambda *_: compatible)
    monkeypatch.setattr(notifications, "telegram_video_metadata", lambda _: {"width": 1920, "height": 1080, "duration": 43})
    service.observe(4, "Cozinha", {"state": "alert_pending", "shadowMode": True, "trackId": 11}, np.zeros((8, 8, 3), np.uint8))
    event = service.store.alerts()[0]
    return service, client, event


def add_clip(service, event, now):
    folder = service.project_dir / "recordings/falls"
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "queda-ch04_test.mp4"
    clip.write_bytes(b"original-video")
    service.clip_ready(4, clip, datetime.fromtimestamp(now-10, timezone.utc))
    return clip


def receipt(service, client, event, message, *, update_id=1):
    service.handle_updates(client, [{"update_id": update_id, "callback_query": {
        "id": "callback", "data": "ack:"+event["id"], "message": copy.deepcopy(message), "from": {"id": 1},
    }}])


def test_photo_becomes_video_and_receipt_leaves_only_clip_with_essential_caption(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    main_id = next(iter(client.messages))[1]
    service.process_once(client, now=now+91)
    service.process_once(client, now=now+182)
    assert len(client.messages) == 2  # Main card and one current reminder.
    clip = add_clip(service, event, now)
    service.process_once(client, now=now+183)
    assert "video" in client.messages["123", main_id]
    receipt(service, client, event, client.messages["123", main_id])
    service.process_once(client, now=now+184)
    assert list(client.messages) == [("123", main_id)]
    final = client.messages["123", main_id]
    assert "Recebimento confirmado" in final["caption"]
    assert "Cozinha" in final["caption"] and "CH 04" in final["caption"]
    assert "Teste" in final["caption"]
    assert event["id"] not in final["caption"] and "#11" not in final["caption"]
    assert final["reply_markup"] == {"inline_keyboard": []}
    assert final["video"] == {"width": 1920, "height": 1080, "duration": 43}
    assert clip.read_bytes() == b"original-video"
    count = len(client.calls)
    service.process_once(client, now=now+1000)
    assert len(client.calls) == count


def test_receipt_before_clip_preserves_photo_until_video_is_available(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    service.process_once(client, now=now+91)
    reminder = max(client.messages.values(), key=lambda item: item["message_id"])
    receipt(service, client, event, reminder)
    service.process_once(client, now=now+92)
    assert any("photo" in message for message in client.messages.values())
    add_clip(service, event, now)
    service.process_once(client, now=now+93)
    assert len(client.messages) == 1
    assert "video" in next(iter(client.messages.values()))
    assert service.store.alert(event["id"])["status"] == "acknowledged"


def test_clip_already_ready_sends_one_video_without_extra_photo(flow):
    service, client, event = flow
    now = time.time()
    add_clip(service, event, now)
    service.process_once(client, now=now)
    assert [method for method, _ in client.calls] == ["sendVideo"]
    assert len(client.messages) == 1


def test_upload_timeout_does_not_send_a_duplicate_video(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    add_clip(service, event, now)
    client.failures["editMessageMedia"] = [NotificationError("Upload interrompido")]
    service.process_once(client, now=now+1)
    assert not any(method == "sendVideo" for method, _ in client.calls)
    assert "photo" in next(iter(client.messages.values()))
    service.process_once(client, now=now+32)
    assert len(client.messages) == 1
    assert "video" in next(iter(client.messages.values()))


def test_missing_original_card_falls_back_to_video_without_touching_other_messages(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    client.messages.clear()  # User removed the original card.
    client.messages["123", 999] = {"text": "Mensagem particular do usuário"}
    add_clip(service, event, now)
    service.process_once(client, now=now+1)
    video = next(message for message in client.messages.values() if "video" in message)
    receipt(service, client, event, video)
    service.process_once(client, now=now+2)
    assert client.messages["123", 999]["text"] == "Mensagem particular do usuário"
    deleted = [value for method, payload in client.calls if method == "deleteMessages" for value in payload["message_ids"]]
    assert 999 not in deleted


def test_cleanup_failure_is_retried_after_restart_without_undoing_receipt(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    service.process_once(client, now=now+91)
    add_clip(service, event, now)
    service.process_once(client, now=now+92)
    video = next(message for message in client.messages.values() if "video" in message)
    receipt(service, client, event, video)
    client.failures["deleteMessages"] = [NotificationError("Falha temporária")]
    service.process_once(client, now=now+93)
    record = service.store.alert(event["id"])
    assert record["status"] == "acknowledged"
    assert record["deliveries"]["123"]["maintenanceFailures"] == 1
    restarted = NotificationService(EventStore(service.store.path), service.project_dir, service.env_path, service.settings)
    restarted.process_once(client, now=now+124)
    assert len(client.messages) == 1
    assert restarted.store.alert(event["id"])["deliveries"]["123"]["maintenanceError"] is None


def test_shared_receipt_cleans_each_configured_chat_and_stops_all_reminders(flow):
    service, client, event = flow
    now = time.time()
    service.settings = TelegramSettings(token=service.settings.token, chats=("123", "456"), enabled=True, allow_calibration=True)
    service.process_once(client, now=now)
    service.process_once(client, now=now+91)
    add_clip(service, event, now)
    service.process_once(client, now=now+92)
    video = next(message for (chat, _), message in client.messages.items() if chat == "123" and "video" in message)
    receipt(service, client, event, video)
    service.process_once(client, now=now+93)
    assert len(client.messages) == 2
    assert all("video" in message and not message["reply_markup"]["inline_keyboard"] for message in client.messages.values())
    count = len(client.calls)
    service.process_once(client, now=now+1000)
    assert len(client.calls) == count


def test_unknown_messages_from_other_senders_cannot_confirm_or_be_deleted(flow):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    forged = {"message_id": 999, "chat": {"id": 123}, "from": {"id": 321, "is_bot": True}}
    receipt(service, client, event, forged)
    assert service.store.alert(event["id"])["status"] == "sent"
    assert "999" not in service.store.alert(event["id"])["deliveries"]["123"]["messages"]


def test_legacy_video_is_adopted_only_from_verified_callback_and_no_history_is_guessed(flow):
    service, client, event = flow
    now = time.time()
    add_clip(service, event, now)
    service.store.update_delivery(event["id"], "123", initialSent=now, clipSent=now)
    service.store.acknowledge(event["id"])
    service.process_once(client, now=now)
    assert client.calls == []
    old_video = {"message_id": 30, "chat": {"id": 123}, "from": {"id": 123456, "is_bot": True}, "video": {}}
    client.messages["123", 30] = old_video
    receipt(service, client, event, old_video)
    service.process_once(client, now=now+1)
    assert "Recebimento confirmado" in client.messages["123", 30]["caption"]
    assert not any(method in {"sendVideo", "deleteMessages"} for method, _ in client.calls)
    old_reminder = {"message_id": 29, "chat": {"id": 123}, "from": {"id": 123456, "is_bot": True}, "text": "Lembrete antigo"}
    client.messages["123", 29] = old_reminder
    receipt(service, client, event, old_reminder, update_id=2)
    service.process_once(client, now=now+2)
    assert list(client.messages) == [("123", 30)]


def test_receipt_arriving_during_upload_is_preserved_and_final_card_is_clean(flow, monkeypatch):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    original = copy.deepcopy(next(iter(client.messages.values())))
    add_clip(service, event, now)
    call = client.call
    def upload(method, payload, **kwargs):
        if method == "editMessageMedia":
            receipt(service, client, event, original)
        return call(method, payload, **kwargs)
    monkeypatch.setattr(client, "call", upload)
    service.process_once(client, now=now+1)
    final = next(iter(client.messages.values()))
    assert service.store.alert(event["id"])["status"] == "acknowledged"
    assert "Recebimento confirmado" in final["caption"]
    assert final["reply_markup"] == {"inline_keyboard": []}


def test_receipts_are_polled_while_upload_worker_is_blocked(flow, monkeypatch):
    service, client, event = flow
    now = time.time()
    service.process_once(client, now=now)
    original = copy.deepcopy(next(iter(client.messages.values())))
    add_clip(service, event, now)
    started, release, acknowledged = threading.Event(), threading.Event(), threading.Event()
    call = client.call
    def upload(method, payload, **kwargs):
        if method == "editMessageMedia":
            started.set()
            assert release.wait(5)
        result = call(method, payload, **kwargs)
        if method == "answerCallbackQuery":
            acknowledged.set()
        return result
    monkeypatch.setattr(client, "call", upload)
    monkeypatch.setattr(notifications, "TelegramClient", lambda _token: client)
    service.start()
    try:
        assert started.wait(3)
        client.updates.put({"update_id": 1, "callback_query": {"id": "callback", "data": "ack:"+event["id"], "message": original}})
        assert acknowledged.wait(3)
        assert not release.is_set()
        assert service.store.alert(event["id"])["status"] == "acknowledged"
    finally:
        release.set()
        service.stop()
    assert not service._thread.is_alive() and not service._poll_thread.is_alive()


def test_captions_escape_names_keep_test_label_and_hide_internal_ids(flow):
    _, _, event = flow
    event["cameraName"] = "Cozinha <script> & corredor"
    caption = event_caption(event)
    assert "&lt;script&gt; &amp;" in caption
    assert "🧪" in caption and len(caption) < 350
    assert event["id"] not in caption and "#11" not in caption
    assert "teste" in reminder_text(event).lower()


def test_remote_errors_are_classified_without_exposing_response_secrets(monkeypatch):
    token = "123456:abcdefghijklmnopqrstuvwxyz"
    body = io.BytesIO(json.dumps({"description": "Bad Request: message is not modified " + token}).encode())
    def fail(*_args, **_kwargs):
        raise HTTPError("https://api.telegram.org/bot"+token, 400, "invalid", {}, body)
    monkeypatch.setattr(notifications, "urlopen", fail)
    with pytest.raises(NotificationError) as error:
        TelegramClient(token).call("editMessageCaption", {})
    assert error.value.code == 400 and error.value.reason == "not_modified"
    assert token not in str(error.value)
