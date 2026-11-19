from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QTabWidget, QWidget, QTableWidget, QTableWidgetItem, QHeaderView,
)

from camera_ai.notifications import NotificationError, TelegramClient, TelegramSettings
from camera_ai.ui_components import SectionCard, label, scroll_page, stylesheet


class AlertsDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._events = queue.Queue()
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notification-settings")
        self.setWindowTitle("Alertas e recebimentos")
        self.resize(960, 680)
        self.setStyleSheet(parent.styleSheet() if parent else stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        summary = SectionCard("Notificações que chegam a quem cuida", "O Telegram recebe uma imagem ao confirmar um evento e a mensagem é atualizada com o vídeo.")
        self.integration_status = label("", "metricValue", wrap=True)
        self.integration_detail = label("", wrap=True)
        self.last_delivery = label("", wrap=True)
        for widget in (self.integration_status, self.integration_detail, self.last_delivery):
            summary.body.addWidget(widget)
        overview_layout.addWidget(summary)
        flow = SectionCard("Como funciona o recebimento", "Confirmar indica que alguém viu o aviso. Os lembretes são encerrados e a conversa mantém o clipe com as informações essenciais.")
        flow.body.addWidget(label("A confirmação não avalia a condição da pessoa. Verifique o evento registrado.", wrap=True))
        overview_layout.addWidget(flow)
        overview_layout.addStretch()
        self.tabs.addTab(scroll_page(overview), "Visão geral")
        settings = service.settings
        configuration = QWidget()
        form_layout = QVBoxLayout(configuration)
        form_layout.setContentsMargins(0, 0, 0, 0)
        connection = SectionCard("Conectar o Telegram", "Crie um bot no @BotFather, envie /start na conversa com ele e busque seu chat nesta estação.")
        self.enabled = QCheckBox("Ativar envio automático de alertas")
        self.enabled.setChecked(settings.enabled)
        connection.body.addWidget(self.enabled)
        form = QFormLayout()
        form.setSpacing(12)
        self.token = QLineEdit(settings.token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Token do bot · protegido nesta tela")
        form.addRow("Token do bot", self.token)
        self.chats = QLineEdit(",".join(settings.chats))
        self.chats.setPlaceholderText("IDs numéricos separados por vírgula")
        form.addRow("Destinatários", self.chats)
        connection.body.addLayout(form)
        discovery = QHBoxLayout()
        self.find = QPushButton("Buscar meu chat")
        self.find.clicked.connect(self.find_chat)
        self.test = QPushButton("Enviar teste de comunicação")
        self.test.clicked.connect(self.send_test)
        discovery.addWidget(self.find)
        discovery.addWidget(self.test)
        connection.body.addLayout(discovery)
        form_layout.addWidget(connection)
        rules = SectionCard("Canais e lembretes")
        rules_form = QFormLayout()
        rules_form.setSpacing(12)
        self.channels = QLineEdit(",".join(map(str, settings.channels)))
        rules_form.addRow("Canais monitorados", self.channels)
        self.interval = QSpinBox()
        self.interval.setRange(30, 3600)
        self.interval.setValue(settings.reminder_seconds)
        self.interval.setSuffix(" segundos")
        rules_form.addRow("Intervalo dos lembretes", self.interval)
        self.repeats = QSpinBox()
        self.repeats.setRange(0, 5)
        self.repeats.setValue(settings.max_reminders)
        rules_form.addRow("Máximo de lembretes", self.repeats)
        rules.body.addLayout(rules_form)
        self.calibration = QCheckBox("Enviar eventos em calibração, identificados como TESTE")
        self.calibration.setChecked(settings.allow_calibration)
        rules.body.addWidget(self.calibration)
        rules.body.addWidget(label("Sem esta opção, eventos em calibração não são enviados. Selecione os canais já revisados.", wrap=True))
        form_layout.addWidget(rules)
        save = QPushButton("Salvar configuração")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save)
        form_layout.addWidget(save)
        self.result = label("", wrap=True)
        form_layout.addStretch()
        self.tabs.addTab(scroll_page(configuration), "Configuração")
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(label("Entregas e confirmações", "cardTitle"))
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(["Data e horário", "Câmera", "Estado", "Entregas", "Recebimento"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history.setAlternatingRowColors(True)
        history_layout.addWidget(self.history, 1)
        acknowledge = QPushButton("Confirmar recebimento selecionado")
        acknowledge.setEnabled(False)
        self.history.itemSelectionChanged.connect(lambda: acknowledge.setEnabled(self.history.currentItem() is not None))
        acknowledge.clicked.connect(self.acknowledge)
        history_layout.addWidget(acknowledge)
        self.tabs.addTab(history_page, "Histórico")
        layout.addWidget(self.result)
        self.connection_status = label(service.message, wrap=True)
        layout.addWidget(self.connection_status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(1000)
        self.refresh()

    def values(self, *, connection: bool = False) -> TelegramSettings:
        try:
            settings = TelegramSettings(
                token=self.token.text().strip(), chats=tuple(item.strip() for item in self.chats.text().split(",") if item.strip()),
                enabled=self.enabled.isChecked(), allow_calibration=self.calibration.isChecked(),
                channels=tuple(int(item.strip()) for item in self.channels.text().split(",") if item.strip()),
                reminder_seconds=self.interval.value(), max_reminders=self.repeats.value(),
            )
        except ValueError:
            raise ValueError("Informe números de canais separados por vírgula.") from None
        settings.validate(require_connection=connection)
        return settings

    def save(self) -> None:
        try:
            self.service.configure(self.values())
            self.result.setText("Configuração salva. " + self.service.message)
        except (OSError, ValueError):
            QMessageBox.warning(self, "Configuração não salva", "Confira o token, os IDs de chat, os canais e a permissão de escrita do .env.")

    def _background(self, operation) -> None:
        self.find.setEnabled(False); self.test.setEnabled(False)
        self.result.setText("Comunicando com o Telegram…")
        def execute():
            try:
                self._events.put(("success", operation()))
            except (NotificationError, OSError, ValueError) as exc:
                self._events.put(("error", str(exc) if isinstance(exc, (NotificationError, ValueError)) else "Não foi possível concluir a operação."))
            except (KeyError, TypeError):
                self._events.put(("error", "Resposta inesperada do Telegram; a configuração não foi alterada."))
        self._workers.submit(execute)

    def find_chat(self) -> None:
        if self.service.settings.enabled:
            self.result.setText("Desative o envio e salve antes de buscar chats, para não disputar a conexão do bot.")
            return
        token = self.token.text().strip()
        try:
            TelegramSettings(token=token).validate()
            if not token:
                raise ValueError("Preencha o token do bot primeiro.")
        except ValueError as exc:
            self.result.setText(str(exc)); return
        def find():
            updates = TelegramClient(token).call("getUpdates", {"timeout": 0, "allowed_updates": ["message"], "limit": 100}) or []
            ids = sorted({str(item["message"]["chat"]["id"]) for item in updates if "message" in item})
            return {"chats": ids}
        self._background(find)

    def send_test(self) -> None:
        try:
            settings = self.values(connection=True)
        except ValueError as exc:
            self.result.setText(str(exc)); return
        def send():
            client = TelegramClient(settings.token)
            for chat in settings.chats:
                client.call("sendMessage", {"chat_id": chat, "parse_mode": "HTML",
                                             "text": "<b>✓ UVision conectado</b>\nEste chat está pronto para receber avisos.\n<i>Teste de comunicação · nenhum evento de queda.</i>"})
            message = "Teste enviado. Alertas automáticos ativos." if self.service.settings.enabled else "Teste enviado. Para receber alertas automáticos, ative o envio e salve a configuração."
            return {"message": message}
        self._background(send)

    def refresh(self) -> None:
        self.connection_status.setText(self.service.message)
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            self.find.setEnabled(True); self.test.setEnabled(True)
            if kind == "error":
                self.result.setText(value)
            elif "chats" in value:
                self.chats.setText(",".join(value["chats"]))
                self.result.setText("Chats encontrados; confira os destinos e salve." if value["chats"] else "Envie /start ao seu bot no Telegram e tente novamente.")
            else:
                self.result.setText(value["message"])
        records = self.service.store.alerts()
        selected = self.history.currentItem().data(Qt.ItemDataRole.UserRole) if self.history.currentItem() else None
        self.history.setRowCount(len(records))
        for row, event in enumerate(reversed(records)):
            deliveries = event.get("deliveries", {})
            sent = sum(bool(item.get("initialSent")) for item in deliveries.values())
            errors = [item[key] for item in deliveries.values() for key in ("error", "maintenanceError") if item.get(key)]
            state = {"pending": "Pendente", "sent": "Enviado", "delivery_error": "Falha de entrega", "acknowledged": "Recebido", "expired": "Expirado"}.get(event["status"], event["status"])
            occurred = datetime.fromisoformat(event["createdAt"]).astimezone().strftime("%d/%m/%Y %H:%M:%S")
            received = datetime.fromisoformat(event["acknowledgedAt"]).astimezone().strftime("%H:%M:%S") if event.get("acknowledgedAt") else "Aguardando"
            for column, value in enumerate([occurred, f"CH {event['channel']:02d} · {event['cameraName']}", state, str(sent), received]):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, event["id"])
                item.setToolTip(errors[0] if errors else value)
                self.history.setItem(row, column, item)
            if event["id"] == selected:
                self.history.selectRow(row)
        settings = self.service.settings
        self.integration_status.setText("Integração ativa" if settings.enabled else "Alertas desativados")
        self.integration_detail.setText(f"{len(settings.chats)} destinatário(s) · Canais: {', '.join(map(str, settings.channels))} · Lembretes a cada {settings.reminder_seconds}s")
        if records:
            latest = records[-1]
            stamp = datetime.fromisoformat(latest["createdAt"]).astimezone().strftime("%d/%m às %H:%M")
            self.last_delivery.setText(f"Último evento · CH {latest['channel']:02d} · {stamp}")
        else:
            self.last_delivery.setText("Nenhum alerta registrado. Use o teste de comunicação para verificar o destino.")

    def acknowledge(self) -> None:
        item = self.history.currentItem()
        if item:
            try:
                self.service.store.acknowledge(item.data(Qt.ItemDataRole.UserRole))
                self.result.setText("Recebimento confirmado. Os lembretes deste evento foram encerrados.")
                self.refresh()
            except (OSError, ValueError):
                self.result.setText("Não foi possível salvar a confirmação de recebimento.")

    def done(self, result: int) -> None:
        self.timer.stop()
        self._workers.shutdown(wait=False, cancel_futures=True)
        super().done(result)
