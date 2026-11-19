from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
from dotenv import dotenv_values, set_key

from camera_ai.event_store import EventStore
from camera_ai.recording.video_export import VideoExportError, prepare_telegram_video, telegram_video_metadata
from camera_ai.telegram_layout import event_caption, reminder_text

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    token: str = field(default="", repr=False)
    chats: tuple[str, ...] = ()
    enabled: bool = False
    allow_calibration: bool = False
    channels: tuple[int, ...] = (4,)
    reminder_seconds: int = 90
    max_reminders: int = 3

    @classmethod
    def load(cls, path: Path) -> "TelegramSettings":
        values = dotenv_values(path) if path.exists() else {}
        def value(key, default=""):
            return values.get(key, os.getenv(key, default)) or default
        def boolean(key):
            normalized = value(key, "false").strip().lower()
            if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise ValueError(f"Valor booleano inválido em {key}.")
            return normalized in {"true", "1", "yes", "on"}
        return cls(
            token=value("TELEGRAM_BOT_TOKEN"),
            chats=tuple(item.strip() for item in value("TELEGRAM_CHAT_IDS").split(",") if item.strip()),
            enabled=boolean("TELEGRAM_ENABLED"),
            allow_calibration=boolean("TELEGRAM_ALLOW_CALIBRATION"),
            channels=tuple(int(item) for item in value("TELEGRAM_CHANNELS", "4").split(",") if item.strip()),
            reminder_seconds=int(value("TELEGRAM_REMINDER_SECONDS", "90")),
            max_reminders=int(value("TELEGRAM_MAX_REMINDERS", "3")),
        )

    def validate(self, *, require_connection: bool = False) -> None:
        if (self.enabled or require_connection) and (not self.token or not self.chats):
            raise ValueError("Preencha o token do bot e pelo menos um chat de destino.")
        if self.token and not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{15,}", self.token):
            raise ValueError("Formato do token do Telegram inválido.")
        if len(self.chats) > 8 or any(not re.fullmatch(r"-?[0-9]{1,20}", item) or int(item) == 0 for item in self.chats):
            raise ValueError("Informe até oito IDs numéricos de chat, separados por vírgula.")
        if not self.channels or any(not 1 <= channel <= 9 for channel in self.channels):
            raise ValueError("Selecione canais de 1 a 9.")
        if not 30 <= self.reminder_seconds <= 3600 or not 0 <= self.max_reminders <= 5:
            raise ValueError("Intervalo ou quantidade de lembretes inválidos.")

    def save(self, path: Path) -> None:
        self.validate()
        values = {
            "TELEGRAM_BOT_TOKEN": self.token, "TELEGRAM_CHAT_IDS": ",".join(self.chats),
            "TELEGRAM_ENABLED": str(self.enabled).lower(), "TELEGRAM_ALLOW_CALIBRATION": str(self.allow_calibration).lower(),
            "TELEGRAM_CHANNELS": ",".join(map(str, self.channels)),
            "TELEGRAM_REMINDER_SECONDS": str(self.reminder_seconds), "TELEGRAM_MAX_REMINDERS": str(self.max_reminders),
        }
        for key, value in values.items():
            set_key(str(path), key, value)


class NotificationError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.code, self.reason = code, reason


def _telegram_error_reason(description: str) -> str | None:
    # Only classify known errors. Never return arbitrary remote text containing
    # URLs or credentials to the UI or logs.
    description = description.lower()
    if "message is not modified" in description:
        return "not_modified"
    if "message to edit not found" in description or "message to delete not found" in description:
        return "not_found"
    if "message can't be edited" in description:
        return "cannot_edit"
    if "query is too old" in description or "query id is invalid" in description:
        return "expired_callback"
    return None


class TelegramClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def call(self, method: str, payload: dict, *, attachment: tuple[str, Path] | None = None):
        headers = {"Content-Type": "application/json"}
        if attachment is None:
            body = json.dumps(payload).encode()
        else:
            field_name, path = attachment
            if path.stat().st_size > 48 * 1024 * 1024:
                raise NotificationError("Clipe excede o limite de envio; abra a gravação local.")
            boundary = "UVision" + uuid.uuid4().hex
            chunks = []
            for key, value in payload.items():
                if isinstance(value, (dict, list, bool)):
                    value = json.dumps(value)
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            mime = "image/jpeg" if path.suffix == ".jpg" else "video/mp4"
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            chunks.extend((path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
            body = b"".join(chunks)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        try:
            request = Request(f"https://api.telegram.org/bot{self._token}/{method}", data=body, headers=headers)
            # Uploading a full-resolution clip can take much longer than a
            # message. This runs in the notification worker, outside the UI.
            with urlopen(request, timeout=90 if attachment else 7) as response:
                result = json.loads(response.read())
            if not result.get("ok"):
                raise NotificationError(f"Telegram recusou a operação (código {result.get('error_code', 'desconhecido')}).")
            return result.get("result")
        except HTTPError as exc:
            reason = None
            try:
                body = json.loads(exc.read(8192))
                reason = _telegram_error_reason(str(body.get("description", "")))
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            raise NotificationError(f"Telegram HTTP {exc.code}; confira token, destinatário e permissões do bot.", code=exc.code, reason=reason) from None
        except TimeoutError:
            raise NotificationError("O Telegram não respondeu dentro do prazo; a fila foi preservada para nova tentativa.") from None
        except (URLError, OSError, ValueError):
            # HTTP errors include the bot token in their URL. Never expose or
            # persist the raw exception or remote response description.
            raise NotificationError("Não foi possível comunicar com o Telegram; a fila foi preservada.") from None


class NotificationService:
    def __init__(self, store: EventStore, project_dir: Path, env_path: Path, settings: TelegramSettings | None = None) -> None:
        self.store, self.project_dir, self.env_path = store, project_dir, env_path
        self.settings = settings or TelegramSettings.load(env_path)
        self.settings.validate()
        self.message = "Alertas desativados" if not self.settings.enabled else "Aguardando eventos confirmados"
        self._active: dict[tuple[int, int | None], str] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telegram-notifications", daemon=True)
        self._poll_thread = threading.Thread(target=self._poll_updates, name="telegram-receipts", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=15)
        if self._poll_thread.is_alive():
            self._poll_thread.join(timeout=10)

    def configure(self, settings: TelegramSettings) -> None:
        settings.validate()
        settings.save(self.env_path)
        with self._lock:
            self.settings = settings
            self.message = "Aguardando eventos confirmados" if settings.enabled else "Alertas desativados"
        self._wake.set()

    def observe(self, channel: int, camera_name: str, status: dict, frame) -> None:
        created = False
        with self._lock:
            settings = self.settings
            if not settings.enabled or channel not in settings.channels:
                return
            if status.get("shadowMode", True) and not settings.allow_calibration:
                return
            people = status.get("people", [status])
            confirmed = {item.get("trackId") for item in people if item.get("state") == "alert_pending"}
            for key in list(self._active):
                if key[0] == channel and key[1] not in confirmed:
                    del self._active[key]
            for track_id in confirmed:
                if (channel, track_id) in self._active:
                    continue
                event = self.store.create_alert(channel, track_id, camera_name, shadow=bool(status.get("shadowMode", True)))
                created = True
                self._active[(channel, track_id)] = event["id"]
                folder = self.project_dir / "runtime" / "alert-images"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (event["id"] + ".jpg")
                if cv2.imwrite(str(path), frame):
                    self.store.update_alert(event["id"], photo=path.name)
                else:
                    self.message = "Evento registrado; não foi possível salvar a imagem do alerta"
            if created:
                self._wake.set()

    def clip_ready(self, channel: int, path: Path, started_at: datetime) -> None:
        now = time.time()
        for event in self.store.alerts():
            if event["channel"] == channel and started_at.timestamp()-1 <= event["createdSeconds"] <= now and not event.get("clip"):
                self.store.update_alert(event["id"], clip=path.name)
        self._wake.set()

    @staticmethod
    def keyboard(event_id: str, *, acknowledged: bool = False) -> dict:
        return {"inline_keyboard": [] if acknowledged else [[{"text": "✓ Confirmar recebimento", "callback_data": "ack:"+event_id}]]}

    def _delivery(self, event_id: str, chat: str) -> dict:
        return self.store.alert(event_id).get("deliveries", {}).get(chat, {})

    def _record_message(self, event_id: str, chat: str, result, kind: str, *, primary: bool = False, **values) -> dict:
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int or message_id <= 0:
            raise NotificationError("O Telegram não retornou o identificador da mensagem; confira o histórico de entregas.")
        values["messages"] = {str(message_id): kind}
        if primary:
            values.update(mainMessageId=message_id, mainKind=kind, cardMissing=False)
        return self.store.update_delivery(event_id, chat, **values)

    def _adopt_legacy_message(self, event_id: str, chat: str) -> dict:
        delivery = self._delivery(event_id, chat)
        if not delivery.get("mainMessageId"):
            messages = delivery.get("messages", {})
            candidates = [(int(key), kind) for key, kind in messages.items()
                          if int(key) not in delivery.get("deletedMessageIds", [])]
            if candidates:
                message_id, kind = max(candidates, key=lambda item: (item[1] == "video", item[1] != "reminder", item[0]))
                delivery = self.store.update_delivery(event_id, chat, mainMessageId=message_id, mainKind=kind)
        return delivery

    def _send_initial(self, client: TelegramClient, event: dict, chat: str, current: float) -> None:
        caption = event_caption(event)
        payload = {"chat_id": chat, "parse_mode": "HTML", "reply_markup": self.keyboard(event["id"], acknowledged=event["status"] == "acknowledged")}
        photo = self.project_dir / "runtime" / "alert-images" / (event["id"]+".jpg")
        if photo.exists():
            result = client.call("sendPhoto", {**payload, "caption": caption}, attachment=("photo", photo))
            kind = "photo"
        else:
            result = client.call("sendMessage", {**payload, "text": caption})
            kind = "text"
        self._record_message(event["id"], chat, result, kind, primary=True, initialSent=current, lastSent=current, reminders=0,
                             cardCaption=caption, cardAcknowledged=event["status"] == "acknowledged")

    def _send_video(self, client: TelegramClient, event: dict, chat: str, current: float) -> None:
        clip = self.project_dir / "recordings" / "falls" / self.store.filename(event["clip"])
        if not clip.is_file():
            raise NotificationError("Clipe ainda não está disponível na estação local.")
        compatible = prepare_telegram_video(clip, self.project_dir / "runtime" / "telegram-videos")
        # A receipt may arrive while FFmpeg is working or the upload is running.
        event = self.store.alert(event["id"])
        delivery = event.get("deliveries", {}).get(chat, {})
        acknowledged = event["status"] == "acknowledged"
        caption = event_caption(event, video=True)
        media = {"type": "video", "media": "attach://video", "caption": caption, "parse_mode": "HTML",
                 "supports_streaming": True, **telegram_video_metadata(compatible)}
        payload = {"chat_id": chat, "reply_markup": self.keyboard(event["id"], acknowledged=acknowledged)}
        message_id = delivery.get("mainMessageId")
        result = None
        if message_id:
            try:
                result = client.call("editMessageMedia", {**payload, "message_id": message_id, "media": media}, attachment=("video", compatible))
            except NotificationError as exc:
                if exc.reason == "not_modified":
                    result = {"message_id": message_id}
                elif exc.reason not in {"not_found", "cannot_edit"}:
                    raise
        if result is None:
            result = client.call("sendVideo", {**payload, **telegram_video_metadata(compatible), "supports_streaming": True,
                                               "caption": caption, "parse_mode": "HTML"}, attachment=("video", compatible))
        values = {"clipSent": current, "cardCaption": caption, "cardAcknowledged": acknowledged}
        if not delivery.get("initialSent"):
            values.update(initialSent=current, lastSent=current, reminders=0)
        self._record_message(event["id"], chat, result, "video", primary=True, **values)

    def _send_reminder(self, client: TelegramClient, event_id: str, chat: str, current: float) -> None:
        event = self.store.alert(event_id)
        if event["status"] == "acknowledged":
            return
        delivery = event["deliveries"][chat]
        result = client.call("sendMessage", {"chat_id": chat, "text": reminder_text(event), "parse_mode": "HTML",
                                             "reply_markup": self.keyboard(event_id)})
        self._record_message(event_id, chat, result, "reminder", lastSent=current, reminders=delivery.get("reminders", 0)+1)

    def _organize(self, client: TelegramClient, event_id: str, chat: str, current: float) -> None:
        event = self.store.alert(event_id)
        delivery = self._adopt_legacy_message(event_id, chat)
        main_id = delivery.get("mainMessageId")
        if not main_id or delivery.get("maintenanceFailures", 0) >= 5 or current < delivery.get("maintenanceNextAttempt", 0):
            return
        acknowledged = event["status"] == "acknowledged"
        caption = event_caption(event, video=delivery.get("mainKind") == "video")
        try:
            if delivery.get("cardCaption") != caption or delivery.get("cardAcknowledged") != acknowledged:
                method = "editMessageText" if delivery.get("mainKind") in {"text", "reminder"} else "editMessageCaption"
                key = "text" if method == "editMessageText" else "caption"
                try:
                    client.call(method, {"chat_id": chat, "message_id": main_id, key: caption, "parse_mode": "HTML",
                                         "reply_markup": self.keyboard(event_id, acknowledged=acknowledged)})
                except NotificationError as exc:
                    if exc.reason != "not_modified":
                        raise
                self.store.update_delivery(event_id, chat, cardCaption=caption, cardAcknowledged=acknowledged)
            messages = delivery.get("messages", {})
            deleted = set(delivery.get("deletedMessageIds", []))
            remaining = {int(key): kind for key, kind in messages.items() if int(key) not in deleted and int(key) != main_id}
            if acknowledged and delivery.get("mainKind") == "video" and delivery.get("clipSent"):
                obsolete = list(remaining)
            else:
                reminders = [key for key, kind in remaining.items() if kind == "reminder"]
                obsolete = [key for key in reminders if key != max(reminders)] if reminders else []
            if obsolete:
                client.call("deleteMessages", {"chat_id": chat, "message_ids": obsolete[:100]})
                self.store.update_delivery(event_id, chat, deletedMessageIds=obsolete[:100])
            if delivery.get("maintenanceFailures") or delivery.get("maintenanceError"):
                self.store.update_delivery(event_id, chat, maintenanceFailures=0, maintenanceNextAttempt=0, maintenanceError=None)
        except NotificationError as exc:
            failures = delivery.get("maintenanceFailures", 0)+1
            self.store.update_delivery(event_id, chat, maintenanceFailures=failures,
                                       maintenanceNextAttempt=current+min(300, 15*2**failures), maintenanceError=str(exc))
            self.message = "Recebimento preservado; organização da conversa pendente." if acknowledged else "Entrega preservada; organização da conversa pendente."
            LOGGER.warning("Organização do Telegram: %s", exc)

    def process_once(self, client: TelegramClient, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        settings = self.settings
        if not settings.enabled:
            return
        for event in self.store.alerts():
            if self._stop.is_set():
                break
            if event["channel"] not in settings.channels or (event.get("shadow") and not settings.allow_calibration):
                continue
            expired = current-event["createdSeconds"] > 86400
            if expired and event["status"] != "acknowledged":
                if event["status"] != "expired":
                    self.store.update_alert(event["id"], status="expired")
                continue
            for chat in settings.chats:
                event = self.store.alert(event["id"])
                acknowledged = event["status"] == "acknowledged"
                delivery = self._adopt_legacy_message(event["id"], chat)
                retry_ready = delivery.get("failures", 0) < 5 and current >= delivery.get("nextAttempt", 0)
                video_due = event.get("clip") and (not delivery.get("clipSent") or
                            (delivery.get("mainMessageId") and delivery.get("mainKind") != "video"))
                send_allowed = not expired and retry_ready and (not acknowledged or delivery.get("initialSent"))
                try:
                    sent = False
                    if send_allowed and video_due:
                        self._send_video(client, event, chat, current)
                        sent = True
                    elif send_allowed and not delivery.get("initialSent"):
                        self._send_initial(client, event, chat, current)
                        sent = True
                    elif send_allowed and not acknowledged and current-delivery.get("lastSent", current) >= settings.reminder_seconds and delivery.get("reminders", 0) < settings.max_reminders:
                        self._send_reminder(client, event["id"], chat, current)
                        sent = True
                    if sent:
                        self.store.update_delivery(event["id"], chat, failures=0, nextAttempt=0, error=None)
                        self.store.update_alert(event["id"], status="sent")
                        self.message = "Recebimento confirmado; lembretes deste evento encerrados." if self.store.alert(event["id"])["status"] == "acknowledged" else "Entrega registrada; aguardando confirmação de recebimento"
                except (NotificationError, VideoExportError, OSError) as exc:
                    failures = delivery.get("failures", 0)+1
                    message = str(exc) if isinstance(exc, (NotificationError, VideoExportError)) else "Não foi possível acessar o clipe; confira o armazenamento local."
                    self.store.update_delivery(event["id"], chat, failures=failures, nextAttempt=current+min(300, 15*2**failures), error=message)
                    self.message = message
                    if not self._delivery(event["id"], chat).get("initialSent"):
                        self.store.update_alert(event["id"], status="delivery_error")
                self._organize(client, event["id"], chat, current)

    def handle_updates(self, client: TelegramClient, updates: list) -> None:
        allowed = set(self.settings.chats)
        for update in updates:
            callback = update.get("callback_query", {})
            message = callback.get("message", {})
            chat = str(message.get("chat", {}).get("id", ""))
            data = callback.get("data", "")
            if chat in allowed and isinstance(data, str) and data.startswith("ack:"):
                try:
                    event = self.store.alert(data[4:])
                except ValueError:
                    event = None
                if event and event.get("deliveries", {}).get(chat, {}).get("initialSent"):
                    message_id = message.get("message_id")
                    tracked = str(message_id) in event["deliveries"][chat].get("messages", {})
                    sender = message.get("from", {})
                    own_message = sender.get("is_bot") is True and str(sender.get("id")) == self.settings.token.split(":", 1)[0]
                    if type(message_id) is int and message_id > 0 and (tracked or own_message):
                        kind = "video" if "video" in message else "photo" if "photo" in message else "document" if "document" in message else "text"
                        self.store.update_delivery(event["id"], chat, messages={str(message_id): kind})
                        self.store.acknowledge(event["id"], "Telegram · "+str(callback.get("from", {}).get("id", "destinatário")))
                        self.message = "Recebimento confirmado; lembretes deste evento encerrados."
                        try:
                            client.call("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                               "text": "Recebido ✓ Os lembretes foram encerrados."})
                        except NotificationError as exc:
                            # Expired callback popups do not undo a stored receipt.
                            LOGGER.warning("Recebimento salvo; resposta do botão indisponível: %s", exc)
                        self._wake.set()
            self.store.telegram_offset = max(self.store.telegram_offset, int(update["update_id"])+1)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                settings = self.settings
                if settings.enabled:
                    client = TelegramClient(settings.token)
                    self.process_once(client)
            except (NotificationError, OSError, ValueError, KeyError) as exc:
                self.message = str(exc) if isinstance(exc, NotificationError) else "Falha no histórico de notificações; confira o diagnóstico local."
                LOGGER.warning("Notificações: %s", self.message)
            self._wake.wait(3)
            self._wake.clear()

    def _poll_updates(self) -> None:
        while not self._stop.is_set():
            try:
                settings = self.settings
                if settings.enabled:
                    client = TelegramClient(settings.token)
                    updates = client.call("getUpdates", {"offset": self.store.telegram_offset, "timeout": 3, "allowed_updates": ["callback_query"]})
                    self.handle_updates(client, updates or [])
            except (NotificationError, OSError, ValueError, KeyError) as exc:
                self.message = str(exc) if isinstance(exc, NotificationError) else "Falha ao registrar recebimentos; confira o diagnóstico local."
                LOGGER.warning("Recebimentos: %s", self.message)
            self._stop.wait(1 if self.settings.enabled else 3)
