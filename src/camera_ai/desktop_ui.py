from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import re
import threading
import math
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QDate, QEvent, QMimeData, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QFontDatabase, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from camera_ai.web_dashboard import DVRSettings, DashboardState, PROJECT_DIR, _draw_person_boxes
from camera_ai.desktop_dialogs import HealthDialog, PerformanceDialog
from camera_ai.calibration_ui import CalibrationDialog
from camera_ai.event_store import EventStore
from camera_ai.alerts_ui import AlertsDialog
from camera_ai.ui_components import COLORS, CommandPalette, ElidedLabel, EmptyState, SectionCard, WorkspaceLayout, stylesheet


LOGGER = logging.getLogger("camera_ai.desktop")
RECORDINGS = PROJECT_DIR / "recordings" / "falls"
BLUE = "#8cb9ff"
GREEN = "#59d7ad"
MUTED = "#8095b2"


def recording_channel(path: Path) -> int | None:
    match = re.fullmatch(r"queda-ch(\d+)_.*", path.stem)
    return int(match.group(1)) if match else None


PATHS = {
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/>',
    "camera": '<rect x="3" y="6" width="14" height="12" rx="3"/><path d="m17 10 4-3v10l-4-3"/><circle cx="10" cy="12" r="3"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    "shield": '<path d="m12 2 8 3v6c0 5-5 9-8 11-3-2-8-6-8-11V5Z"/><path d="m8 12 3 3 5-6"/>',
    "person": '<circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/><rect x="10" y="10" width="4" height="4" rx="1"/>',
    "expand": '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
    "folder": '<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 8h18"/>',
    "refresh": '<path d="M20 7v-5M20 7h-5M20 7a9 9 0 1 0 1 9"/>',
    "play": '<path d="m8 4 13 8-13 8Z"/>',
    "pause": '<path d="M8 4v16M16 4v16"/>',
    "left": '<path d="m15 5-7 7 7 7"/>',
    "right": '<path d="m9 5 7 7-7 7"/>',
    "up": '<path d="m5 15 7-7 7 7"/>',
    "down": '<path d="m5 9 7 7 7-7"/>',
    "home": '<path d="m3 10 9-8 9 8v11H3Z"/><path d="M9 21v-8h6v8"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
    "bell": '<path d="M5 16h14l-2-3V8a5 5 0 0 0-10 0v5Z"/><path d="M10 20h4"/>',
    "external": '<path d="M14 3h7v7M21 3 10 14M10 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6"/>',
}


@lru_cache(maxsize=128)
def icon(name: str, color: str = MUTED, size: int = 20) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def text(value: str = "", name: str = "", *, wrap: bool = False) -> QLabel:
    label = QLabel(value)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class ActionButton(QPushButton):
    def setToolTip(self, value: str) -> None:
        super().setToolTip(value)
        self.setAccessibleName(value)


def button(title: str, symbol: str | None = None, *, primary: bool = False) -> QPushButton:
    widget = ActionButton(title)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if symbol:
        widget.setIcon(icon(symbol, "#e9f1ff" if primary else BLUE, 17))
        widget.setIconSize(QSize(17, 17))
    if primary:
        widget.setObjectName("primaryButton")
    return widget


def chip(value: str, tone: str = "green") -> QLabel:
    widget = text(value, "chip")
    widget.setProperty("tone", tone)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setFixedHeight(25)
    return widget


def set_tone(widget: QWidget, tone: str) -> None:
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.updateGeometry()


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class VideoSurface(QWidget):
    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, placeholder: str = "Conectando à câmera…") -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image: QImage | None = None
        self.badge = ""
        self.placeholder = placeholder
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_frame(self, frame: np.ndarray) -> None:
        target_width = max(1, round(self.width() * self.devicePixelRatioF()))
        target_height = max(1, round(self.height() * self.devicePixelRatioF()))
        ratio = min(target_width / frame.shape[1], target_height / frame.shape[0], 1.0)
        resized = cv2.resize(frame, (max(1, round(frame.shape[1]*ratio)), max(1, round(frame.shape[0]*ratio))), interpolation=cv2.INTER_AREA) if ratio < 1 else frame
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 5, 5)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QColor("#050a12"))
        if self.image is None:
            painter.setPen(QColor("#596e8b"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
        else:
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2, size.width(), size.height())
            painter.drawImage(target, self.image)
            if self.badge:
                painter.setFont(QFont("Segoe UI", 8))
                width = min(target.width() - 12, painter.fontMetrics().horizontalAdvance(self.badge) + 18)
                badge = QRectF(target.left() + 6, target.bottom() - 25, max(0, width), 20)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(8, 18, 30, 220))
                painter.drawRoundedRect(badge, 4, 4)
                painter.setPen(QColor("#a9bfdc"))
                painter.drawText(badge.adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, self.badge)
        painter.end()

    def mousePressEvent(self, _event) -> None:
        self.setFocus()
        self.clicked.emit()

    def mouseDoubleClickEvent(self, _event) -> None:
        self.doubleClicked.emit()


class CameraTile(QFrame):
    selected = Signal(int)
    moved = Signal(int, int)
    settingsRequested = Signal(int)

    def __init__(self, channel: int) -> None:
        super().__init__()
        self.channel = channel
        self._drag_origin = None
        self.setAcceptDrops(True)
        self.sequence = -1
        self.setObjectName("cameraTile")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("cameraHeader")
        header.setFixedHeight(32)
        self.header = header
        header.installEventFilter(self)
        header.setCursor(Qt.CursorShape.OpenHandCursor)
        header.setToolTip("Arraste o cabeçalho para trocar a posição desta câmera")
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(7)
        camera_icon = QLabel()
        camera_icon.setPixmap(icon("camera", "#7e9ac0", 14).pixmap(14, 14))
        row.addWidget(camera_icon)
        self.title = ElidedLabel(f"Câmera {channel:02d}", "cameraName")
        row.addWidget(self.title, 1)
        self.people = text("", "cameraFooter")
        row.addWidget(self.people)
        self.code_label = text(f"CH {channel:02d}", "cameraCode")
        row.addWidget(self.code_label)
        self.connection = text("Conectando", "cameraState")
        row.addWidget(self.connection)
        layout.addWidget(header)
        self.surface = VideoSurface()
        self.surface.clicked.connect(lambda: self.selected.emit(channel))
        layout.addWidget(self.surface, 1)
        self.actions = QFrame(self.surface)
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(5)
        for symbol, title, callback in (("expand", "Ampliar câmera", lambda: self.selected.emit(self.channel)),
                                        ("settings", "Diagnóstico desta câmera", lambda: self.settingsRequested.emit(self.channel))):
            action = button("", symbol)
            action.setObjectName("tileAction")
            action.setFixedSize(30, 28)
            action.setToolTip(title)
            action.setAccessibleName(title)
            action.clicked.connect(callback)
            actions.addWidget(action)
        self.actions.hide()

    def enterEvent(self, event) -> None:
        self.actions.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.actions.hide()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.actions.adjustSize()
        self.actions.move(max(0, self.surface.width()-self.actions.width()-8), 8)
        self.sequence = -1

    def eventFilter(self, watched, event) -> bool:
        if watched is self.header:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = event.position().toPoint()
            elif event.type() == QEvent.Type.MouseMove and self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
                if (event.position().toPoint()-self._drag_origin).manhattanLength() >= QApplication.startDragDistance():
                    drag = QDrag(self)
                    mime = QMimeData()
                    mime.setData("application/x-uvision-camera", str(self.channel).encode())
                    drag.setMimeData(mime)
                    self._drag_origin = None
                    drag.exec(Qt.DropAction.MoveAction)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin = None
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-uvision-camera"):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        try:
            source = int(bytes(event.mimeData().data("application/x-uvision-camera")))
        except ValueError:
            return
        if 1 <= source <= 9:
            self.moved.emit(source, self.channel)
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-uvision-camera"):
            event.acceptProposedAction()

    def update_status(self, status: dict) -> None:
        self.title.setText(str(status["name"]))
        self.connection.setText("Ao vivo" if status["online"] else "Conectando" if status.get("connectionState") == "connecting" else "Offline")
        self.code_label.setText(f"CH {self.channel:02d}")
        self.connection.setStyleSheet(f"color: {'#55d5a8' if status['online'] else '#e88493'}")
        fall = status.get("fall", {})
        state = fall.get("state", "normal")
        labels = {"possible_fall": "Verificando movimento", "down": "Postura no chão", "alert_pending": "Queda confirmada"}
        self.connection.setToolTip(status.get("connectionMessage", ""))
        if not status["online"]:
            detail = status.get("connectionMessage", "Reconectando…")
        elif fall.get("enabled"):
            detail = ("Recalibre após mover a câmera" if fall.get("calibrationRequired") else
                      labels.get(state, "Perspectiva pendente" if not fall.get("perspectiveGuard") else
                                 "Calibrando quedas" if fall.get("shadowMode") else "Monitor de quedas ativo"))
        else:
            detail = f"AO VIVO  ·  {status.get('fps', 0):.0f} fps"
        self.surface.badge = detail
        if not status["online"]:
            self.surface.placeholder = detail
        self.surface.update()
        self.setToolTip(f"CH {self.channel:02d} · {status['name']}\n{detail}")
        if fall.get("enabled") and not fall.get("perspectiveGuard"):
            self.setToolTip(self.toolTip() + "\nCalibração de perspectiva pendente")
        count = status.get("personCount", 0)
        self.people.setText(f"{count} {'pessoa' if count == 1 else 'pessoas'}" if count else "")
        alert = state == "alert_pending"
        if self.property("alert") != alert:
            self.setProperty("alert", alert)
            self.style().unpolish(self)
            self.style().polish(self)


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, hint: str, symbol: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setFixedHeight(82)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 9, 15, 9)
        column = QVBoxLayout()
        column.setSpacing(0)
        self.title = text(title, "metricLabel")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column.addWidget(self.title)
        self.value = text(value, "metricValue")
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column.addWidget(self.value)
        self.hint = text(hint, "metricHint")
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column.addWidget(self.hint)
        layout.addLayout(column, 1)
        image = text("", "metricIcon")
        self.image = image
        image.setPixmap(icon(symbol, BLUE, 21).pixmap(21, 21))
        layout.addWidget(image)


class RecordingRow(QWidget):
    def __init__(self, path: Path, classification: str, camera_label: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(9, 9, 9, 9)
        row.setSpacing(11)
        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(112, 67)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setStyleSheet("background:#080f19;border-radius:5px;")
        self.thumbnail.setPixmap(icon("film", "#4d6688", 25).pixmap(25, 25))
        row.addWidget(self.thumbnail)
        info = QVBoxLayout()
        info.setSpacing(4)
        try:
            occurred = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d_%H-%M-%S")
            title = occurred.strftime("%H:%M:%S  ·  %d/%m/%Y")
        except (IndexError, ValueError):
            title = path.stem
        info.addWidget(ElidedLabel(title, "recordingTitle"))
        info.addWidget(ElidedLabel(f"{camera_label}  ·  {path.stat().st_size / 1_048_576:.1f} MB", "recordingSub"))
        info.addWidget(ElidedLabel(classification, "recordingSub"))
        row.addLayout(info, 1)


class ProfessionalWindow(QMainWindow):
    def __init__(self, *, start_backend: bool = True) -> None:
        super().__init__()
        self.backend: DashboardState | None = None
        self._events: queue.Queue = queue.Queue()
        self._closing = False
        self._backend_starting = start_backend
        self._view = "mosaic"
        self._tool_pages = {}
        self._tool_widgets = {}
        self._grid_page = 0
        self._player_focus = False
        self._playback_speed = 1.0
        self._layout_path = PROJECT_DIR / "runtime" / "ui-layout.json"
        self._layout_error = ""
        try:
            self.workspace_layout = WorkspaceLayout.load(self._layout_path)
        except (OSError, ValueError) as exc:
            self.workspace_layout = WorkspaceLayout()
            self._layout_error = str(exc)
            LOGGER.warning("Layout da interface não carregado: %s", exc)
        self._selected_channel = 0
        self._fullscreen = False
        self._ptz_action: str | None = None
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vision-media")
        self._ptz_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-ptz")
        self._paths: list[Path] = []
        self._recording_rows: dict[Path, RecordingRow] = {}
        self._thumbnail_cache: dict[Path, np.ndarray] = {}
        self._pending_thumbnails: set[Path] = set()
        self._player: cv2.VideoCapture | None = None
        self._playing = False
        self._current_clip: Path | None = None
        self._clip_fps = 8.0
        self._clip_frames = 0
        self._clip_position = 0
        self._classifications: dict[str, bool | str] = {}
        self.event_store = EventStore(PROJECT_DIR / "runtime" / "events.json")
        self._last_refresh = 0
        self.setWindowTitle("UStudio Vision Control 2.0")
        self.setWindowIcon(icon("shield", "#69a5ff", 64))
        self.resize(1500, 940)
        self.setMinimumSize(960, 620)
        self.setStyleSheet(stylesheet())
        self._build()
        self._install_shortcuts()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_events)
        self._poll_timer.start(80)
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render)
        self._render_timer.start(66)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._player_timer = QTimer(self)
        self._player_timer.timeout.connect(self._video_tick)
        self._tick_clock()
        self._load_classifications()
        self._refresh_recordings()
        if self._layout_error:
            self.notify("Não foi possível restaurar o layout. O arquivo existente foi preservado.", error=True)
        if start_backend:
            threading.Thread(target=self._start_backend, name="vision-start", daemon=True).start()

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.sidebar = self._build_sidebar()
        shell.addWidget(self.sidebar)
        main = QVBoxLayout()
        main.setSpacing(0)
        shell.addLayout(main, 1)
        self.topbar = self._build_topbar()
        main.addWidget(self.topbar)
        workspace = QVBoxLayout()
        workspace.setContentsMargins(20, 14, 20, 14)
        workspace.setSpacing(12)
        main.addLayout(workspace, 1)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        headings = QVBoxLayout()
        headings.setSpacing(3)
        self.page_title = text("Visão geral", "pageTitle")
        self.page_subtitle = text("Todas as câmeras, em um só lugar.", "subtitle")
        headings.addWidget(self.page_title)
        headings.addWidget(self.page_subtitle)
        heading.addLayout(headings, 1)
        self.back_button = button("Visão geral", "grid")
        self.back_button.clicked.connect(self.show_mosaic)
        self.back_button.hide()
        heading.addWidget(self.back_button)
        self.layout_button = button("3 × 3", "grid")
        self.layout_button.setObjectName("layoutButton")
        layout_menu = QMenu(self.layout_button)
        for title, mode in (("Automático", "auto"), ("1 × 1", "1x1"), ("2 × 2", "2x2"), ("3 × 3", "3x3"), ("4 × 4", "4x4")):
            layout_menu.addAction(title, lambda checked=False, value=mode: self.set_grid_layout(value))
        self.layout_button.setMenu(layout_menu)
        self.page_previous = button("", "left")
        self.page_previous.setToolTip("Câmeras anteriores")
        self.page_previous.clicked.connect(lambda: self.change_grid_page(-1))
        self.page_next = button("", "right")
        self.page_next.setToolTip("Próximas câmeras")
        self.page_next.clicked.connect(lambda: self.change_grid_page(1))
        self.grid_page_label = text("", "caption")
        heading.addWidget(self.page_previous)
        heading.addWidget(self.grid_page_label)
        heading.addWidget(self.page_next)
        heading.addWidget(self.layout_button)
        expand = button("", "expand")
        expand.setObjectName("iconButton")
        expand.setToolTip("Tela cheia · F11")
        expand.clicked.connect(self._toggle_fullscreen)
        heading.addWidget(expand)
        self.heading_widget = QWidget()
        self.heading_widget.setLayout(heading)
        workspace.addWidget(self.heading_widget)
        self.metrics_row = QHBoxLayout()
        self.metrics_row.setSpacing(11)
        self.online_card = MetricCard("Câmeras conectadas", "— / 9", "Conectando ao DVR", "camera")
        self.people_card = MetricCard("Pessoas no ambiente", "—", "Análise em tempo real", "person")
        self.fall_card = MetricCard("Monitor de quedas", "Preparando", "Calibração em todas as câmeras", "shield")
        self.recordings_card = MetricCard("Clipes de eventos", "0", "Armazenados neste computador", "film")
        self.fall_card.value.setStyleSheet("font-size:18px;color:#79d9ba;font-weight:600;")
        for card in (self.online_card, self.people_card, self.fall_card, self.recordings_card):
            self.metrics_row.addWidget(card, 1)
        self.metrics_widget = QWidget()
        self.metrics_widget.setLayout(self.metrics_row)
        workspace.addWidget(self.metrics_widget)
        self.alert_banner = button("", "bell")
        self.alert_banner.setStyleSheet("background:#3b2430;color:#ffc4ce;border-color:#87485c;text-align:left;padding:10px;")
        self.alert_banner.clicked.connect(self.show_alerts)
        self.alert_banner.hide()
        workspace.addWidget(self.alert_banner)
        self.pages = QStackedWidget()
        self.pages.setMinimumSize(0, 0)
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        workspace.addWidget(self.pages, 1)
        mosaic = QWidget()
        self.mosaic_page = mosaic
        grid = QGridLayout(mosaic)
        self.camera_grid = grid
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(9)
        self.tiles = []
        for index in range(9):
            tile = CameraTile(index + 1)
            tile.selected.connect(self.select_camera)
            tile.moved.connect(self.reorder_camera)
            tile.settingsRequested.connect(self.show_camera_health)
            grid.addWidget(tile, index // 3, index % 3)
            self.tiles.append(tile)
        for index in range(3):
            grid.setRowStretch(index, 1)
            grid.setColumnStretch(index, 1)
        self.pages.addWidget(mosaic)
        self.single_tile = CameraTile(1)
        self.single_tile.surface.doubleClicked.connect(self.show_mosaic)
        self.pages.addWidget(self.single_tile)
        self.pages.addWidget(self._build_library())
        self.ptz_panel = self._build_ptz()
        self.statusbar_widget = self._build_statusbar()
        main.addWidget(self.statusbar_widget)
        self._build_device_drawer()
        self.toast = text("", "toast", wrap=True)
        self.toast.setParent(self)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        self._reflow_grid()
        self._apply_sidebar_state()
        self._refresh_navigation()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        column = QVBoxLayout(sidebar)
        column.setContentsMargins(12, 12, 12, 14)
        column.setSpacing(5)
        brand = QFrame()
        brand.setObjectName("brand")
        row = QHBoxLayout(brand)
        row.setContentsMargins(6, 10, 0, 20)
        logo = QLabel()
        logo.setPixmap(icon("shield", BLUE, 30).pixmap(30, 30))
        row.addWidget(logo)
        self.brand_names = QWidget()
        names = QVBoxLayout(self.brand_names)
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(1)
        names.addWidget(text("USTUDIO", "brandName"))
        names.addWidget(text("VISION CONTROL", "brandSub"))
        row.addWidget(self.brand_names)
        column.addWidget(brand)
        self.nav_buttons = {}
        self.nav_labels = {}
        self.nav_sections = []
        groups = [
            ("MONITORAMENTO", [("mosaic", "Visão geral", "grid", self.show_mosaic)]),
            ("EVENTOS", [("library", "Eventos e gravações", "film", self.show_library),
                        ("alerts", "Alertas", "bell", self.show_alerts)]),
            ("INTELIGÊNCIA", [("calibration", "Calibração", "settings", self.show_calibration),
                             ("performance", "Desempenho da IA", "chip", self.show_performance)]),
            ("DISPOSITIVOS", [("health", "Câmeras e diagnóstico", "camera", self.show_health)]),
            ("SISTEMA", [("about", "Sobre a central", "shield", self.show_about)]),
        ]
        for title, entries in groups:
            section = text(title, "sectionLabel")
            section.setContentsMargins(10, 15, 0, 5)
            self.nav_sections.append(section)
            column.addWidget(section)
            for key, title, symbol, callback in entries:
                item = button("  "+title, symbol)
                item.setObjectName("navButton")
                item.setCheckable(True)
                item.setToolTip(title)
                item.setAccessibleName(title)
                item.clicked.connect(callback)
                self.nav_buttons[key] = item
                self.nav_labels[key] = title
                column.addWidget(item)
        self.monitor_nav = self.nav_buttons["mosaic"]
        self.recording_nav = self.nav_buttons["library"]
        self.tools_nav = button("", "settings")
        self.tools_nav.setObjectName("iconButton")
        self.tools_nav.setToolTip("Acesso rápido às ferramentas")
        menu = QMenu(self.tools_nav)
        for title, callback in (("Diagnóstico das câmeras", self.show_health), ("Calibração por ambiente", self.show_calibration),
                                ("Alertas e recebimentos", self.show_alerts), ("Desempenho da análise", self.show_performance)):
            menu.addAction(title, callback)
        self.tools_nav.setMenu(menu)
        column.addStretch()
        self.sidebar_state = text("●  Estação local", "caption")
        self.sidebar_state.setContentsMargins(8, 10, 0, 8)
        column.addWidget(self.sidebar_state)
        self.collapse_button = button("  Recolher navegação", "left")
        self.collapse_button.setToolTip("Recolher ou expandir navegação")
        self.collapse_button.clicked.connect(self.toggle_sidebar)
        column.addWidget(self.collapse_button)
        self.protection_label = self.sidebar_state
        return sidebar

    def _build_device_drawer(self) -> None:
        self.device_drawer = QFrame(self)
        self.device_drawer.setObjectName("sectionCard")
        layout = QVBoxLayout(self.device_drawer)
        layout.setContentsMargins(14, 16, 14, 16)
        heading = QHBoxLayout()
        heading.addWidget(text("Suas câmeras", "cardTitle"))
        close = button("", "right")
        close.setToolTip("Fechar lista de câmeras")
        close.clicked.connect(self.device_drawer.hide)
        heading.addWidget(close)
        layout.addLayout(heading)
        self.camera_buttons = []
        for channel in range(1, 10):
            item = button(f"  {channel:02d}   Conectando…", "camera")
            item.setObjectName("cameraNav")
            item.setCheckable(True)
            item.clicked.connect(lambda checked=False, value=channel: self.select_camera(value))
            layout.addWidget(item)
            self.camera_buttons.append(item)
        detail = button("Diagnóstico dos dispositivos", "settings")
        detail.clicked.connect(self.show_health)
        layout.addWidget(detail)
        self.device_drawer.hide()

    def toggle_devices(self) -> None:
        self.device_drawer.setVisible(not self.device_drawer.isVisible())
        self.device_drawer.setGeometry(max(0, self.width()-300), 62, 280, min(540, self.height()-100))
        self.device_drawer.raise_()

    def toggle_sidebar(self) -> None:
        self.workspace_layout.collapsed = not self.workspace_layout.collapsed
        self._apply_sidebar_state()
        self._save_workspace()

    def _apply_sidebar_state(self) -> None:
        collapsed = self.workspace_layout.collapsed or self.width() < 1180
        self.sidebar.setFixedWidth(64 if collapsed else 218)
        self.brand_names.setVisible(not collapsed)
        self.sidebar_state.setVisible(not collapsed)
        for section in self.nav_sections:
            section.setVisible(not collapsed)
        for key, item in self.nav_buttons.items():
            item.setText("" if collapsed else "  "+self.nav_labels[key])
        self.collapse_button.setText("" if collapsed else "  Recolher navegação")
        self.collapse_button.setIcon(icon("right" if collapsed else "left", BLUE, 17))
        compact = self.height() < 760
        for section in self.nav_sections:
            section.setContentsMargins(10, 5 if compact else 15, 0, 2 if compact else 5)
        if hasattr(self, "search_button"):
            self.search_button.setText("" if self.width() < 1300 else "Buscar…   Ctrl+K")
            self.clock.setVisible(self.width() >= 1180)
        if hasattr(self, "online_card"):
            for card in (self.online_card, self.people_card, self.fall_card, self.recordings_card):
                card.setFixedHeight(68 if compact else 82)
                card.hint.setVisible(not compact)
                card.image.setVisible(self.width() >= 1180)
        if hasattr(self, "folder_button"):
            self.library_catalog.setFixedWidth(360 if self.width() >= 1500 else 310 if self.width() >= 1180 else 290)
            self.external_button.setText("" if self.width() < 1200 else "Abrir vídeo")
            self.folder_button.setText("" if self.width() < 1200 else "Pasta de gravações")

    def _save_workspace(self) -> None:
        try:
            self.workspace_layout.save(self._layout_path)
        except OSError:
            self.notify("Não foi possível salvar a organização da interface.", error=True)

    def set_grid_layout(self, mode: str) -> None:
        self.workspace_layout.mode = mode
        self._grid_page = 0
        self.show_mosaic()
        self._save_workspace()

    def change_grid_page(self, direction: int) -> None:
        self._grid_page += direction
        self._reflow_grid()

    def reorder_camera(self, source: int, target: int) -> None:
        order = list(self.workspace_layout.order)
        if source in order and target in order:
            first, second = order.index(source), order.index(target)
            order[first], order[second] = order[second], order[first]
            self.workspace_layout.order = tuple(order)
            self._reflow_grid()
            self._save_workspace()
            self.notify("Disposição das câmeras salva.")

    def _reflow_grid(self) -> None:
        channels = {feed.channel for feed in self.backend.feeds} if self.backend is not None else set(range(1, 10))
        count = len(channels)
        order = [channel for channel in self.workspace_layout.order if channel in channels]
        columns, capacity = self.workspace_layout.dimensions(count)
        pages = max(1, math.ceil(len(order)/capacity))
        self._grid_page = max(0, min(self._grid_page, pages-1))
        visible = order[self._grid_page*capacity:(self._grid_page+1)*capacity]
        while self.camera_grid.count():
            item = self.camera_grid.takeAt(0)
            item.widget().hide()
        for index in range(9):
            self.camera_grid.setRowStretch(index, 0)
            self.camera_grid.setColumnStretch(index, 0)
        rows = max(1, math.ceil(len(visible)/columns)) if self.workspace_layout.mode == "auto" else columns
        for index, channel in enumerate(visible):
            tile = self.tiles[channel-1]
            self.camera_grid.addWidget(tile, index//columns, index%columns)
            tile.sequence = -1
            tile.show()
        for row in range(rows):
            self.camera_grid.setRowStretch(row, 1)
        for column in range(columns):
            self.camera_grid.setColumnStretch(column, 1)
        self.layout_button.setText("Automático" if self.workspace_layout.mode == "auto" else self.workspace_layout.mode.replace("x", " × "))
        self.grid_page_label.setText(f"{self._grid_page+1} / {pages}")
        self.page_previous.setEnabled(self._grid_page > 0)
        self.page_next.setEnabled(self._grid_page < pages-1)
        for widget in (self.page_previous, self.page_next, self.grid_page_label):
            widget.setVisible(pages > 1 and self._view == "mosaic")

    def notify(self, message: str, *, error: bool = False) -> None:
        self.toast.setText(message)
        set_tone(self.toast, "red" if error else "green")
        self.toast.setMaximumWidth(max(200, min(560, self.width()-100)))
        self.toast.adjustSize()
        self.toast.move(max(10, (self.width()-self.toast.width())//2), self.height()-self.toast.height()-46)
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start(5000)

    def open_palette(self) -> None:
        commands = [(self.nav_labels[key], item.click) for key, item in self.nav_buttons.items()]
        if self.backend is not None:
            commands.extend((f"CH {feed.channel:02d} · {feed.name}", lambda value=feed.channel: self.select_camera(value)) for feed in self.backend.feeds)
        CommandPalette(commands, self).exec()

    def show_about(self) -> None:
        self._show_tool("about")

    def show_camera_health(self, channel: int) -> None:
        self.show_health()
        tool = self._tool_widgets.get("health")
        if tool is not None:
            tool.select_channel(channel)

    def _show_tool(self, key: str) -> None:
        self._stop_ptz()
        self._pause_playback()
        self.device_drawer.hide()
        if self._player_focus:
            self._toggle_player_fullscreen()
        if key not in self._tool_pages:
            page = QWidget()
            page.setObjectName("workspacePage")
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(EmptyState("Preparando a central", "As ferramentas estarão disponíveis assim que a conexão inicial terminar."))
            self._tool_pages[key] = page
            self.pages.addWidget(page)
        self._ensure_tool(key)
        self._view = key
        self.pages.setCurrentWidget(self._tool_pages[key])
        title, description = {
            "health": ("Dispositivos", "Conexão e qualidade de análise de cada câmera."),
            "calibration": ("Calibração por ambiente", "Prepare o enquadramento, marque as áreas e revise antes de aplicar."),
            "alerts": ("Alertas e recebimentos", "Configure o Telegram e acompanhe as entregas."),
            "performance": ("Desempenho da inteligência artificial", "Hardware, tempos de processamento e perfis de análise."),
            "about": ("Sua estação UStudio", "Vision Control 2.0 · monitoramento local com inteligência artificial."),
        }[key]
        self.page_title.setText(title)
        self.page_subtitle.setText(description)
        self.breadcrumb.setText("UStudio Vision Control  /  "+title)
        self.back_button.show()
        self.layout_button.hide()
        self.ptz_panel.hide()
        self._refresh_navigation()

    def _ensure_tool(self, key: str) -> None:
        if key in self._tool_widgets or (self.backend is None and key != "about"):
            return
        if key == "about":
            tool = QWidget()
            content = QVBoxLayout(tool)
            content.setContentsMargins(0, 0, 0, 0)
            card = SectionCard("UStudio Vision Control 2.0", "Central desktop de monitoramento. Vídeo, análise e gravações continuam ativos durante a navegação.")
            card.body.addWidget(text("Interface nativa Qt / PySide6 · Python 3.12", "subtitle"))
            card.body.addWidget(text("Processamento local · sem servidor web no executável", "subtitle"))
            card.body.addWidget(text(str(PROJECT_DIR), "caption"))
            for title, target in (("Abrir pasta do projeto", PROJECT_DIR), ("Abrir registros de diagnóstico", PROJECT_DIR/"logs")):
                action = button(title, "folder")
                action.clicked.connect(lambda checked=False, path=target: os.startfile(path))
                card.body.addWidget(action)
            content.addWidget(card)
            content.addStretch()
        else:
            factories = {"health": lambda: HealthDialog(self.backend, self), "calibration": lambda: CalibrationDialog(self.backend, PROJECT_DIR, self),
                         "alerts": lambda: AlertsDialog(self.backend.notifications, self), "performance": lambda: PerformanceDialog(self.backend, self)}
            tool = factories[key]()
            tool.setWindowFlags(Qt.WindowType.Widget)
            tool.setObjectName("workspacePage")
        self._tool_widgets[key] = tool
        host = self._tool_pages[key].layout()
        while host.count():
            child = host.takeAt(0).widget()
            child.hide()
            child.deleteLater()
        host.addWidget(tool)
        tool.show()
        if hasattr(tool, "refresh"):
            tool.refresh()

    def show_health(self) -> None:
        self._show_tool("health")

    def show_calibration(self) -> None:
        self._show_tool("calibration")

    def show_alerts(self) -> None:
        self._show_tool("alerts")

    def show_performance(self) -> None:
        self._show_tool("performance")

    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(57)
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 0, 23, 0)
        self.breadcrumb = text("Central de vigilância    /    Monitoramento", "breadcrumb")
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.breadcrumb, 1)
        search = button("Buscar…   Ctrl+K", "search")
        self.search_button = search
        search.setToolTip("Buscar páginas e câmeras · Ctrl+K")
        search.setObjectName("searchButton")
        search.clicked.connect(self.open_palette)
        row.addWidget(search)
        devices = button("Câmeras", "camera")
        devices.clicked.connect(self.toggle_devices)
        row.addWidget(devices)
        row.addWidget(self.tools_nav)
        self.live_chip = chip("●  CONECTANDO", "muted")
        row.addWidget(self.live_chip)
        row.addSpacing(11)
        self.mode_chip = chip("MODO DE CALIBRAÇÃO", "amber")
        row.addWidget(self.mode_chip)
        row.addSpacing(18)
        self.clock = text("", "clock")
        row.addWidget(self.clock)
        return bar

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("statusbar")
        bar.setFixedHeight(29)
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 0, 23, 0)
        self.status_text = text("Inicializando o sistema de monitoramento…", "statusText")
        row.addWidget(self.status_text)
        row.addStretch()
        self.engine_status = text("Motor de visão iniciando", "statusText")
        row.addWidget(self.engine_status)
        row.addSpacing(24)
        row.addWidget(text("F11  Tela cheia     Esc  Voltar", "statusText"))
        return bar

    def _build_library(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        catalog = QFrame()
        catalog.setObjectName("card")
        self.library_catalog = catalog
        catalog.setMinimumWidth(290)
        catalog.setMaximumWidth(390)
        left = QVBoxLayout(catalog)
        left.setContentsMargins(12, 14, 12, 10)
        left.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(text("Histórico de eventos", "cardTitle"))
        heading.addStretch()
        refresh = button("", "refresh")
        refresh.setObjectName("iconButton")
        refresh.setToolTip("Atualizar gravações")
        refresh.clicked.connect(self._refresh_recordings)
        heading.addWidget(refresh)
        left.addLayout(heading)
        self.search = QLineEdit()
        self.search.setAccessibleName("Buscar eventos por câmera, data ou horário")
        self.search.setPlaceholderText("Buscar por data ou horário…")
        self.search.textChanged.connect(self._filter_recordings)
        left.addWidget(self.search)
        self.period_filter = QComboBox()
        self.period_filter.setAccessibleName("Período dos eventos")
        self.period_filter.addItems(["Todo o período", "Hoje", "Últimos 7 dias", "Últimos 30 dias", "Escolher período"])
        self.period_filter.currentIndexChanged.connect(self._filter_recordings)
        left.addWidget(self.period_filter)
        self.date_range = QWidget()
        dates = QHBoxLayout(self.date_range)
        dates.setContentsMargins(0, 0, 0, 0)
        self.start_date = QDateEdit(QDate.currentDate().addDays(-7))
        self.end_date = QDateEdit(QDate.currentDate())
        for widget, name in ((self.start_date, "Data inicial"), (self.end_date, "Data final")):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("dd/MM/yyyy")
            widget.setAccessibleName(name)
            widget.dateChanged.connect(self._filter_recordings)
            dates.addWidget(widget)
        self.date_range.hide()
        left.addWidget(self.date_range)
        self.filter_box = QComboBox()
        self.filter_box.setAccessibleName("Estado de revisão")
        self.filter_box.addItems(["Todos os eventos", "Quedas revisadas", "Atividades sem queda revisadas", "Eventos não revisados", "Revisões inconclusivas"])
        self.filter_box.currentIndexChanged.connect(self._filter_recordings)
        left.addWidget(self.filter_box)
        self.camera_filter = QComboBox()
        self.camera_filter.setAccessibleName("Câmera de origem")
        self.camera_filter.addItem("Todas as câmeras", None)
        self.camera_filter.currentIndexChanged.connect(self._filter_recordings)
        left.addWidget(self.camera_filter)
        self.sort_filter = QComboBox()
        self.sort_filter.setAccessibleName("Ordenação dos eventos")
        self.sort_filter.addItems(["Mais recentes primeiro", "Mais antigos primeiro", "Agrupar por câmera"])
        self.sort_filter.currentIndexChanged.connect(self._filter_recordings)
        left.addWidget(self.sort_filter)
        self.recording_list = QListWidget()
        self.recording_list.setObjectName("recordingList")
        self.recording_list.setSpacing(0)
        self.recording_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recording_list.currentRowChanged.connect(self._recording_selected)
        self.recording_list.itemDoubleClicked.connect(lambda _item: self._toggle_playback())
        left.addWidget(self.recording_list, 1)
        self.recording_count = text("Nenhuma gravação", "recordingSub")
        left.addWidget(self.recording_count)
        layout.addWidget(catalog)
        player_card = QFrame()
        self.player_card = player_card
        player_card.setObjectName("card")
        right = QVBoxLayout(player_card)
        right.setContentsMargins(1, 1, 1, 12)
        right.setSpacing(0)
        player_header = QFrame()
        player_header.setObjectName("playerHeader")
        title_layout = QVBoxLayout(player_header)
        title_layout.setContentsMargins(17, 12, 17, 12)
        title_layout.setSpacing(4)
        self.video_title = text("Selecione uma gravação", "cardTitle")
        self.video_description = text("Reveja os eventos registrados pelo monitor de quedas.", "recordingSub")
        self.video_description.setWordWrap(True)
        title_layout.addWidget(self.video_title)
        title_layout.addWidget(self.video_description)
        right.addWidget(player_header)
        self.player_surface = VideoSurface("Selecione um evento na lista para assistir")
        self.player_surface.setAccessibleName("Reprodutor de eventos")
        self.player_surface.clicked.connect(self._toggle_playback)
        right.addWidget(self.player_surface, 1)
        timeline = QHBoxLayout()
        timeline.setContentsMargins(17, 13, 17, 8)
        self.time_label = text("00:00 / 00:00", "recordingSub")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Posição da gravação")
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(self._pause_playback)
        self.seek.sliderMoved.connect(self._seek_video)
        self.seek.sliderReleased.connect(lambda: self._seek_video(self.seek.value()))
        timeline.addWidget(self.seek, 1)
        timeline.addSpacing(10)
        timeline.addWidget(self.time_label)
        right.addLayout(timeline)
        controls = QHBoxLayout()
        controls.setContentsMargins(17, 4, 17, 0)
        self.play_button = button("Reproduzir", "play", primary=True)
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)
        self.speed = QComboBox()
        self.speed.setAccessibleName("Velocidade da reprodução")
        self.speed.setToolTip("Velocidade de reprodução")
        self.speed.setAccessibleName("Velocidade de reprodução")
        for title, value in (("0,5×", .5), ("1×", 1), ("1,5×", 1.5), ("2×", 2)):
            self.speed.addItem(title, value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self._change_speed)
        controls.addWidget(self.speed)
        controls.addStretch()
        self.external_button = button("Abrir vídeo", "external")
        self.external_button.setToolTip("Abrir vídeo no reprodutor do Windows")
        self.external_button.setEnabled(False)
        self.external_button.clicked.connect(self._open_current_video)
        controls.addWidget(self.external_button)
        folder = button("Pasta de gravações", "folder")
        self.folder_button = folder
        folder.setToolTip("Abrir pasta de gravações")
        folder.clicked.connect(self._open_folder)
        controls.addWidget(folder)
        fullscreen = button("", "expand")
        fullscreen.setObjectName("iconButton")
        fullscreen.setToolTip("Expandir player · Esc para voltar")
        fullscreen.clicked.connect(self._toggle_player_fullscreen)
        controls.addWidget(fullscreen)
        right.addLayout(controls)
        review_row = QHBoxLayout()
        review_row.setSpacing(8)
        review_row.setContentsMargins(17, 10, 17, 0)
        self.review_buttons = []
        for title, label in (("Queda", "fall"), ("Atividade normal", "normal"), ("Não tenho certeza", "unsure")):
            review_button = button(title, "shield")
            review_button.setObjectName("reviewButton")
            review_button.setCheckable(True)
            review_button.setProperty("reviewLabel", label)
            review_button.setEnabled(False)
            review_button.clicked.connect(lambda _checked=False, value=label: self.review_current(value))
            review_row.addWidget(review_button)
            self.review_buttons.append(review_button)
        right.addLayout(review_row)
        self.protect_clip = QCheckBox("Preservar este clipe na retenção automática")
        self.protect_clip.setContentsMargins(17, 8, 0, 0)
        self.protect_clip.setEnabled(False)
        self.protect_clip.toggled.connect(self.protect_current)
        right.addWidget(self.protect_clip)
        self.review_hint = text("Classifique para ajudar na calibração. Clipes revisados são preservados.", "recordingSub")
        self.review_hint.setContentsMargins(17, 7, 17, 0)
        self.review_hint.setWordWrap(True)
        right.addWidget(self.review_hint)
        self.event_details = text("", "caption", wrap=True)
        self.event_details.setContentsMargins(17, 7, 17, 0)
        self.event_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.event_details)
        layout.addWidget(player_card, 1)
        return page

    def _build_ptz(self) -> QFrame:
        panel = QFrame(self.single_tile)
        panel.setObjectName("ptzPanel")
        panel.setFixedSize(236, 112)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setSpacing(5)
        title = text("CONTROLE DA CÂMERA", "sectionLabel")
        grid.addWidget(title, 0, 0, 1, 5)
        for column, action in enumerate(("left", "up", "home", "down", "right")):
            item = button("", action)
            item.setObjectName("iconButton")
            item.setToolTip({"left": "Esquerda", "right": "Direita", "up": "Cima", "down": "Baixo", "home": "Posição inicial"}[action])
            if action == "home":
                item.clicked.connect(lambda: self._send_ptz("home"))
            else:
                item.pressed.connect(lambda value=action: self._start_ptz(value))
                item.released.connect(self._stop_ptz)
            grid.addWidget(item, 1, column)
        panel.hide()
        return panel

    def _install_shortcuts(self) -> None:
        for key, callback in (("F11", self._toggle_fullscreen), ("Escape", self._escape), ("Ctrl+1", self.show_mosaic), ("Ctrl+2", self.show_library), ("Ctrl+K", self.open_palette)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)
        action = QAction(self.player_surface)
        action.setShortcut(QKeySequence("Space"))
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(self._toggle_playback)
        self.player_surface.addAction(action)

    def _tick_clock(self) -> None:
        self.clock.setText(datetime.now().strftime("%d/%m   %H:%M:%S"))

    def _refresh_navigation(self) -> None:
        for key, item in self.nav_buttons.items():
            item.setChecked(self._view == key or key == "mosaic" and self._view == "camera")
        self.metrics_widget.setVisible(self._view == "mosaic")
        for index, item in enumerate(self.camera_buttons, start=1):
            item.setChecked(self._view == "camera" and self._selected_channel == index)
        for widget in (self.page_previous, self.page_next, self.grid_page_label):
            widget.setVisible(self._view == "mosaic" and self.grid_page_label.text() != "1 / 1")

    def show_mosaic(self) -> None:
        if self._player_focus:
            self._toggle_player_fullscreen()
        self.device_drawer.hide()
        self._stop_ptz()
        self._pause_playback()
        self._view, self._selected_channel = "mosaic", 0
        self.pages.setCurrentIndex(0)
        self.page_title.setText("Visão geral")
        self.page_subtitle.setText("Todas as câmeras, em um só lugar.")
        self.breadcrumb.setText("Central de vigilância    /    Monitoramento")
        self.back_button.hide()
        self.layout_button.show()
        self.ptz_panel.hide()
        self._reflow_grid()
        self._refresh_navigation()

    def select_camera(self, channel: int) -> None:
        if self.backend is None or not 1 <= channel <= len(self.backend.feeds):
            return
        self._stop_ptz()
        self._pause_playback()
        if self._player_focus:
            self._toggle_player_fullscreen()
        self.device_drawer.hide()
        self._view, self._selected_channel = "camera", channel
        feed = self.backend.feeds[channel - 1]
        self.single_tile.channel = channel
        self.single_tile.sequence = -1
        self.single_tile.surface.image = None
        self.single_tile.update_status(feed.status())
        self.pages.setCurrentIndex(1)
        self.page_title.setText(f"{feed.name}  ·  Canal {channel:02d}")
        self.page_subtitle.setText("Visualização ampliada ao vivo · dê dois cliques para voltar ao mosaico.")
        self.breadcrumb.setText(f"Central de vigilância    /    Câmeras    /    CH {channel:02d}")
        self.back_button.show()
        self.layout_button.hide()
        self.ptz_panel.setVisible(feed.supports_ptz)
        self._place_ptz()
        self._refresh_navigation()

    def show_library(self) -> None:
        self.device_drawer.hide()
        self._stop_ptz()
        self._view = "library"
        self.pages.setCurrentIndex(2)
        self.page_title.setText("Gravações de eventos")
        self.page_subtitle.setText("Histórico local · reveja e acompanhe os eventos registrados.")
        self.breadcrumb.setText("Central de vigilância    /    Gravações")
        self.back_button.show()
        self.layout_button.hide()
        self.ptz_panel.hide()
        self.live_chip.setText("●  MONITORAMENTO ATIVO" if self.backend is not None else "●  CONECTANDO")
        self._refresh_recordings()
        self._refresh_navigation()

    def _start_backend(self) -> None:
        try:
            settings = DVRSettings.load()
            backend = DashboardState(settings, native=True, compose_mosaic=False, event_store=self.event_store)
            backend.start()
            self._events.put(("ready", backend))
        except Exception as exc:
            LOGGER.exception("Falha ao iniciar a central de monitoramento")
            self._events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event == "ready":
                self._backend_starting = False
                self.backend = payload
                if self._closing:
                    self._shutdown_backend()
                    continue
                for index, feed in enumerate(self.backend.feeds[:9]):
                    self.camera_buttons[index].setText(f"  {feed.channel:02d}   {feed.name}")
                    self.tiles[index].title.setText(feed.name)
                self._refresh_camera_filter()
                self._filter_recordings()
                self._reflow_grid()
                for key in list(self._tool_pages):
                    self._ensure_tool(key)
            elif event == "thumbnail":
                path, frame = payload
                self._pending_thumbnails.discard(path)
                if frame is not None and path in self._paths:
                    self._thumbnail_cache[path] = frame
                item = self._recording_rows.get(path)
                if item is not None and frame is not None:
                    self._set_thumbnail(item, frame)
            elif event == "error":
                self._backend_starting = False
                if self._closing:
                    self._finish_close()
                    return
                QMessageBox.critical(self, "UStudio Vision", f"Não foi possível iniciar o monitoramento.\n\n{payload}")
                self.close()
            elif event == "stopped":
                self._finish_close()
                return
            elif event == "ptz_error":
                self.status_text.setText(f"A câmera não respondeu ao controle: {payload}")

    def _render(self) -> None:
        if self._closing or self.backend is None or self._view not in {"mosaic", "camera"}:
            return
        targets = self.tiles if self._view == "mosaic" else [self.single_tile]
        for tile in targets:
            if not tile.isVisible() or tile.channel > len(self.backend.feeds):
                continue
            feed = self.backend.feeds[tile.channel - 1]
            frame, sequence, detections, width, height, _online = feed.display_snapshot()
            if frame is None or tile.sequence == sequence:
                continue
            # Paint only at the visible resolution, using the shared visual
            # tracker. Full-resolution recording stays in the existing engine.
            view_width = max(160, round(tile.surface.width() * tile.devicePixelRatioF()))
            view_width = min(1920, view_width)
            aspect = 16/9 if width == 960 and height == 1080 and tile.channel <= getattr(getattr(self.backend, "settings", None), "channel_count", 8) else width/max(1, height)
            view_height = max(1, round(view_width/aspect))
            image = cv2.resize(frame, (view_width, view_height), interpolation=cv2.INTER_AREA)
            _draw_person_boxes(image, detections, width, height)
            tile.surface.set_frame(image)
            tile.sequence = sequence

    def _refresh_status(self) -> None:
        if self._closing:
            return
        self._last_refresh += 1
        if self._last_refresh % 6 == 0:
            self._refresh_recordings()
        if self.backend is None:
            return
        statuses = [feed.status() for feed in self.backend.feeds]
        ai = self.backend.detection_worker.status()
        online = sum(bool(item["online"]) for item in statuses)
        people = sum(item["personCount"] for item in statuses)
        self.online_card.value.setText(f"{online} / {len(statuses)}")
        self.online_card.hint.setText("Todos os dispositivos online" if online == len(statuses) else "Verificando conexões")
        self.people_card.value.setText(str(people))
        self.people_card.hint.setText("Nenhuma pessoa detectada" if people == 0 else "Presença identificada nas câmeras")
        self.status_text.setText(f"●  {online} câmeras conectadas   ·   {'Ambiente sem pessoas' if people == 0 else str(people) + ' pessoa(s) detectada(s)'}")
        self.engine_status.setText(f"Visão inteligente ativa  ·  {ai['inferenceMs']:.0f} ms" if ai.get("ready") else "Preparando visão inteligente…")
        self.live_chip.setText("●  SEM CONEXÃO" if online == 0 else "●  MONITORAMENTO ATIVO" if self._view == "library" else "●  AO VIVO")
        set_tone(self.live_chip, "red" if online == 0 else "green" if online == len(statuses) else "amber")
        for index, item in enumerate(statuses[:9]):
            self.tiles[index].update_status(item)
            self.camera_buttons[index].setIcon(icon("camera", GREEN if item["online"] else "#e88493", 15))
        if self._view == "camera":
            self.single_tile.update_status(statuses[self._selected_channel - 1])
        monitored = [item for item in statuses if item.get("fall", {}).get("enabled")]
        if monitored:
            priority = {"normal": 0, "possible_fall": 1, "down": 2, "alert_pending": 3}
            current = max(monitored, key=lambda item: (bool(item["online"]), priority.get(item["fall"].get("state"), 0)))
            state = current["fall"].get("state", "normal") if current["online"] else "normal"
            label = {"normal": "Em observação", "possible_fall": "Verificando", "down": "Pessoa no chão", "alert_pending": "Queda confirmada"}.get(state, "Em observação") if ai.get("ready") else "Preparando"
            color = {"normal": "#79d9ba", "possible_fall": "#efc17c", "down": "#efc17c", "alert_pending": "#ff8d9c"}.get(state, GREEN)
            self.fall_card.value.setText(label)
            self.fall_card.value.setStyleSheet(f"font-size:18px;color:{color};font-weight:600;")
            active = sum(bool(item["online"]) for item in monitored)
            self.fall_card.hint.setText(
                f"CH {current['id']:02d} · {current['name']}" if state != "normal"
                else f"{active} / {len(monitored)} câmeras em calibração"
            )
            pending = [f"CH {item['id']:02d}" for item in monitored if not item["fall"].get("perspectiveGuard")]
            self.fall_card.setToolTip("Perspectiva pendente: " + ", ".join(pending) if pending else "Perfis de perspectiva carregados")
            self.mode_chip.setText("MODO DE CALIBRAÇÃO" if all(item["fall"].get("shadowMode") for item in monitored) else "MONITOR DE QUEDAS")
        if ai.get("error"):
            self.engine_status.setText("Visão inteligente indisponível")
        critical = [feed for feed in statuses if feed.get("fall", {}).get("state") == "alert_pending"]
        self.alert_banner.setVisible(bool(critical) and self._view in {"mosaic", "camera"})
        if critical:
            self.alert_banner.setText("Alerta de queda · " + ", ".join(f"CH {feed['id']:02d} · {feed['name']}" for feed in critical) + "    —    Acompanhar recebimentos")
        unreviewed = sum(self._classifications.get(path.name) is None for path in self._paths)
        self.recordings_card.hint.setText(f"{unreviewed} aguardando revisão" if unreviewed else "Revisões em dia")

    def _load_classifications(self) -> None:
        for folder in sorted((PROJECT_DIR / "datasets").glob("ch*-calibration")):
            for manifest in (folder / "manifest.json", *sorted(folder.glob("review-*.json"))):
                if not manifest.exists():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    if "results" in data and "summary" in data:
                        continue
                    self._classifications.update({item["file"]: item["fall"] for item in data["clips"]})
                except (OSError, ValueError, KeyError):
                    LOGGER.warning("Não foi possível carregar a revisão: %s", manifest.name)
        self._classifications.update({name: {"fall": True, "normal": False, "unsure": "unsure"}[item["label"]]
                                      for name, item in self.event_store.reviews().items() if item.get("label")})

    def _camera_label(self, channel: int | None) -> str:
        if channel is None:
            return "Canal não identificado"
        feed = self.backend.feed(channel) if self.backend else None
        return f"CH {channel:02d}" + (f" · {feed.name}" if feed else "")

    def _refresh_camera_filter(self) -> None:
        selected = self.camera_filter.currentData()
        channels = {recording_channel(path) for path in self._paths} - {None}
        if self.backend:
            channels.update(feed.channel for feed in self.backend.feeds)
        self.camera_filter.blockSignals(True)
        self.camera_filter.clear()
        self.camera_filter.addItem("Todas as câmeras", None)
        for channel in sorted(channels):
            self.camera_filter.addItem(self._camera_label(channel), channel)
        self.camera_filter.setCurrentIndex(max(0, self.camera_filter.findData(selected)))
        self.camera_filter.blockSignals(False)

    def _refresh_recordings(self) -> None:
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        paths = sorted(RECORDINGS.glob("queda-ch*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
        self.recordings_card.value.setText(str(len(paths)))
        if paths == self._paths:
            return
        self._paths = paths
        self._refresh_camera_filter()
        self._thumbnail_cache = {path: frame for path, frame in self._thumbnail_cache.items() if path in paths}
        self._filter_recordings()

    def _filter_recordings(self) -> None:
        selected = self._current_clip
        self.recording_list.blockSignals(True)
        self.recording_list.clear()
        self._recording_rows.clear()
        needle = self.search.text().strip().lower()
        category = self.filter_box.currentIndex()
        channel = self.camera_filter.currentData()
        period = self.period_filter.currentIndex()
        self.date_range.setVisible(period == 4)
        paths = list(self._paths)
        def event_date(path):
            try:
                return datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d_%H-%M-%S")
            except (IndexError, ValueError):
                return datetime.fromtimestamp(path.stat().st_mtime)
        if self.sort_filter.currentIndex() == 2:
            paths.sort(key=lambda path: (recording_channel(path) or 0, -event_date(path).timestamp()))
        else:
            paths.sort(key=event_date, reverse=self.sort_filter.currentIndex() == 0)
        visible = []
        for path in paths:
            day = event_date(path).date()
            today = datetime.now().date()
            if period in {1, 2, 3} and not 0 <= (today-day).days <= {1: 0, 2: 6, 3: 29}[period]:
                continue
            if period == 4 and not self.start_date.date().toPython() <= day <= self.end_date.date().toPython():
                continue
            clip_channel = recording_channel(path)
            if channel is not None and clip_channel != channel:
                continue
            reviewed = self._classifications.get(path.name)
            if (category == 1 and reviewed is not True or category == 2 and reviewed is not False
                    or category == 3 and reviewed is not None or category == 4 and reviewed != "unsure"):
                continue
            stamp = path.stem.replace("_", " ").replace("-", "/")
            try:
                human_stamp = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d_%H-%M-%S").strftime("%d/%m/%Y %H:%M:%S")
            except (IndexError, ValueError):
                human_stamp = ""
            camera_label = self._camera_label(clip_channel)
            if needle and needle not in path.name.lower() and needle not in stamp.lower() and needle not in human_stamp and needle not in camera_label.lower():
                continue
            visible.append(path)
            classification = ("Queda revisada" if reviewed is True else "Revisado · atividade sem queda" if reviewed is False
                              else "Revisado · inconclusivo" if reviewed == "unsure" else "Evento registrado · não revisado")
            if self.event_store.is_protected(path):
                classification += " · preservado"
            row = RecordingRow(path, classification, camera_label)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(300, 93))
            self.recording_list.addItem(item)
            self.recording_list.setItemWidget(item, row)
            self._recording_rows[path] = row
            if path in self._thumbnail_cache:
                self._set_thumbnail(row, self._thumbnail_cache[path])
            elif path not in self._pending_thumbnails:
                self._pending_thumbnails.add(path)
                self._workers.submit(self._load_thumbnail, path)
        self.recording_list.blockSignals(False)
        self.recording_count.setText(f"{len(visible)} {'evento disponível' if len(visible) == 1 else 'eventos disponíveis'}")
        if selected in visible:
            self.recording_list.blockSignals(True)
            self.recording_list.setCurrentRow(visible.index(selected))
            self.recording_list.blockSignals(False)
            self._update_clip_heading(selected)
        elif visible:
            self.recording_list.setCurrentRow(0)
        else:
            self._pause_playback()
            if self._player is not None:
                self._player.release()
                self._player = None
            self._current_clip = None
            self._sync_review_controls()
            self._clip_frames = self._clip_position = 0
            self.seek.setRange(0, 0)
            self.play_button.setEnabled(False)
            self.external_button.setEnabled(False)
            self.video_title.setText("Nenhum evento neste filtro")
            self.video_description.setText("Ajuste o período, a câmera ou a classificação")
            self.player_surface.image = None
            self.player_surface.placeholder = "Nenhuma gravação corresponde aos filtros"
            self.player_surface.update()
            self._update_timeline()

    @staticmethod
    def _set_thumbnail(item: RecordingRow, frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        item.thumbnail.setPixmap(QPixmap.fromImage(image).scaled(112, 67, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _load_thumbnail(self, path: Path) -> None:
        capture = cv2.VideoCapture(str(path))
        fps = capture.get(cv2.CAP_PROP_FPS) or 8
        total = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        capture.set(cv2.CAP_PROP_POS_FRAMES, min(round(10 * fps), max(0, int(total) - 1)))
        ok, frame = capture.read()
        capture.release()
        self._events.put(("thumbnail", (path, cv2.resize(frame, (224, 126)) if ok else None)))

    def _recording_selected(self, row: int) -> None:
        if row < 0:
            return
        path = Path(self.recording_list.item(row).data(Qt.ItemDataRole.UserRole))
        if path == self._current_clip:
            self._update_clip_heading(path)
            self._sync_review_controls()
            return
        self._pause_playback()
        if self._player is not None:
            self._player.release()
        self._current_clip = path
        self._player = cv2.VideoCapture(str(path))
        self._clip_fps = self._player.get(cv2.CAP_PROP_FPS) or 8.0
        self._clip_frames = max(0, int(self._player.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.seek.setRange(0, max(0, self._clip_frames - 1))
        ok, frame = self._player.read()
        self.play_button.setEnabled(ok)
        self.external_button.setEnabled(ok)
        self._clip_position = 0
        self._update_clip_heading(path)
        if ok:
            self.player_surface.set_frame(frame)
        else:
            self.player_surface.image = None
            self.player_surface.placeholder = "Não foi possível abrir esta gravação"
            self.player_surface.update()
        self._update_timeline()
        self._sync_review_controls()

    def _update_clip_heading(self, path: Path) -> None:
        self.video_title.setText(self._camera_label(recording_channel(path)))
        details = f"{self._camera_label(recording_channel(path))}   /   {format_time(self._clip_frames / self._clip_fps)}   /   {path.stat().st_size / 1_048_576:.1f} MB"
        try:
            stamp = datetime.strptime(path.stem.split("_", 1)[1], "%Y-%m-%d_%H-%M-%S")
            details = f"{self._camera_label(recording_channel(path))} · {stamp:%d/%m/%Y · %H:%M:%S}   /   {format_time(self._clip_frames / self._clip_fps)}   /   {path.stat().st_size / 1_048_576:.1f} MB"
        except (IndexError, ValueError):
            pass
        self.video_description.setText(details)

    def _sync_review_controls(self) -> None:
        available = self._current_clip is not None
        for widget in self.review_buttons:
            widget.setEnabled(available)
            current = self._classifications.get(self._current_clip.name) if available else None
            widget.setChecked({"fall": True, "normal": False, "unsure": "unsure"}[widget.property("reviewLabel")] is current
                              if widget.property("reviewLabel") != "unsure" else current == "unsure")
        self.protect_clip.setEnabled(available)
        self.protect_clip.blockSignals(True)
        self.protect_clip.setChecked(available and self.event_store.is_protected(self._current_clip))
        self.protect_clip.blockSignals(False)
        if available:
            review = self._classifications.get(self._current_clip.name)
            state = "Queda revisada" if review is True else "Atividade normal" if review is False else "Revisão inconclusiva" if review == "unsure" else "Aguardando revisão"
            self.event_details.setText(f"{state} · {'Clipe preservado' if self.event_store.is_protected(self._current_clip) else 'Retenção automática'}\n{self._current_clip.name}")
        else:
            self.event_details.clear()

    def review_current(self, label: str) -> None:
        if self._current_clip is None:
            return
        try:
            self.event_store.review(self._current_clip, label, protected=True)
            self._load_classifications()
            self.review_hint.setText("Revisão salva e clipe preservado. Isso gera uma referência para calibração; o modelo não é retreinado automaticamente.")
            self._filter_recordings()
            self._sync_review_controls()
            self.notify("Classificação salva. O clipe está preservado.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Revisão não salva", str(exc))

    def protect_current(self, enabled: bool) -> None:
        if self._current_clip is None:
            return
        try:
            self.event_store.protect(self._current_clip, enabled)
            self._filter_recordings()
            self._sync_review_controls()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Proteção não salva", str(exc))

    def _toggle_playback(self) -> None:
        if self._player is None:
            return
        if self._playing:
            self._pause_playback()
        else:
            if self._clip_position >= self._clip_frames - 1:
                self._player.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._playing = True
            self.play_button.setText("Pausar")
            self.play_button.setIcon(icon("pause", "#eff5ff", 17))
            self._player_timer.start(max(20, round(1000 / (self._clip_fps*self._playback_speed))))

    def _change_speed(self) -> None:
        self._playback_speed = self.speed.currentData()
        if self._playing:
            self._player_timer.start(max(20, round(1000/(self._clip_fps*self._playback_speed))))

    def _toggle_player_fullscreen(self) -> None:
        self._player_focus = not self._player_focus
        for widget in (self.library_catalog, self.sidebar, self.topbar, self.statusbar_widget, self.heading_widget):
            widget.setVisible(not self._player_focus)
        if self._player_focus:
            self.showFullScreen()
        else:
            self.showMaximized()
            self._refresh_navigation()

    def _pause_playback(self) -> None:
        self._playing = False
        if hasattr(self, "_player_timer"):
            self._player_timer.stop()
        self.play_button.setText("Reproduzir")
        self.play_button.setIcon(icon("play", "#eff5ff", 17))

    def _video_tick(self) -> None:
        if self._player is None or not self._playing:
            return
        ok, frame = self._player.read()
        if not ok:
            self._pause_playback()
            return
        self._clip_position = max(0, int(self._player.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
        self.player_surface.set_frame(frame)
        self._update_timeline()

    def _seek_video(self, position: int) -> None:
        if self._player is None:
            return
        self._player.set(cv2.CAP_PROP_POS_FRAMES, position)
        ok, frame = self._player.read()
        if ok:
            self._clip_position = position
            self.player_surface.set_frame(frame)
            self._update_timeline()

    def _update_timeline(self) -> None:
        self.seek.setValue(self._clip_position)
        self.time_label.setText(f"{format_time(self._clip_position / self._clip_fps)} / {format_time(self._clip_frames / self._clip_fps)}")

    def _open_current_video(self) -> None:
        if self._current_clip is not None:
            os.startfile(self._current_clip)

    @staticmethod
    def _open_folder() -> None:
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        os.startfile(RECORDINGS)

    def _place_ptz(self) -> None:
        self.ptz_panel.move(max(0, self.single_tile.width() - self.ptz_panel.width() - 18), max(34, self.single_tile.height() - self.ptz_panel.height() - 36))
        self.ptz_panel.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "ptz_panel"):
            QTimer.singleShot(0, self._place_ptz)
        if hasattr(self, "sidebar"):
            self._apply_sidebar_state()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "_ptz_action"):
            self._stop_ptz()
        return super().event(event)

    def _start_ptz(self, action: str) -> None:
        self._ptz_action = action
        self._send_ptz(action)

    def _stop_ptz(self) -> None:
        if self._ptz_action is not None:
            self._ptz_action = None
            self._send_ptz("stop")

    def _send_ptz(self, action: str) -> None:
        if self.backend is None or self.backend.ptz_controller is None:
            return
        controller = self.backend.ptz_controller
        backend, channel = self.backend, self._selected_channel

        def send() -> None:
            try:
                controller.control(action)
                if action != "stop" and channel in backend.fall_monitors:
                    backend.invalidate_view(channel)
            except Exception as exc:
                LOGGER.warning("Falha no PTZ: %s", exc)
                self._events.put(("ptz_error", str(exc)))

        self._ptz_worker.submit(send)

    def _escape(self) -> None:
        if self._player_focus:
            self._toggle_player_fullscreen()
        elif self.isFullScreen():
            self.showMaximized()
        elif self._view != "mosaic":
            self.show_mosaic()
        else:
            self.close()

    def _toggle_fullscreen(self) -> None:
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, event) -> None:
        event.ignore()
        if self._closing:
            return
        self._stop_ptz()
        self._closing = True
        self._pause_playback()
        self.status_text.setText("Encerrando câmeras e finalizando gravações…")
        self.centralWidget().setEnabled(False)
        if self.backend is not None:
            self._shutdown_backend()
        elif not self._backend_starting:
            self._finish_close()

    def _shutdown_backend(self) -> None:
        backend = self.backend

        def stop() -> None:
            try:
                if backend is not None:
                    backend.stop()
            finally:
                self._events.put(("stopped", None))

        threading.Thread(target=stop, name="vision-stop", daemon=True).start()

    def _finish_close(self) -> None:
        for timer in (self._poll_timer, self._render_timer, self._status_timer, self._clock_timer, self._player_timer):
            timer.stop()
        if self._player is not None:
            self._player.release()
        for tool in self._tool_widgets.values():
            if hasattr(tool, "timer"):
                tool.timer.stop()
            if isinstance(tool, AlertsDialog):
                tool._workers.shutdown(wait=False, cancel_futures=True)
        self._workers.shutdown(wait=False, cancel_futures=True)
        self._ptz_worker.shutdown(wait=False, cancel_futures=True)
        self.hide()
        QApplication.instance().quit()


def configure_application(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, token in ((QPalette.ColorRole.Window, "background"), (QPalette.ColorRole.Base, "surface"),
                        (QPalette.ColorRole.AlternateBase, "raised"), (QPalette.ColorRole.Button, "raised"),
                        (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Text, "text"),
                        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "primary"),
                        (QPalette.ColorRole.PlaceholderText, "muted")):
        palette.setColor(role, QColor(COLORS[token]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            path = fonts / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    app.setApplicationName("UStudio Vision")
    app.setOrganizationName("UStudio")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(icon("shield", "#75aaff", 64))
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UStudio.Vision.Desktop")


def dark_titlebar(window: QMainWindow) -> None:
    if os.name == "nt":
        try:
            dark = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(window.winId()), 20, ctypes.byref(dark), ctypes.sizeof(dark))
        except (OSError, AttributeError):
            pass
