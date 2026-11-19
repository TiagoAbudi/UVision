from __future__ import annotations

import hashlib
import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2


LOGGER = logging.getLogger(__name__)


class VideoExportError(RuntimeError):
    pass


def telegram_video_metadata(path: Path) -> dict[str, int]:
    capture = cv2.VideoCapture(str(path))
    try:
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not all(math.isfinite(value) and value > 0 for value in (width, height, fps, count)):
            raise VideoExportError("Não foi possível validar as dimensões e a duração do vídeo para o Telegram.")
        return {"width": int(width), "height": int(height), "duration": math.ceil(count / fps)}
    finally:
        capture.release()


def prepare_telegram_video(source: Path, cache_dir: Path) -> Path:
    """Export a mobile-compatible copy without modifying the local recording."""
    info = source.stat()
    fingerprint = hashlib.sha256(
        f"h264-v1\0{source.resolve()}\0{info.st_size}\0{info.st_mtime_ns}".encode()
    ).hexdigest()[:16]
    target = cache_dir / f"{source.stem}-{fingerprint}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return target
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VideoExportError("FFmpeg não encontrado; instale-o no PATH para enviar vídeos compatíveis com o celular.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".export-", suffix=".mp4", delete=False) as output:
        temporary = Path(output.name)
    try:
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
                "-threads", "2", "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-profile:v", "baseline", "-level:v", "4.1", "-pix_fmt", "yuv420p",
                "-threads", "2", "-movflags", "+faststart", str(temporary),
            ],
            capture_output=True, timeout=180, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            LOGGER.error("Falha na exportação H.264: %s", result.stderr.decode("utf-8", errors="replace")[-1500:])
            raise VideoExportError("Não foi possível converter o clipe para H.264; consulte o diagnóstico local.")
        temporary.replace(target)
        return target
    except subprocess.TimeoutExpired:
        raise VideoExportError("A conversão do clipe excedeu 180 segundos; o vídeo original foi preservado.") from None
    except OSError:
        raise VideoExportError("Não foi possível exportar o vídeo; confira o FFmpeg e o espaço disponível em disco.") from None
    finally:
        temporary.unlink(missing_ok=True)
