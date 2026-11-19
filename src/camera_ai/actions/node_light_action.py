from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from camera_ai.events import DetectionEvent


class NodeLightAction:
    """Keeps the existing Tuya light synchronized with person presence."""

    def __init__(self, script: Path, *, timeout: float = 15.0) -> None:
        self.script = script.resolve()
        self.workspace = self.script.parent
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

        if not self.script.is_file():
            raise FileNotFoundError(f"Script da luz não encontrado: {self.script}")
        if shutil.which("node") is None:
            raise RuntimeError("Node.js não foi encontrado no PATH.")

    def handle(self, event: DetectionEvent, _frame) -> None:
        command_by_event = {
            "PERSON_DETECTED": "ligar",
            "PERSON_CLEARED": "desligar",
        }
        light_command = command_by_event.get(event.event_type)
        if light_command is None:
            return

        creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            result = subprocess.run(
                ["node", str(self.script), "luz", light_command],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"O comando da luz excedeu {self.timeout:.0f} segundos.") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "erro sem detalhes").strip()
            raise RuntimeError(f"Falha ao executar luz {light_command}: {detail}")

        action_name = "LIGHT_ON" if light_command == "ligar" else "LIGHT_OFF"
        self.logger.info("%s concluído pelo casa.js", action_name)
