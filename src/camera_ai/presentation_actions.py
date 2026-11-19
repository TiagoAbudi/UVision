"""Validated commands for the embedded UI, delegating to existing services."""
from __future__ import annotations

import json
import math
from pathlib import Path

from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.zones import CameraZones, fit_vertical_point
from camera_ai.notifications import TelegramClient, TelegramSettings
from camera_ai.performance import PerformanceProfile


def validated_regions(value, *, lines=False):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("Quantidade de regiões inválida.")
    for polygon in value:
        if not isinstance(polygon, list) or not (len(polygon) == 2 if lines else 3 <= len(polygon) <= 128):
            raise ValueError("Região incompleta.")
        for point in polygon:
            if not isinstance(point, list) or len(point) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in point):
                raise ValueError("Coordenadas fora da imagem.")
    return value


def save_calibration(backend, root: Path, payload: dict):
    channel = payload.get("channel")
    if type(channel) is not int or channel not in backend.fall_monitors:
        raise ValueError("Canal não habilitado para análise de quedas.")
    floor = validated_regions(payload.get("floor"))
    rest = validated_regions(payload.get("rest"))
    lines = validated_regions(payload.get("lines"), lines=True)
    rotation, seconds = payload.get("rotation"), payload.get("seconds")
    if type(rotation) is not int or rotation not in (0, 90, 180, 270) or type(seconds) not in (int, float) or not 1 <= seconds <= 30:
        raise ValueError("Orientação ou tempo no chão inválido.")
    previous = backend.settings.camera_zones(channel)
    if previous and previous.view_changed and len(lines) < 2:
        raise ValueError("O enquadramento mudou. Marque pelo menos duas novas verticais.")
    data = {"channel": channel, "coordinateSystem": "raw_image_normalized_xy", "floor": floor, "rest": rest,
            "rotationDegrees": rotation, "groundConfirmSeconds": seconds}
    target = root/"calibration"/f"ch{channel:02d}-zones.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    CameraZones.load(temporary, channel)
    if lines:
        point = fit_vertical_point(lines)
        settings = backend.settings
        configured = settings.fall_geometry_profiles.get(channel) or (settings.fall_geometry_profile if channel == settings.fall_monitor_channel else "")
        geometry_path = root/(configured or f"calibration/ch{channel:02d}-geometry.json")
        profile = json.loads(geometry_path.read_text(encoding="utf-8")) if geometry_path.exists() else {
            "channel": channel, "coordinateSystem": "raw_image_normalized_xy", "minLegDeviationDegrees": 8,
            "minTorsoDeviationDegrees": 25, "strongLegDeviationDegrees": 30, "strongTorsoDeviationDegrees": 60}
        profile.update(verticalVanishingPoint=list(point), source="manual_vertical_references", validated=False)
        geometry_path.parent.mkdir(parents=True, exist_ok=True)
        geometry_temp = geometry_path.with_suffix(".tmp")
        geometry_temp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        if CameraGeometry.load(geometry_temp).channel != channel:
            raise ValueError("A perspectiva pertence a outro canal.")
        geometry_temp.replace(geometry_path)
    temporary.replace(target)
    backend.reload_calibration(channel)
    return {"message": "Calibração aplicada somente a este canal. Revise os clipes para validar o ambiente."}


def telegram_values(bridge, payload):
    current = bridge.backend.notifications.settings
    channels, chats = payload.get("channels"), payload.get("chats")
    if not isinstance(channels, list) or any(type(n) is not int for n in channels) or not isinstance(chats, list) or any(not isinstance(n, str) for n in chats):
        raise ValueError("Selecione os canais e informe destinatários válidos.")
    if any(type(payload.get(key)) is not bool for key in ("enabled", "allowCalibration")):
        raise ValueError("Estado de integração inválido.")
    if any(type(payload.get(key)) is not int for key in ("reminderSeconds", "maxReminders")):
        raise ValueError("Informe intervalos e quantidades inteiras.")
    settings = TelegramSettings(token=payload.get("token", "").strip() or current.token, chats=tuple(chats), channels=tuple(channels),
                                enabled=payload["enabled"], allow_calibration=payload["allowCalibration"],
                                reminder_seconds=payload["reminderSeconds"], max_reminders=payload["maxReminders"])
    settings.validate()
    return settings


def service_action(bridge, action, payload):
    backend = bridge.backend
    if action == "calibrationLoad":
        feed = bridge._feed(payload)
        if feed.channel not in backend.fall_monitors:
            raise ValueError("Esta câmera não está habilitada para calibração.")
        frame, _ = feed.frame_snapshot()
        if frame is None or not feed.status()["online"]:
            raise ValueError("A câmera está offline. Selecione uma câmera com imagem recente.")
        from camera_ai.presentation import encode_image
        bridge._set_image("calibration", encode_image(frame))
        zones = backend.settings.camera_zones(feed.channel)
        return {"channel": feed.channel, "width": frame.shape[1], "height": frame.shape[0],
                "floor": zones.floors if zones and not zones.view_changed else [], "rest": zones.rest if zones and not zones.view_changed else [],
                "lines": [], "rotation": backend.settings.fall_rotation(feed.channel), "seconds": backend.settings.ground_seconds(feed.channel),
                "perspective": backend.settings.fall_geometry(feed.channel) is not None, "viewChanged": bool(zones and zones.view_changed)}
    if action == "calibrationSave":
        return save_calibration(backend, bridge.root, payload)
    if action == "profile":
        profiles = {"original": PerformanceProfile(backend.settings.yolo_image_size, backend.settings.detection_interval),
                    "balanced": PerformanceProfile(640, .30, 1280), "economy": PerformanceProfile(512, .50, 960)}
        profile = profiles.get(payload.get("id"))
        if profile is None:
            raise ValueError("Perfil de análise inválido.")
        backend.detection_worker.configure_performance(profile)
        return {"message": "Perfil salvo e aplicado. Reavalie a precisão com clipes revisados."}
    if action == "telegramSave":
        backend.notifications.configure(telegram_values(bridge, payload))
        return {"message": "Configuração de alertas salva."}
    if action == "telegramTest":
        settings = telegram_values(bridge, payload)
        settings.validate(require_connection=True)
        client = TelegramClient(settings.token)
        for chat in settings.chats:
            client.call("sendMessage", {"chat_id": chat, "parse_mode": "HTML", "text": "<b>✓ UStudio Vision conectado</b>\nEste chat está pronto para receber avisos.\n<i>Teste de comunicação · nenhum evento de queda.</i>"})
        return {"message": "Teste de comunicação enviado aos destinatários selecionados."}
    if action == "telegramFind":
        if backend.notifications.settings.enabled:
            raise ValueError("Desative o envio e salve antes de buscar chats, para não disputar a conexão do bot.")
        token = payload.get("token", "").strip() or backend.notifications.settings.token
        TelegramSettings(token=token).validate()
        if not token:
            raise ValueError("Preencha o token primeiro.")
        updates = TelegramClient(token).call("getUpdates", {"timeout": 0, "allowed_updates": ["message"], "limit": 100}) or []
        return {"chats": sorted({str(item["message"]["chat"]["id"]) for item in updates if "message" in item})}
    if action == "ptz":
        feed = bridge._feed(payload)
        command = payload.get("command")
        if not feed.supports_ptz or backend.ptz_controller is None or command not in {"left", "right", "up", "down", "home", "stop"}:
            raise ValueError("Comando PTZ indisponível.")
        if command != "stop" and not feed.status()["online"]:
            raise ValueError("Ligue a câmera para usar o PTZ.")
        backend.ptz_controller.control(command)
        if command != "stop" and feed.channel in backend.fall_monitors:
            backend.invalidate_view(feed.channel)
        return True
    raise ValueError("Ação indisponível.")
