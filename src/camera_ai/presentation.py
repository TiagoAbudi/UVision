"""Local presentation adapter. Capture, inference and event services remain shared."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from camera_ai.event_store import EventStore
from camera_ai.ui_components import WorkspaceLayout
from camera_ai.web_dashboard import PROJECT_DIR, DVRSettings, DashboardState, _draw_person_boxes

LOGGER = logging.getLogger("camera_ai.presentation")


def clip_path(root: Path, name: str) -> Path:
    target = root / "recordings" / "falls" / EventStore.filename(name)
    if not target.is_file():
        raise ValueError("Gravação não encontrada.")
    return target


def encode_image(frame, width: int = 1920) -> bytes:
    scale = min(1, width / frame.shape[1])
    if scale < 1:
        frame = cv2.resize(frame, (round(frame.shape[1]*scale), round(frame.shape[0]*scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise OSError("Não foi possível preparar a imagem.")
    return encoded.tobytes()


class PresentationBridge(QObject):
    stateChanged = Signal(str)
    response = Signal(str, str)
    initialized = Signal(object)
    closingFinished = Signal()
    windowAction = Signal(str)

    ACTIONS = {"bootstrap", "visible", "layout", "reconnect", "discover", "recordings", "review", "protect",
               "playerOpen", "playerPlay", "playerSeek", "playerSpeed", "openFile", "openFolder", "acknowledge",
               "telegramSave", "telegramTest", "telegramFind", "profile", "calibrationLoad", "calibrationSave", "ptz", "fullscreen"}

    def __init__(self, root: Path = PROJECT_DIR, *, start_backend: bool = True, backend=None, parent=None):
        super().__init__(parent)
        self.root, self.backend = root, backend
        self.store = backend.event_store if backend else EventStore(root/"runtime/events.json")
        self._commands = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-ui-commands")
        self._media = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-ui-images")
        self._ptz = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-ui-ptz")
        self._lock = threading.RLock()
        self._images: dict[str, bytes] = {}
        self._visible: list[dict] = []
        self._sequences = {}
        self._encoding = False
        self._stopped = False
        self._startup_error = ""
        self._last_frame_error = 0.0
        self._history = deque(maxlen=120)
        self._catalog: list[dict] = []
        self._catalog_stamp = None
        self._player = None
        self._player_lock = threading.RLock()
        self.player = {"file": None, "playing": False, "position": 0, "duration": 0, "fps": 0, "speed": 1, "frame": 0}
        try:
            self.layout = WorkspaceLayout.load(root/"runtime/ui-layout.json")
        except (OSError, ValueError):
            self.layout = WorkspaceLayout()
            self._startup_error = "Preferências visuais inválidas. O arquivo foi preservado; organize novamente o mosaico para substituí-las."
        self.initialized.connect(self._ready)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self.publish)
        self._status_timer.start(1000)
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._schedule_frames)
        self._frame_timer.start(33)
        if start_backend:
            self._commands.submit(self._start)

    def _start(self):
        try:
            backend = DashboardState(DVRSettings.load(), native=True, compose_mosaic=False, event_store=self.store)
            backend.start()
            self.backend = backend
            self.initialized.emit(backend)
        except Exception as exc:
            LOGGER.error("Inicialização indisponível (%s)", type(exc).__name__)
            self._startup_error = "Não foi possível iniciar o monitoramento. Consulte o diagnóstico local."

    @Slot(object)
    def _ready(self, backend):
        self.backend = backend
        if self._stopped:
            return
        else:
            self.publish()

    @Slot(str, str, str)
    def request(self, request_id: str, action: str, raw: str):
        if self._stopped:
            return
        if len(request_id) > 64 or action not in self.ACTIONS or len(raw) > 262144:
            self.response.emit(request_id[:64], json.dumps({"error": "Solicitação inválida."}))
            return
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Dados inválidos.")
            if action == "bootstrap":
                self.response.emit(request_id, json.dumps({"data": self.snapshot()}, ensure_ascii=False))
                return
            if action == "visible":
                channels = payload.get("channels", [])
                if not isinstance(channels, list) or len(channels) > 9:
                    raise ValueError("Lista de câmeras inválida.")
                normalized = []
                for item in channels:
                    channel, width = item.get("channel"), item.get("width")
                    if type(channel) is not int or not 1 <= channel <= 9 or type(width) is not int or not 160 <= width <= 1920:
                        raise ValueError("Dimensões de visualização inválidas.")
                    normalized.append({"channel": channel, "width": width})
                with self._lock:
                    self._visible = normalized
                self.response.emit(request_id, '{"data":true}')
                return
            if action == "fullscreen":
                self.windowAction.emit("fullscreen")
                self.response.emit(request_id, '{"data":true}')
                return
        except (ValueError, TypeError, AttributeError):
            self.response.emit(request_id, '{"error":"Dados inválidos."}')
            return
        worker = self._ptz if action == "ptz" else self._media if action in {"recordings", "playerOpen", "playerPlay", "playerSeek", "playerSpeed"} else self._commands
        worker.submit(self._execute, request_id, action, payload)

    def _execute(self, request_id, action, payload):
        try:
            data = self.execute(action, payload)
            result = {"data": data}
        except ValueError as exc:
            result = {"error": str(exc)}
        except Exception as exc:
            LOGGER.error("Ação %s indisponível (%s)", action, type(exc).__name__)
            result = {"error": "Não foi possível concluir a ação. A configuração anterior foi preservada quando aplicável; confira o diagnóstico."}
        self.response.emit(request_id, json.dumps(result, ensure_ascii=False))

    def execute(self, action: str, payload: dict):
        if action == "layout":
            layout = WorkspaceLayout(payload["mode"], tuple(payload["order"]), payload["collapsed"])
            if layout.mode not in {"auto", "1x1", "2x2", "3x3", "4x4"} or type(layout.collapsed) is not bool or any(type(n) is not int for n in layout.order) or sorted(layout.order) != list(range(1, 10)):
                raise ValueError("Organização inválida.")
            layout.save(self.root/"runtime/ui-layout.json")
            self.layout = layout
            return True
        if action == "recordings":
            return self.recordings()
        if action in {"review", "protect", "playerOpen", "openFile"}:
            path = clip_path(self.root, payload["file"])
            if action == "review":
                self.store.review(path, payload["label"], protected=True)
                return self.recordings()
            if action == "protect":
                if type(payload["enabled"]) is not bool:
                    raise ValueError("Estado de preservação inválido.")
                self.store.protect(path, payload["enabled"])
                return self.recordings()
            if action == "openFile":
                import os
                os.startfile(path)
                return True
            return self.open_player(path)
        if action == "openFolder":
            import os
            targets = {"recordings": self.root/"recordings/falls", "logs": self.root/"logs", "project": self.root, "guide": self.root/"README.md"}
            target = targets.get(payload.get("target"))
            if target is None or not target.exists():
                raise ValueError("Destino indisponível.")
            os.startfile(target)
            return True
        if action in {"playerPlay", "playerSeek", "playerSpeed"}:
            with self._player_lock:
                if not self._player:
                    raise ValueError("Selecione uma gravação.")
                if action == "playerPlay":
                    if type(payload["playing"]) is not bool:
                        raise ValueError("Estado de reprodução inválido.")
                    self.player["playing"] = bool(payload["playing"])
                    self._last_playback = time.monotonic()
                elif action == "playerSeek":
                    seconds = float(payload["seconds"])
                    if not 0 <= seconds <= self.player["duration"]:
                        raise ValueError("Posição inválida.")
                    self._player.set(cv2.CAP_PROP_POS_MSEC, seconds*1000)
                    self._read_player()
                    self._last_playback = time.monotonic()
                else:
                    speed = payload["speed"]
                    if speed not in [.5, 1, 1.5, 2]:
                        raise ValueError("Velocidade inválida.")
                    self.player["speed"] = speed
                    self._last_playback = time.monotonic()
                return dict(self.player)
        if action == "acknowledge":
            self.store.acknowledge(payload["id"])
            return True
        backend = self.backend
        if backend is None:
            raise ValueError("Aguarde a inicialização do monitoramento.")
        if action == "reconnect":
            self._feed(payload).reconnect()
            return True
        if action == "discover":
            backend.recovery.request()
            return True
        from camera_ai.presentation_actions import service_action
        return service_action(self, action, payload)

    def _feed(self, payload):
        channel = payload.get("channel")
        feed = self.backend.feed(channel) if type(channel) is int else None
        if feed is None:
            raise ValueError("Câmera inválida.")
        return feed

    def recordings(self):
        folder = self.root/"recordings/falls"
        paths = sorted(folder.glob("queda-ch*.mp4"), reverse=True)
        stamp = tuple((p.name, p.stat().st_size) for p in paths)
        if stamp != self._catalog_stamp:
            catalog = []
            for path in paths:
                try:
                    channel = int(path.stem.split("_", 1)[0].split("ch")[1])
                    date = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d_%H-%M-%S").astimezone().isoformat()
                except (IndexError, ValueError):
                    continue
                capture = cv2.VideoCapture(str(path))
                fps = capture.get(cv2.CAP_PROP_FPS) or 8
                frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                capture.set(cv2.CAP_PROP_POS_FRAMES, min(round(10*fps), max(0, frames-1)))
                ok, image = capture.read()
                capture.release()
                if ok:
                    self._set_image("thumb/"+path.name, encode_image(image, 288))
                catalog.append({"file": path.name, "channel": channel, "date": date, "size": path.stat().st_size,
                                "duration": frames/fps, "thumbnail": ok})
            self._catalog, self._catalog_stamp = catalog, stamp
        reviews = self.store.reviews()
        return [{**item, "review": reviews.get(item["file"], {}).get("label"), "protected": self.store.is_protected(item["file"])} for item in self._catalog]

    def open_player(self, path):
        with self._player_lock:
            if self._player:
                self._player.release()
            self._player = cv2.VideoCapture(str(path))
            if not self._player.isOpened():
                self._player.release()
                self._player = None
                raise ValueError("Não foi possível abrir esta gravação.")
            fps = self._player.get(cv2.CAP_PROP_FPS) or 8
            self.player.update(file=path.name, playing=False, position=0, duration=self._player.get(cv2.CAP_PROP_FRAME_COUNT)/fps, fps=fps, frame=0)
            self._read_player()
            self._last_playback = time.monotonic()
            return dict(self.player)

    def _read_player(self):
        ok, image = self._player.read()
        if ok:
            self._set_image("player", encode_image(image))
            self.player["position"] = max(0, (self._player.get(cv2.CAP_PROP_POS_FRAMES)-1)/self.player["fps"])
            self.player["frame"] += 1
        else:
            self.player["playing"] = False

    def image(self, key: str) -> bytes | None:
        with self._lock:
            return self._images.get(key)

    def _set_image(self, key, value):
        with self._lock:
            self._images[key] = value

    def _schedule_frames(self):
        if not self._encoding and not self._stopped:
            self._encoding = True
            self._media.submit(self._frames)

    def _frames(self):
        try:
            with self._lock:
                visible = list(self._visible)
            if self.backend:
                for item in visible:
                    feed = self.backend.feed(item["channel"])
                    if feed is None:
                        continue
                    frame, sequence, detections, width, height, online = feed.display_snapshot()
                    key = str(feed.channel)
                    identity = (sequence, item["width"])
                    if frame is None or not online or self._sequences.get(key) == identity:
                        continue
                    aspect = 16/9 if width == 960 and height == 1080 and feed.channel <= self.backend.settings.channel_count else width/max(1, height)
                    image = cv2.resize(frame, (item["width"], max(1, round(item["width"]/aspect))), interpolation=cv2.INTER_AREA)
                    _draw_person_boxes(image, detections, width, height)
                    self._set_image(key, encode_image(image))
                    self._sequences[key] = identity
            with self._player_lock:
                if self._player and self.player["playing"]:
                    now = time.monotonic()
                    interval = 1/(self.player["fps"]*self.player["speed"])
                    due = min(8, int((now-getattr(self, "_last_playback", now))/interval))
                    if due:
                        for _ in range(due-1):
                            self._player.grab()
                        self._read_player()
                        self._last_playback += due*interval
        except Exception as exc:
            if time.monotonic()-self._last_frame_error >= 30:
                self._last_frame_error = time.monotonic()
                LOGGER.error("Imagem de apresentação indisponível (%s)", type(exc).__name__)
        finally:
            self._encoding = False

    def snapshot(self):
        backend = self.backend
        cameras = [feed.status() for feed in backend.feeds] if backend else []
        ai = backend.detection_worker.status() if backend else {"ready": False, "device": "Inicializando"}
        telegram = backend.notifications.settings if backend else None
        alerts = []
        for event in reversed(self.store.alerts()):
            deliveries = list(event.get("deliveries", {}).values())
            alerts.append({key: event.get(key) for key in ("id", "channel", "cameraName", "createdAt", "status", "shadow", "clip", "acknowledgedAt")})
            alerts[-1].update(delivered=sum(bool(d.get("initialSent")) for d in deliveries), failed=any(d.get("error") for d in deliveries))
        profile = asdict(backend.detection_worker.performance) if backend else None
        choices = [
            {"id": "original", "name": "Qualidade original", "image_size": backend.settings.yolo_image_size, "interval": backend.settings.detection_interval, "analysis_max_side": 0},
            {"id": "balanced", "name": "Equilibrado", "image_size": 640, "interval": .30, "analysis_max_side": 1280},
            {"id": "economy", "name": "Econômico", "image_size": 512, "interval": .50, "analysis_max_side": 960},
        ] if backend else []
        return {"ready": backend is not None, "error": self._startup_error, "cameras": cameras, "ai": ai,
                "layout": asdict(self.layout), "history": list(self._history), "alerts": alerts, "player": dict(self.player),
                "profile": profile, "profileChoices": choices, "recovery": backend.recovery.message if backend else "Inicializando",
                "telegram": {"enabled": telegram.enabled, "configured": bool(telegram.token), "chats": list(telegram.chats),
                             "channels": list(telegram.channels), "allowCalibration": telegram.allow_calibration,
                             "reminderSeconds": telegram.reminder_seconds, "maxReminders": telegram.max_reminders,
                             "message": backend.notifications.message} if telegram else None,
                "environment": {"version": "3.0", "python": "3.12", "backend": "PyTorch / CUDA", "startedAt": backend.started_at if backend else None}}

    def publish(self):
        if self._stopped:
            return
        state = self.snapshot()
        if state["ai"].get("ready"):
            rates = [c["analysisFps"] for c in state["cameras"] if c["online"]]
            self._history.append({"time": time.time(), "latency": state["ai"].get("averageInferenceMs", 0), "fps": sum(rates)/len(rates) if rates else 0})
            state["history"] = list(self._history)
        self.stateChanged.emit(json.dumps(state, ensure_ascii=False))

    def stop(self):
        self._stopped = True
        self._status_timer.stop()
        self._frame_timer.stop()
        def finish():
            self._media.shutdown(wait=True, cancel_futures=True)
            self._ptz.shutdown(wait=True, cancel_futures=True)
            if self.backend:
                self.backend.stop()
            with self._player_lock:
                if self._player:
                    self._player.release()
            self.closingFinished.emit()
        self._commands.submit(finish)
        self._commands.shutdown(wait=False, cancel_futures=False)
