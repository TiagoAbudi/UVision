from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QLabel, QLineEdit, QListWidget,
    QScrollArea, QVBoxLayout, QWidget,
)


COLORS = {
    "background": "#0b1018", "surface": "#111925", "raised": "#182332",
    "border": "#253246", "text": "#e8eef8", "muted": "#9aaac0",
    "primary": "#5796ff", "healthy": "#50d2a6", "attention": "#ecc174", "critical": "#ff8291",
}
SPACING = (4, 8, 12, 16, 24, 32)
RADII = (6, 10, 14)


def stylesheet() -> str:
    source = Path(__file__).with_name("desktop_theme.qss").read_text(encoding="utf-8")
    for name, value in COLORS.items():
        source = source.replace("$"+name, value)
    return source.replace("$icons", Path(__file__).with_name("ui-icons").as_posix())


def label(value: str, role: str = "subtitle", *, wrap: bool = False) -> QLabel:
    widget = QLabel(value)
    widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


class ElidedLabel(QLabel):
    def __init__(self, value: str, role: str) -> None:
        super().__init__()
        from PySide6.QtWidgets import QSizePolicy
        self.full_text = value
        self.setObjectName(role)
        self.setToolTip(value)
        self.setAccessibleName(value)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(value)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def setText(self, value: str) -> None:
        self.full_text = value
        self.setToolTip(value)
        self.setAccessibleName(value)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))


class SectionCard(QFrame):
    def __init__(self, title: str, description: str = "") -> None:
        super().__init__()
        self.setObjectName("sectionCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(14)
        if title:
            self.body.addWidget(label(title, "cardTitle"))
        if description:
            self.body.addWidget(label(description, wrap=True))


class EmptyState(QFrame):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = label(title, "emptyTitle", wrap=True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = label(description, wrap=True)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        layout.addWidget(detail)


def scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    return scroll


class CommandPalette(QDialog):
    def __init__(self, commands: list[tuple[str, object]], parent=None) -> None:
        super().__init__(parent)
        self.commands = commands
        self.setWindowTitle("Navegação rápida")
        self.resize(540, 390)
        self.setStyleSheet(stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ir para uma página ou câmera…")
        self.search.setAccessibleName("Pesquisar páginas e câmeras")
        self.results = QListWidget()
        self.results.setObjectName("commandResults")
        layout.addWidget(self.search)
        layout.addWidget(self.results, 1)
        layout.addWidget(label("Enter para abrir · Esc para fechar", "caption"))
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.activate)
        self.results.itemActivated.connect(lambda _item: self.activate())
        self.filter()

    def filter(self) -> None:
        self.results.clear()
        for index, (title, _) in enumerate(self.commands):
            if self.search.text().casefold() in title.casefold():
                self.results.addItem(title)
                self.results.item(self.results.count()-1).setData(Qt.ItemDataRole.UserRole, index)
        if self.results.count():
            self.results.setCurrentRow(0)

    def activate(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            callback = self.commands[item.data(Qt.ItemDataRole.UserRole)][1]
            self.accept()
            callback()


@dataclass
class WorkspaceLayout:
    mode: str = "3x3"
    order: tuple[int, ...] = tuple(range(1, 10))
    collapsed: bool = False

    @classmethod
    def load(cls, path: Path) -> "WorkspaceLayout":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Formato das preferências da interface inválido.")
        mode, order, collapsed = data.get("mode"), data.get("order"), data.get("collapsed")
        if mode not in {"auto", "1x1", "2x2", "3x3", "4x4"}:
            raise ValueError("Layout de câmeras inválido.")
        if not isinstance(order, list) or any(type(value) is not int for value in order) or sorted(order) != list(range(1, 10)):
            raise ValueError("Ordem das câmeras inválida.")
        if type(collapsed) is not bool:
            raise ValueError("Estado da navegação inválido.")
        return cls(mode, tuple(order), collapsed)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"mode": self.mode, "order": self.order, "collapsed": self.collapsed}, indent=2), encoding="utf-8")
        temporary.replace(path)

    def dimensions(self, count: int) -> tuple[int, int]:
        columns = max(1, min(4, math.ceil(math.sqrt(count)))) if self.mode == "auto" else int(self.mode[0])
        return columns, columns*columns
