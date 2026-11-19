from __future__ import annotations

import json
import copy
from pathlib import Path

import cv2
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QMessageBox,
    QPushButton, QVBoxLayout, QWidget, QFormLayout,
)

from camera_ai.detection.camera_geometry import CameraGeometry
from camera_ai.detection.zones import CameraZones, fit_vertical_point
from camera_ai.ui_components import EmptyState, SectionCard, label, scroll_page, stylesheet


class CalibrationCanvas(QWidget):
    changed = Signal()

    def __init__(self, frame, parent=None) -> None:
        super().__init__(parent)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.mode = "floor"
        self.floor: list = []
        self.rest: list = []
        self.lines: list = []
        self.points: list = []
        self.zoom = 1.0
        self.pan = QPointF()
        self.editable = True
        self._history = []
        self._drag_point = None
        self._pan_origin = None
        self._selected = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        undo = QAction(self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        undo.triggered.connect(self.undo)
        self.addAction(undo)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(160, 160)

    def image_rect(self) -> QRectF:
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        width, height = size.width()*self.zoom, size.height()*self.zoom
        return QRectF((self.width()-width)/2+self.pan.x(), (self.height()-height)/2+self.pan.y(), width, height)

    def checkpoint(self) -> None:
        self._history.append(copy.deepcopy((self.floor, self.rest, self.lines, self.points)))
        self._history = self._history[-64:]

    def nearest(self, position):
        rect = self.image_rect()
        for group in [self.points, *self.floor, *self.rest, *self.lines]:
            for index, (x, y) in enumerate(group):
                mapped = QPointF(rect.x()+x*rect.width(), rect.y()+y*rect.height())
                if (mapped-position).manhattanLength() <= 11:
                    return group, index
        return None

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.MiddleButton or event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._pan_origin = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if not self.editable:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self.image_rect()
        point = event.position()
        if rect.contains(point):
            nearest = self.nearest(point)
            self.checkpoint()
            if nearest is not None:
                self._drag_point = self._selected = nearest
                self.update()
                return
            self.points.append(((point.x()-rect.x())/rect.width(), (point.y()-rect.y())/rect.height()))
            if self.mode == "vertical" and len(self.points) == 2:
                self.lines.append(self.points[:])
                self.points.clear()
            self.update()
            self.changed.emit()

    def mouseMoveEvent(self, event) -> None:
        if self._pan_origin is not None:
            self.pan += event.position()-self._pan_origin
            self._pan_origin = event.position()
            self.update()
        elif self._drag_point is not None:
            group, index = self._drag_point
            rect = self.image_rect()
            group[index] = (max(0, min(1, (event.position().x()-rect.x())/rect.width())),
                            max(0, min(1, (event.position().y()-rect.y())/rect.height())))
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_point = None
        self._pan_origin = None
        self.setCursor(Qt.CursorShape.CrossCursor if self.editable else Qt.CursorShape.ArrowCursor)

    def wheelEvent(self, event) -> None:
        self.zoom = max(.5, min(3, self.zoom*(1.15 if event.angleDelta().y() > 0 else 1/1.15)))
        self.update()
        event.accept()

    def reset_view(self) -> None:
        self.zoom, self.pan = 1.0, QPointF()
        self.update()

    def finish(self) -> None:
        if self.mode == "vertical":
            return
        if len(self.points) < 3:
            raise ValueError("Clique em pelo menos três cantos da região antes de concluir.")
        self.checkpoint()
        (self.floor if self.mode == "floor" else self.rest).append(self.points[:])
        self.points.clear()
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self._history:
            self.floor, self.rest, self.lines, self.points = self._history.pop()
            self._selected = None
            self.update()
            self.changed.emit()
            return

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080f1b"))
        rect = self.image_rect()
        painter.drawImage(rect, self.image)
        def mapped(points):
            return QPolygonF([QPointF(rect.x()+x*rect.width(), rect.y()+y*rect.height()) for x,y in points])
        for polygons, color in ((self.floor, "#59d7ad"), (self.rest, "#e9b45d")):
            for polygon in polygons:
                painter.setPen(QPen(QColor(color), 2))
                fill = QColor(color); fill.setAlpha(45)
                painter.setBrush(fill)
                painter.drawPolygon(mapped(polygon))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#c4a5ff"), 3))
        for first, second in self.lines:
            points = mapped((first, second))
            painter.drawLine(points[0], points[1])
        for group in [*self.floor, *self.rest, *self.lines]:
            painter.setPen(QPen(QColor("#e3edff"), 1))
            painter.setBrush(QColor("#152a45"))
            for index, point in enumerate(mapped(group)):
                selected = self._selected is not None and self._selected[0] is group and self._selected[1] == index
                painter.drawEllipse(point, 6 if selected else 4, 6 if selected else 4)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        if self.points:
            painter.drawPolyline(mapped(self.points))
            for point in mapped(self.points):
                painter.drawEllipse(point, 4, 4)


class CalibrationDialog(QDialog):
    def __init__(self, backend, project_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.backend, self.project_dir = backend, project_dir
        self.canvas: CalibrationCanvas | None = None
        self._baseline = None
        self._loaded_channel = None
        self.step = 0
        self.setWindowTitle("Calibração por ambiente")
        self.resize(1100, 740)
        self.setStyleSheet(parent.styleSheet() if parent else stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        steps = QHBoxLayout()
        self.step_buttons = []
        for index, title in enumerate(("Câmera", "Chão", "Repouso", "Perspectiva", "Revisão")):
            item = QPushButton(f"{index+1}  {title}")
            item.setObjectName("stepButton")
            item.setCheckable(True)
            item.clicked.connect(lambda checked=False, number=index: self.set_step(number))
            self.step_buttons.append(item)
            steps.addWidget(item, 1)
        layout.addLayout(steps)
        workspace = QHBoxLayout()
        workspace.setSpacing(14)
        settings = SectionCard("Preparação do ambiente")
        self.camera = QComboBox()
        self.camera.setAccessibleName("Câmera para calibração")
        for feed in backend.feeds:
            if feed.channel in backend.fall_monitors:
                self.camera.addItem(f"CH {feed.channel:02d} · {feed.name}", feed.channel)
        self.camera.setCurrentIndex(max(0, self.camera.findData(backend.settings.fall_monitor_channel)))
        settings.body.addWidget(self.camera)
        form = QFormLayout()
        self.rotation = QComboBox()
        self.rotation.addItems(["0°", "90°", "180°", "270°"])
        form.addRow("Orientação", self.rotation)
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(1, 30)
        self.seconds.setSuffix(" s")
        form.addRow("Tempo no chão", self.seconds)
        settings.body.addLayout(form)
        self.mode = QComboBox(self)
        for title, mode in (("Área de chão", "floor"), ("Repouso / bancada", "rest"), ("Vertical do ambiente", "vertical")):
            self.mode.addItem(title, mode)
        self.mode.hide()
        self.help = label("", wrap=True)
        settings.body.addWidget(self.help)
        self.review_summary = label("", wrap=True)
        settings.body.addWidget(self.review_summary)
        settings.body.addStretch()
        settings.body.addWidget(label("Verde · chão monitorado\nÂmbar · repouso / bancada\nVioleta · referências verticais", "caption", wrap=True))
        settings.body.addWidget(label("Roda do mouse: zoom\nBotão do meio ou Alt + arrastar: mover\nArraste um ponto para ajustá-lo", "caption", wrap=True))
        settings_scroll = scroll_page(settings)
        settings_scroll.setFixedWidth(255)
        workspace.addWidget(settings_scroll)
        canvas_column = QVBoxLayout()
        toolbar = QHBoxLayout()
        self.canvas_actions = {}
        for title, callback in (("Atualizar imagem", self.load_camera), ("Concluir região", self.finish_region),
                                ("Desfazer", self.undo), ("Limpar", self.clear), ("Ajustar zoom", self.reset_view)):
            item = QPushButton(title)
            item.clicked.connect(callback)
            toolbar.addWidget(item)
            self.canvas_actions[title] = item
        canvas_column.addLayout(toolbar)
        self.canvas_host = QVBoxLayout()
        self.empty = EmptyState("Sem imagem recente", "Selecione uma câmera online para iniciar a calibração.")
        self.canvas_host.addWidget(self.empty)
        canvas_column.addLayout(self.canvas_host, 1)
        workspace.addLayout(canvas_column, 1)
        layout.addLayout(workspace, 1)
        footer = QHBoxLayout()
        self.result = label("As alterações só serão aplicadas ao salvar.", wrap=True)
        footer.addWidget(self.result, 1)
        previous = QPushButton("Voltar")
        self.previous = previous
        previous.clicked.connect(lambda: self.set_step(max(0, self.step-1)))
        footer.addWidget(previous)
        self.next = QPushButton("Próxima etapa")
        self.next.clicked.connect(lambda: self.set_step(min(4, self.step+1)))
        footer.addWidget(self.next)
        self.save_button = QPushButton("Salvar e aplicar")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.camera.currentIndexChanged.connect(self.load_camera)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.rotation.currentIndexChanged.connect(self.refresh_review)
        self.seconds.valueChanged.connect(self.refresh_review)
        self.load_camera()
        self.set_step(0)

    def set_step(self, step: int) -> None:
        if self.canvas and self.canvas.points and step != self.step:
            self.result.setText("Conclua a região ou desfaça os pontos abertos antes de mudar de etapa.")
            return
        self.step = step
        instructions = [
            "Escolha a câmera e confira a orientação. O tempo define por quanto tempo a pessoa deve permanecer identificada no chão.",
            "Clique nos cantos do chão que deseja monitorar. Conclua cada região; use mais de uma quando necessário.",
            "Opcional: marque camas, sofás ou bancadas. Quedas dentro dessas regiões não serão confirmadas.",
            "Marque duas extremidades de cada vertical real, como batentes ou pilares. Use pelo menos duas em posições diferentes; não use linhas do piso.",
            "Confira as áreas, a perspectiva e o tempo no chão. Salvar aplica somente a esta câmera; valide depois com clipes revisados.",
        ]
        self.help.setText(instructions[step])
        for index, item in enumerate(self.step_buttons):
            item.setChecked(index == step)
        if step in {1, 2, 3}:
            self.mode.setCurrentIndex(step-1)
        if self.canvas:
            self.canvas.editable = step in {1, 2, 3}
            self.canvas.setCursor(Qt.CursorShape.CrossCursor if self.canvas.editable else Qt.CursorShape.ArrowCursor)
        self.review_summary.setVisible(step == 4)
        self.previous.setEnabled(step > 0)
        self.next.setVisible(step < 4)
        self.save_button.setVisible(step == 4)
        self.refresh_review()

    def refresh_review(self) -> None:
        ready = self.canvas is not None and self.canvas.isEnabled()
        self.next.setEnabled(ready)
        self.save_button.setEnabled(ready and not self.canvas.points)
        for title, item in self.canvas_actions.items():
            if title != "Atualizar imagem":
                item.setEnabled(ready)
        self.canvas_actions["Desfazer"].setEnabled(ready and bool(self.canvas._history))
        self.canvas_actions["Concluir região"].setEnabled(ready and self.canvas.mode != "vertical" and len(self.canvas.points) >= 3)
        if not ready:
            self.review_summary.setText("A câmera precisa estar online.")
            return
        channel = self.camera.currentData()
        existing = self.backend.settings.fall_geometry(channel)
        perspective = "novas referências" if len(self.canvas.lines) >= 2 else "existente preservada" if existing else "pendente"
        self.review_summary.setText(f"CH {channel:02d}\n{len(self.canvas.floor)} área(s) de chão\n{len(self.canvas.rest)} área(s) de repouso\nPerspectiva: {perspective}\nOrientação: {self.rotation.currentText()}\nTempo no chão: {self.seconds.value():g}s")

    def reset_view(self) -> None:
        if self.canvas:
            self.canvas.reset_view()

    def load_camera(self) -> None:
        channel = self.camera.currentData()
        if self._loaded_channel is not None and self._snapshot() != self._baseline:
            answer = QMessageBox.question(self, "Alterações ainda não salvas",
                "Atualizar a imagem ou trocar de câmera descarta as alterações desta calibração. Deseja descartá-las?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                self.camera.blockSignals(True)
                self.camera.setCurrentIndex(self.camera.findData(self._loaded_channel))
                self.camera.blockSignals(False)
                return
        self._loaded_channel = channel
        feed = self.backend.feed(channel)
        frame, _ = feed.frame_snapshot() if feed is not None else (None, 0)
        if frame is None or not feed.status()["online"]:
            self.result.setText("Esta câmera está offline. Escolha uma câmera com imagem recente.")
            if self.canvas:
                self.canvas.setEnabled(False)
                self.canvas.hide()
            self.empty.show()
            self._baseline = self._snapshot()
            self.refresh_review()
            return
        if self.canvas:
            self.canvas_host.removeWidget(self.canvas)
            self.canvas.deleteLater()
        self.empty.hide()
        self.canvas = CalibrationCanvas(frame.copy())
        self.canvas.changed.connect(self.refresh_review)
        self.canvas_host.addWidget(self.canvas)
        zones = self.backend.settings.camera_zones(channel)
        if zones and not zones.view_changed:
            self.canvas.floor = [[list(point) for point in polygon] for polygon in zones.floors]
            self.canvas.rest = [[list(point) for point in polygon] for polygon in zones.rest]
        self.rotation.setCurrentIndex(self.backend.settings.fall_rotation(channel)//90)
        self.seconds.setValue(self.backend.settings.ground_seconds(channel))
        geometry = self.backend.settings.fall_geometry(channel)
        self.result.setText("Enquadramento alterado: marque novas referências verticais e regiões." if zones and zones.view_changed else
                           "Perspectiva existente será preservada se nenhuma vertical nova for marcada." if geometry else
                           "Perspectiva pendente: marque pelo menos duas verticais reais. Somente desenhar zonas não calibra a perspectiva.")
        self.change_mode()
        self.canvas.editable = self.step in {1, 2, 3}
        self._baseline = self._snapshot()
        self.refresh_review()

    def _snapshot(self):
        if self.canvas is None or not self.canvas.isEnabled():
            return None
        return copy.deepcopy((self.canvas.floor, self.canvas.rest, self.canvas.lines, self.canvas.points,
                              self.rotation.currentIndex(), self.seconds.value()))

    def change_mode(self) -> None:
        if self.canvas:
            self.canvas.mode = self.mode.currentData()
            self.canvas.points.clear()
            self.canvas.update()

    def finish_region(self) -> None:
        try:
            if self.canvas and self.canvas.isEnabled():
                self.canvas.finish()
        except ValueError as exc:
            self.result.setText(str(exc))

    def undo(self) -> None:
        if self.canvas:
            self.canvas.undo()

    def clear(self) -> None:
        if self.canvas:
            self.canvas.checkpoint()
            self.canvas.floor.clear(); self.canvas.rest.clear(); self.canvas.lines.clear(); self.canvas.points.clear()
            self.canvas._selected = None
            self.canvas.changed.emit()
            self.canvas.update()

    def save(self) -> None:
        if not self.canvas or not self.canvas.isEnabled():
            return
        if self.canvas.points:
            self.result.setText("Conclua ou desfaça os pontos ainda abertos antes de salvar.")
            return
        channel = self.camera.currentData()
        folder = self.project_dir / "calibration"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"ch{channel:02d}-zones.json"
        temporary = target.with_suffix(".tmp")
        try:
            previous_zones = self.backend.settings.camera_zones(channel)
            if previous_zones and previous_zones.view_changed and len(self.canvas.lines) < 2:
                raise ValueError("A câmera mudou de enquadramento. Marque pelo menos duas novas referências verticais.")
            data = {"channel": channel, "coordinateSystem": "raw_image_normalized_xy", "floor": self.canvas.floor,
                    "rest": self.canvas.rest, "rotationDegrees": self.rotation.currentIndex()*90,
                    "groundConfirmSeconds": self.seconds.value()}
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            CameraZones.load(temporary, channel)
            if self.canvas.lines:
                point = fit_vertical_point(self.canvas.lines)
                settings = self.backend.settings
                configured = settings.fall_geometry_profiles.get(channel) or (settings.fall_geometry_profile if channel == settings.fall_monitor_channel else "")
                geometry_path = self.project_dir / (configured or f"calibration/ch{channel:02d}-geometry.json")
                profile = json.loads(geometry_path.read_text(encoding="utf-8")) if geometry_path.exists() else {
                    "channel": channel, "coordinateSystem": "raw_image_normalized_xy", "minLegDeviationDegrees": 8,
                    "minTorsoDeviationDegrees": 25, "strongLegDeviationDegrees": 30, "strongTorsoDeviationDegrees": 60,
                }
                profile.update(verticalVanishingPoint=list(point), source="manual_vertical_references", validated=False)
                geometry_path.parent.mkdir(parents=True, exist_ok=True)
                geometry_temp = geometry_path.with_suffix(".tmp")
                geometry_temp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
                if CameraGeometry.load(geometry_temp).channel != channel:
                    raise ValueError("O perfil de perspectiva pertence a outro canal.")
                geometry_temp.replace(geometry_path)
            temporary.replace(target)
            self.backend.reload_calibration(channel)
            self._baseline = self._snapshot()
            self.result.setText("Configuração aplicada a este canal. Valide os clipes antes de habilitar alertas reais; zonas de repouso não confirmam quedas.")
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Calibração não aplicada", str(exc))
