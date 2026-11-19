"""Validate public Markdown links and obvious sensitive patterns without loading secrets."""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_REPORTS = {"UI-2.0.md", "UI-3.0.md"}
LINK = re.compile(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)")


def public_documents(root: Path) -> list[Path]:
    return sorted([*root.glob("*.md"), *(p for p in (root / "docs").rglob("*.md") if p.name not in PRIVATE_REPORTS)])


def anchors(text: str) -> set[str]:
    result, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, flags=re.MULTILINE):
        slug = re.sub(r"<[^>]+>", "", heading).lower()
        slug = re.sub(r"[^\w\s-]", "", html.unescape(slug)).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug if not count else f"{slug}-{count}")
    return result


def check(root: Path) -> tuple[list[str], dict]:
    errors, links, diagrams, images = [], 0, 0, set()
    sensitive = {
        "token com formato Telegram": r"\b\d{5,}:[A-Za-z0-9_-]{15,}\b",
        "caminho de usuario Windows": r"(?i)[A-Z]:\\Users\\[^\s`]+",
        "chave privada": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "endereco privado da instalacao": r"\b192\.168\.18\.\d{1,3}\b",
    }
    documents = public_documents(root)
    for path in documents:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for label, pattern in sensitive.items():
            if re.search(pattern, text):
                errors.append(f"{relative}: revisar {label}")
        for raw in LINK.findall(text):
            parsed = urlsplit(raw.strip("<>"))
            if parsed.scheme or parsed.netloc:
                continue
            links += 1
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            if not target.is_relative_to(root):
                errors.append(f"{relative}: link fora do projeto")
            elif not target.exists():
                errors.append(f"{relative}: destino ausente {parsed.path}")
            elif parsed.fragment and target.suffix == ".md" and unquote(parsed.fragment) not in anchors(target.read_text(encoding="utf-8")):
                errors.append(f"{relative}: ancora ausente {parsed.fragment}")
            if target.suffix.lower() in {".png", ".gif", ".svg", ".webp"}:
                images.add(str(target))
        for diagram in re.findall(r"```mermaid\s*\n(.*?)```", text, re.DOTALL):
            diagrams += 1
            if not re.match(r"\s*(flowchart|sequenceDiagram|stateDiagram-v2)\b", diagram):
                errors.append(f"{relative}: cabecalho Mermaid desconhecido")
            if diagram.count("[") != diagram.count("]") or diagram.count('"') % 2:
                errors.append(f"{relative}: delimitadores Mermaid inconsistentes")
    return errors, {"documents": len(documents), "relativeLinks": links, "images": len(images), "mermaidBlocks": diagrams}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors, stats = check(args.root.resolve())
    for key, value in stats.items():
        print(f"{key}: {value}")
    for error in errors:
        print(f"ERRO: {error}")
    if not errors:
        print("Links, imagens, ancoras e verificacoes basicas: OK. Renderize Mermaid separadamente.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
