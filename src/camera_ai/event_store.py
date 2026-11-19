from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class EventStore:
    """Small local journal shared by the UI, recorder and notification worker."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = {"version": 1, "reviews": {}, "alerts": {}, "telegramOffset": 0}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") != 1 or not isinstance(loaded.get("reviews"), dict) or not isinstance(loaded.get("alerts"), dict):
                raise ValueError("Histórico local de eventos inválido. Preserve o arquivo para recuperação.")
            self._data.update(loaded)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(self._data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)

    @staticmethod
    def filename(value: Path | str) -> str:
        name = str(value.name if isinstance(value, Path) else value)
        if Path(name).name != name or "/" in name or "\\" in name or not name.lower().endswith(".mp4"):
            raise ValueError("Nome de clipe inválido.")
        return name

    def reviews(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data["reviews"])

    def review(self, path: Path | str, label: str | None, *, protected: bool = True) -> None:
        if label not in {None, "fall", "normal", "unsure"}:
            raise ValueError("Classificação inválida.")
        name = self.filename(path)
        with self._lock:
            self._data["reviews"][name] = {
                "label": label, "protected": bool(protected),
                "reviewedAt": datetime.now(timezone.utc).isoformat(),
            }
            self._save()

    def protect(self, path: Path | str, enabled: bool) -> None:
        name = self.filename(path)
        with self._lock:
            record = self._data["reviews"].get(name, {})
            self.review(name, record.get("label"), protected=enabled)

    def is_protected(self, path: Path | str) -> bool:
        name = self.filename(path)
        with self._lock:
            return bool(self._data["reviews"].get(name, {}).get("protected")) or any(
                alert.get("clip") == name and (alert.get("status") != "acknowledged" or any(
                    delivery.get("initialSent") and not delivery.get("clipSent") for delivery in alert.get("deliveries", {}).values()
                ))
                for alert in self._data["alerts"].values()
            )

    def create_alert(self, channel: int, track_id: int | None, camera_name: str, *, shadow: bool) -> dict:
        with self._lock:
            record = {
                "id": uuid.uuid4().hex, "channel": channel, "trackId": track_id, "cameraName": camera_name,
                "createdAt": datetime.now(timezone.utc).isoformat(), "createdSeconds": time.time(),
                "shadow": shadow, "status": "pending", "deliveries": {}, "clip": None,
            }
            self._data["alerts"][record["id"]] = record
            self._save()
            return copy.deepcopy(record)

    def alerts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self._data["alerts"].values()))

    def alert(self, event_id: str) -> dict:
        with self._lock:
            if event_id not in self._data["alerts"]:
                raise ValueError("Evento não encontrado.")
            return copy.deepcopy(self._data["alerts"][event_id])

    def update_delivery(self, event_id: str, chat: str, **values) -> dict:
        """Merge sender and receipt updates without losing tracked messages."""
        with self._lock:
            if event_id not in self._data["alerts"]:
                raise ValueError("Evento não encontrado.")
            delivery = self._data["alerts"][event_id].setdefault("deliveries", {}).setdefault(chat, {})
            values = copy.deepcopy(values)
            if "messages" in values:
                delivery.setdefault("messages", {}).update(values.pop("messages"))
            if "deletedMessageIds" in values:
                values["deletedMessageIds"] = sorted(set(delivery.get("deletedMessageIds", [])) | set(values["deletedMessageIds"]))
            delivery.update(values)
            self._save()
            return copy.deepcopy(delivery)

    def update_alert(self, event_id: str, **values) -> None:
        with self._lock:
            if event_id not in self._data["alerts"]:
                raise ValueError("Evento não encontrado.")
            if self._data["alerts"][event_id].get("status") == "acknowledged" and values.get("status") != "acknowledged":
                values.pop("status", None)
            self._data["alerts"][event_id].update(copy.deepcopy(values))
            self._save()

    def acknowledge(self, event_id: str, actor: str = "Estação local") -> None:
        with self._lock:
            if self.alert(event_id)["status"] == "acknowledged":
                return
            self.update_alert(event_id, status="acknowledged", acknowledgedBy=actor,
                              acknowledgedAt=datetime.now(timezone.utc).isoformat())

    @property
    def telegram_offset(self) -> int:
        with self._lock:
            return int(self._data.get("telegramOffset", 0))

    @telegram_offset.setter
    def telegram_offset(self, value: int) -> None:
        with self._lock:
            self._data["telegramOffset"] = int(value)
            self._save()
