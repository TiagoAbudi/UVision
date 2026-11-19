from __future__ import annotations

from datetime import datetime
from html import escape


def event_caption(event: dict, *, video: bool = False) -> str:
    occurred = datetime.fromisoformat(event["createdAt"]).astimezone()
    title = "🧪 Teste · possível queda" if event.get("shadow") else "⚠️ Possível queda"
    camera = str(event.get("cameraName", "Câmera")).removesuffix(" (teste recuperado)")[:80]
    lines = [f"<b>{title}</b>", f"{escape(camera)} · CH {event['channel']:02d}", occurred.strftime("%d/%m/%Y · %H:%M:%S")]
    if event["status"] == "acknowledged":
        received = event.get("acknowledgedAt")
        suffix = " às " + datetime.fromisoformat(received).astimezone().strftime("%H:%M") if received else ""
        lines.extend(("", "✓ Recebimento confirmado" + suffix))
    else:
        lines.extend(("", "Aguardando confirmação." if video else "Vídeo em preparação."))
    return "\n".join(lines)


def reminder_text(event: dict) -> str:
    occurred = datetime.fromisoformat(event["createdAt"]).astimezone()
    title = "⏳ Lembrete de teste" if event.get("shadow") else "⏳ Confirmação pendente"
    camera = str(event.get("cameraName", "Câmera")).removesuffix(" (teste recuperado)")[:80]
    return f"<b>{title}</b>\n{escape(camera)} · CH {event['channel']:02d} · {occurred:%H:%M}\nConfira o evento e confirme o recebimento."
