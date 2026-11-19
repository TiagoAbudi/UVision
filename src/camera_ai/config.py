from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_DIR.parent


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Valor booleano inválido: {value!r}")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"A variável {name} deve ser preenchida no .env da raiz.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    camera_url: str
    camera_user: str
    camera_password: str
    yolo_model: str = "yolo26n.pt"
    yolo_device: str = "auto"
    yolo_image_size: int = 640
    confidence_threshold: float = 0.50
    presence_confirm_frames: int = 3
    absence_confirm_frames: int = 10
    detection_cooldown: float = 30.0
    display_enabled: bool = True
    snapshot_on_event: bool = False
    light_automation_enabled: bool = False
    light_command_timeout: float = 15.0
    reconnect_delay: float = 2.0
    max_read_failures: int = 5

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(WORKSPACE_DIR / ".env", override=False)
        load_dotenv(PROJECT_DIR / ".env", override=True)

        settings = cls(
            camera_url=_required("CAMERA_URL"),
            camera_user=_required("CAMERA_USER"),
            camera_password=_required("CAMERA_PASSWORD"),
            yolo_model=os.getenv("YOLO_MODEL", "yolo26n.pt").strip(),
            yolo_device=os.getenv("YOLO_DEVICE", "auto").strip(),
            yolo_image_size=int(os.getenv("YOLO_IMAGE_SIZE", "640")),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.50")),
            presence_confirm_frames=int(os.getenv("PRESENCE_CONFIRM_FRAMES", "3")),
            absence_confirm_frames=int(os.getenv("ABSENCE_CONFIRM_FRAMES", "10")),
            detection_cooldown=float(os.getenv("DETECTION_COOLDOWN", "30")),
            display_enabled=_as_bool(os.getenv("DISPLAY_ENABLED"), True),
            snapshot_on_event=_as_bool(os.getenv("SNAPSHOT_ON_EVENT"), False),
            light_automation_enabled=_as_bool(os.getenv("LIGHT_AUTOMATION_ENABLED"), False),
            light_command_timeout=float(os.getenv("LIGHT_COMMAND_TIMEOUT", "15")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlsplit(self.camera_url)
        if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
            raise ValueError("CAMERA_URL deve ser uma URL RTSP válida e sem credenciais.")
        if parsed.username or parsed.password:
            raise ValueError("Mantenha usuário e senha separados da CAMERA_URL.")
        if not 0.0 < self.confidence_threshold <= 1.0:
            raise ValueError("CONFIDENCE_THRESHOLD deve estar entre 0 e 1.")
        if self.presence_confirm_frames < 1 or self.absence_confirm_frames < 1:
            raise ValueError("Os limites de confirmação devem ser maiores que zero.")
        if self.detection_cooldown < 0:
            raise ValueError("DETECTION_COOLDOWN não pode ser negativo.")
        if self.light_command_timeout <= 0:
            raise ValueError("LIGHT_COMMAND_TIMEOUT deve ser maior que zero.")

    def authenticated_camera_url(self) -> str:
        parsed = urlsplit(self.camera_url)
        credentials = f"{quote(self.camera_user, safe='')}:{quote(self.camera_password, safe='')}@"
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{credentials}{host}{port}", parsed.path, parsed.query, parsed.fragment))

    @property
    def logs_dir(self) -> Path:
        return PROJECT_DIR / "logs"

    @property
    def snapshots_dir(self) -> Path:
        return PROJECT_DIR / "snapshots"

    @property
    def light_script(self) -> Path:
        return WORKSPACE_DIR / "casa.js"

