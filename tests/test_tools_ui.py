from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

from camera_ai import desktop_ui
from camera_ai.calibration_ui import CalibrationDialog
from camera_ai.web_dashboard import CameraFeed, DVRSettings


def test_new_tools_do_not_cut_bottom_camera_tiles(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(desktop_ui, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(desktop_ui, "RECORDINGS", tmp_path / "recordings")
    window = desktop_ui.ProfessionalWindow(start_backend=False)
    try:
        window.resize(1366, 768)
        window.show()
        app.processEvents()
        assert window.height() <= 768
        assert len(window.tools_nav.menu().actions()) == 4
        for tile in window.tiles:
            bottom = tile.mapTo(window, tile.rect().bottomRight()).y()
            assert bottom < window.height()-20
            assert tile.height() > 80
    finally:
        window.close(); app.processEvents()


def test_calibration_applies_only_selected_camera(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from camera_ai import web_dashboard
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    feed = CameraFeed(1, "Entrada", "")
    feed._frame = np.zeros((100, 200, 3), np.uint8)
    feed._last_frame_monotonic = time.monotonic()
    applied = []
    backend = SimpleNamespace(feeds=[feed], feed=lambda channel: feed, fall_monitors={1: object()},
                              settings=DVRSettings(host="localhost", user="test", password="test", fall_geometry_profile=""),
                              reload_calibration=applied.append)
    dialog = CalibrationDialog(backend, tmp_path)
    try:
        dialog.canvas.points = [(0,0), (1,0), (1,1), (0,1)]
        dialog.finish_region()
        dialog.rotation.setCurrentIndex(1)
        dialog.save()
        assert applied == [1]
        assert backend.settings.camera_zones(1).rotation == 90
        assert backend.settings.camera_zones(2) is None
    finally:
        dialog.close(); app.processEvents()


def test_all_tools_open_without_network_requests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit
    from camera_ai import web_dashboard
    from camera_ai.alerts_ui import AlertsDialog
    from camera_ai.desktop_dialogs import HealthDialog, PerformanceDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(web_dashboard, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(web_dashboard, "discover_channel_titles", lambda settings: [f"Câmera {i}" for i in range(8)])
    settings = DVRSettings(host="localhost", user="test", password="test", fall_geometry_profile="")
    backend = web_dashboard.DashboardState(settings, native=True, compose_mosaic=False)
    dialogs = []
    try:
        dialogs = [HealthDialog(backend), PerformanceDialog(backend), AlertsDialog(backend.notifications)]
        assert dialogs[0].table.rowCount() == 8
        assert dialogs[2].token.echoMode() == QLineEdit.EchoMode.Password
        assert not dialogs[2].enabled.isChecked()
    finally:
        for dialog in dialogs:
            dialog.close()
        for monitor in backend.fall_monitors.values(): monitor.close()
        app.processEvents()


def test_offline_camera_explains_failure_in_the_empty_tile(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    feed = CameraFeed(9, "Adicional", "rtsp://localhost/live")
    feed._connection_state = "network_error"
    tile = desktop_ui.CameraTile(9)
    try:
        tile.update_status(feed.status())
        assert tile.surface.placeholder == "Equipamento sem resposta"
    finally:
        tile.close(); app.processEvents()
