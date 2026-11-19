from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from camera_ai.performance import PerformanceProfile
from camera_ai.ui_components import COLORS, SectionCard, label, scroll_page, stylesheet


class HealthDialog(QDialog):
    def __init__(self, backend, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Diagnóstico das câmeras")
        self.resize(1080, 680)
        self.setStyleSheet(parent.styleSheet() if parent else stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        toolbar = QHBoxLayout()
        self.summary = label("", "cardTitle")
        toolbar.addWidget(self.summary, 1)
        self.connection_filter = QComboBox()
        self.connection_filter.setAccessibleName("Filtrar dispositivos por conexão")
        self.connection_filter.addItems(["Todos os dispositivos", "Somente online", "Precisam de atenção"])
        self.connection_filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.connection_filter)
        refresh = QPushButton("Atualizar diagnóstico")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Câmera", "Endereço", "Conexão", "Vídeo · fps", "IA · fps", "Quadro · s", "IA · ms", "Reconexões"])
        descriptions = ("Canal e nome configurado", "Endereço local, sem credenciais", "Estado informado pela captura",
                        "Quadros de vídeo recebidos por segundo", "Quadros analisados por segundo",
                        "Segundos desde o último quadro recebido", "Idade do resultado da IA em milissegundos",
                        "Reconexões desde a abertura do aplicativo")
        for column, description in enumerate(descriptions):
            self.table.horizontalHeaderItem(column).setToolTip(description)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(45)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(43)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table, 1)
        details = SectionCard("Dispositivo selecionado")
        self.details = label("Selecione uma câmera na tabela para investigar.", wrap=True)
        details.body.addWidget(self.details)
        row = QHBoxLayout()
        self.retry = QPushButton("Reconectar câmera")
        self.retry.clicked.connect(self._retry)
        row.addWidget(self.retry)
        find = QPushButton("Procurar DVR pareado")
        find.setToolTip("Solicitar busca do gravador já pareado, sem mudar credenciais")
        find.clicked.connect(backend.recovery.request)
        row.addWidget(find)
        row.addStretch()
        details.body.addLayout(row)
        self.recovery = label("", wrap=True)
        details.body.addWidget(self.recovery)
        layout.addWidget(details)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(1000)
        self.refresh()

    def selected_channel(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select_channel(self, channel: int) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == channel:
                self.table.selectRow(row)
                return

    def refresh(self) -> None:
        selected = self.selected_channel()
        statuses = [(feed, feed.status()) for feed in self.backend.feeds]
        self.summary.setText(f"{sum(status['online'] for _, status in statuses)} online · {len(statuses)} dispositivos")
        mode = self.connection_filter.currentIndex()
        visible = [(feed, status) for feed, status in statuses if mode == 0 or bool(status["online"]) == (mode == 1)]
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(visible))
        for row, (feed, status) in enumerate(visible):
            age = status["frameAgeSeconds"]
            values = [f"CH {feed.channel:02d} · {feed.name}", status["host"] or "—", status["connectionMessage"],
                      status["fps"], status["analysisFps"], age if age is not None else "Sem imagem",
                      round(status["resultAgeMs"]), status["reconnectCount"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                item.setData(Qt.ItemDataRole.UserRole, feed.channel)
                item.setToolTip(str(value))
                if column >= 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 2:
                    item.setForeground(QColor(COLORS["healthy"] if status["online"] else COLORS["critical"]))
                elif (column == 5 and age is not None and age > 1) or (column == 6 and status["resultAgeMs"] > 1000):
                    item.setForeground(QColor(COLORS["attention"]))
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.recovery.setText(self.backend.recovery.message)
        if selected is not None:
            self.select_channel(selected)
        elif self.table.rowCount():
            self.table.selectRow(0)
        self.show_details()

    def show_details(self) -> None:
        channel = self.selected_channel()
        feed = self.backend.feed(channel) if channel is not None else None
        self.retry.setEnabled(feed is not None)
        if feed is None:
            self.details.setText("Nenhum dispositivo neste filtro.")
            return
        status = feed.status()
        age = status["frameAgeSeconds"]
        self.details.setText(
            f"CH {channel:02d} · {feed.name} — {status['connectionMessage']}\n"
            f"{status['host'] or 'Endereço indisponível'} · Vídeo {status['fps']:.1f} fps · Análise {status['analysisFps']:.1f} fps · "
            f"Atraso da IA {status['resultAgeMs']:.0f} ms\n"
            f"Último quadro: {str(age)+'s atrás' if age is not None else 'ainda não recebido'} · {status['reconnectCount']} reconexão(ões)"
        )

    def _retry(self) -> None:
        channel = self.selected_channel()
        feed = self.backend.feed(channel) if channel is not None else None
        if feed is not None:
            feed.reconnect()
            self.refresh()

    def done(self, result: int) -> None:
        self.timer.stop()
        super().done(result)


class PerformanceDialog(QDialog):
    def __init__(self, backend, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Desempenho da análise")
        self.resize(950, 660)
        self.setStyleSheet(parent.styleSheet() if parent else stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        hardware = SectionCard("Motor de visão")
        self.hardware = label("", "cardTitle", wrap=True)
        self.model = label("", wrap=True)
        hardware.body.addWidget(self.hardware)
        hardware.body.addWidget(self.model)
        layout.addWidget(hardware)
        processing = SectionCard("Processamento em tempo real")
        metrics = QGridLayout()
        self.metric_values = {}
        for index, (key, title) in enumerate((("average", "Tempo médio"), ("p95", "P95 de inferência"), ("fps", "FPS médios por câmera"), ("cameras", "Câmeras no último lote"))):
            box = QVBoxLayout()
            box.addWidget(label(title, "caption"))
            value = label("—", "metricValue")
            self.metric_values[key] = value
            box.addWidget(value)
            metrics.addLayout(box, index//2, index%2)
        processing.body.addLayout(metrics)
        self.statistics = label("", wrap=True)
        processing.body.addWidget(self.statistics)
        layout.addWidget(processing)
        configuration = SectionCard("Perfil de análise", "A visualização e os clipes continuam usando o stream principal. Alterações só são aplicadas pelo botão abaixo.")
        self.profile = QComboBox()
        self.profile.setAccessibleName("Perfil de análise")
        self.profile.addItem("Qualidade original", PerformanceProfile(backend.settings.yolo_image_size, backend.settings.detection_interval))
        self.profile.addItem("Equilibrado", PerformanceProfile(640, .30, 1280))
        self.profile.addItem("Econômico", PerformanceProfile(512, .50, 960))
        current = backend.detection_worker.performance
        match = next((index for index in range(self.profile.count()) if self.profile.itemData(index) == current), None)
        if match is None:
            self.profile.addItem("Personalizado · em uso", current)
            match = self.profile.count()-1
        self.profile.setCurrentIndex(match)
        configuration.body.addWidget(self.profile)
        self.profile_description = label("", wrap=True)
        configuration.body.addWidget(self.profile_description)
        self.profile.currentIndexChanged.connect(self.describe_profile)
        apply = QPushButton("Aplicar perfil selecionado")
        apply.setObjectName("primaryButton")
        apply.clicked.connect(self.apply)
        configuration.body.addWidget(apply)
        self.result = label("", wrap=True)
        configuration.body.addWidget(self.result)
        layout.addWidget(configuration)
        layout.addStretch()
        outer.addWidget(scroll_page(content))
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(1000)
        self.describe_profile()
        self.refresh()

    def describe_profile(self) -> None:
        profile = self.profile.currentData()
        self.profile_description.setText(
            f"IA {profile.image_size} px · intervalo {profile.interval:.2f}s · imagem de análise "
            f"{str(profile.analysis_max_side)+' px' if profile.analysis_max_side else 'original'}. "
            "Menor resolução ou frequência reduz o consumo, mas pode perder detalhes e movimentos rápidos. "
            "Revalide os clipes antes de usar o perfil para alertas."
        )

    def refresh(self) -> None:
        status = self.backend.detection_worker.status()
        current = self.backend.detection_worker.performance
        ready = status.get("ready", False)
        self.hardware.setText(str(status.get("device", "Inicializando")))
        self.model.setText(f"Modelo: {status.get('model', self.backend.settings.yolo_model)} · Backend PyTorch · {'Em operação' if ready else 'Inicializando análise'}")
        self.metric_values["average"].setText(f"{status.get('averageInferenceMs', 0):.0f} ms" if ready else "—")
        self.metric_values["p95"].setText(f"{status.get('p95InferenceMs', 0):.0f} ms" if ready else "—")
        rates = [feed.status().get("analysisFps", 0) for feed in self.backend.feeds if feed.status().get("online")]
        self.metric_values["fps"].setText(f"{sum(rates)/len(rates):.1f}" if rates else "—")
        self.metric_values["cameras"].setText(str(status.get("analyzedCameras", 0)) if ready else "—")
        self.statistics.setText(f"Configuração ativa: {current.image_size} px · intervalo {current.interval:.2f}s · limite da imagem {current.analysis_max_side or 'original'}")

    def apply(self) -> None:
        try:
            self.backend.detection_worker.configure_performance(self.profile.currentData())
            self.result.setText("Perfil salvo e aplicado. Confira os FPS e o atraso no diagnóstico.")
            self.refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Perfil não aplicado", str(exc))

    def done(self, result: int) -> None:
        self.timer.stop()
        super().done(result)
