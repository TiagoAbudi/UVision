from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path


PROJECT_DIR = Path(r"C:\UStudioSmartHome\camera-ai")
PYTHONW = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, "UStudio Vision", 0x10)


def main() -> int:
    if not PYTHONW.is_file():
        show_error(f"Python do projeto não encontrado em:\n{PYTHONW}")
        return 1
    try:
        subprocess.Popen(
            [str(PYTHONW), "-m", "camera_ai.desktop_dashboard"],
            cwd=PROJECT_DIR,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        return 0
    except OSError as exc:
        show_error(f"Não foi possível iniciar o UStudio Vision.\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
