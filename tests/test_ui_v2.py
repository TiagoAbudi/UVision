from __future__ import annotations

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from camera_ai import desktop_ui
from camera_ai.ui_components import WorkspaceLayout


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_ui, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(desktop_ui, "RECORDINGS", tmp_path / "recordings")
    widget = desktop_ui.ProfessionalWindow(start_backend=False)
    yield widget
    widget.backend = None
    widget.close()
    app.processEvents()


def test_layout_persistence_pagination_and_reordering(window, app):
    window.show()
    original = tuple(id(tile) for tile in window.tiles)
    window.set_grid_layout("2x2")
    assert window.camera_grid.count() == 4
    window.change_grid_page(2)
    assert window.camera_grid.count() == 1
    assert window.camera_grid.itemAt(0).widget().channel == 9
    window.reorder_camera(1, 9)
    stored = WorkspaceLayout.load(window._layout_path)
    assert stored.order[0] == 9
    assert stored.mode == "2x2"
    window.set_grid_layout("3x3")
    assert window.camera_grid.count() == 9
    assert window.camera_grid.itemAt(0).widget().channel == 9
    assert tuple(id(tile) for tile in window.tiles) == original


@pytest.mark.parametrize("content", [
    None,
    {"mode": "5x5", "order": list(range(1, 10)), "collapsed": False},
    {"mode": "3x3", "order": [1]*9, "collapsed": False},
    {"mode": "3x3", "order": list(range(1, 10)), "collapsed": "false"},
])
def test_invalid_preferences_are_rejected(tmp_path, content):
    target = tmp_path / "layout.json"
    target.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        WorkspaceLayout.load(target)


@pytest.mark.parametrize("width,height", [(960, 620), (1280, 672), (1366, 768), (1920, 1008), (2560, 1392), (3440, 1392), (3840, 2112)])
def test_nine_tiles_fit_available_window(window, app, width, height):
    window.resize(width, height)
    window.show()
    for _ in range(4):
        app.processEvents()
    assert window.width() <= width
    assert window.height() <= height
    for tile in window.tiles:
        assert tile.isVisible()
        bottom = tile.mapTo(window, tile.rect().bottomRight())
        assert bottom.y() < window.statusbar_widget.y()
        assert bottom.x() < window.width()
        assert tile.surface.height() >= 60
        assert tile.title.width() > 20
        assert tile.title.text()
    assert window.breadcrumb.width() > 50


def test_noncontiguous_channels_and_navigation_reuse_backend(window, app):
    feeds = [SimpleNamespace(channel=channel) for channel in (1, 4, 9)]
    backend = SimpleNamespace(feeds=feeds)
    window.backend = backend
    window.show_mosaic()
    assert [window.camera_grid.itemAt(i).widget().channel for i in range(3)] == [1, 4, 9]
    window.show_library()
    window.show_mosaic()
    assert window.backend is backend
    assert window.pages.count() == 3


def test_canvas_drag_undo_preserves_normalized_coordinates(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from camera_ai.calibration_ui import CalibrationCanvas
    canvas = CalibrationCanvas(np.zeros((200, 400, 3), np.uint8))
    canvas.resize(800, 600)
    canvas.floor = [[[.25, .25], [.75, .25], [.75, .75]]]
    canvas.undo()
    assert len(canvas.floor) == 1
    canvas.show()
    app.processEvents()
    try:
        canvas.zoom = 1.5
        canvas.pan = QPointF(20, 10)
        rect = canvas.image_rect()
        start = QPoint(round(rect.x()+.25*rect.width()), round(rect.y()+.25*rect.height()))
        end = QPoint(round(rect.x()+.4*rect.width()), round(rect.y()+.4*rect.height()))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
        assert canvas.floor[0][0] == pytest.approx((.4, .4), abs=.002)
        canvas.undo()
        assert canvas.floor[0][0] == [.25, .25]
        canvas.reset_view()
        assert canvas.zoom == 1
        assert canvas.pan == QPointF()
    finally:
        canvas.close()


def test_calibration_stages_block_open_polygon_and_save_exact_coordinates(app, tmp_path, monkeypatch):
    from camera_ai import web_dashboard
    from camera_ai.calibration_ui import CalibrationDialog
    from camera_ai.web_dashboard import CameraFeed, DVRSettings
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    feed = CameraFeed(4, "Cozinha", "")
    feed._frame = np.zeros((200, 400, 3), np.uint8)
    feed._last_frame_monotonic = time.monotonic()
    applied = []
    backend = SimpleNamespace(feeds=[feed], fall_monitors={4: object()}, feed=lambda ch: feed,
                              settings=DVRSettings(host="localhost", user="test", password="test", fall_geometry_profile=""),
                              reload_calibration=applied.append)
    dialog = CalibrationDialog(backend, tmp_path)
    try:
        dialog.set_step(1)
        points = [[.05, .1], [.95, .1], [.9, .9]]
        dialog.canvas.points = points.copy()
        dialog.set_step(4)
        assert dialog.step == 1
        dialog.finish_region()
        dialog.set_step(4)
        dialog.rotation.setCurrentIndex(1)
        dialog.seconds.setValue(4)
        dialog.save()
        saved = json.loads((tmp_path / "calibration/ch04-zones.json").read_text())
        assert saved["floor"] == [points]
        assert saved["coordinateSystem"] == "raw_image_normalized_xy"
        assert saved["rotationDegrees"] == 90
        assert saved["groundConfirmSeconds"] == 4
        assert applied == [4]
        dialog.canvas.points = [[.1, .2]]
        original = dialog.canvas
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
        dialog.load_camera()
        assert dialog.canvas is original
        assert dialog.canvas.points == [[.1, .2]]
    finally:
        dialog.close()


def test_diagnostic_reconnect_uses_selected_channel_after_sort(app):
    from PySide6.QtCore import Qt
    from camera_ai.desktop_dialogs import HealthDialog
    from camera_ai.web_dashboard import CameraFeed
    calls = []
    feeds = [CameraFeed(channel, name, "") for channel, name in ((1, "Entrada"), (4, "Cozinha"), (9, "Adicional"))]
    for feed in feeds:
        feed.reconnect = lambda channel=feed.channel: calls.append(channel)
    backend = SimpleNamespace(feeds=feeds, feed=lambda ch: next((f for f in feeds if f.channel == ch), None),
                              recovery=SimpleNamespace(request=lambda: None, message=""))
    dialog = HealthDialog(backend)
    try:
        dialog.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        dialog.select_channel(4)
        dialog._retry()
        assert calls == [4]
        assert dialog.selected_channel() == 4
    finally:
        dialog.close()


def test_all_tool_pages_share_one_backend_and_preserve_settings(window, app, tmp_path, monkeypatch):
    from camera_ai import web_dashboard
    from camera_ai.web_dashboard import DashboardState, DVRSettings
    monkeypatch.setattr(web_dashboard, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(web_dashboard, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(web_dashboard, "discover_channel_titles", lambda settings: [f"Câmera {i}" for i in range(8)])
    backend = DashboardState(DVRSettings(host="localhost", user="test", password="test", fall_geometry_profile=""), native=True, compose_mosaic=False)
    window.backend = backend
    profile = backend.detection_worker.performance
    try:
        for key in ("health", "calibration", "alerts", "performance", "about"):
            window._show_tool(key)
            assert window.backend is backend
            assert not window._tool_widgets[key].isWindow()
            assert window.pages.currentWidget() is window._tool_pages[key]
            for width, height in ((960, 620), (1280, 672)):
                window.resize(width, height)
                window.show()
                for _ in range(4):
                    app.processEvents()
                tool = window._tool_widgets[key]
                assert window.size().width() <= width
                assert window.size().height() <= height
                assert tool.width() <= window.pages.width()
                assert tool.height() <= window.pages.height()
        count = window.pages.count()
        window.show_health()
        window.show_performance()
        assert window.pages.count() == count
        assert backend.detection_worker.performance == profile
        assert not backend.detection_worker._thread.is_alive()
        assert not (tmp_path / "runtime/performance.json").exists()
        assert not (tmp_path / "calibration").exists()
    finally:
        for monitor in backend.fall_monitors.values():
            monitor.close()


def test_video_surface_keeps_native_aspect(app):
    from camera_ai.desktop_ui import VideoSurface
    surface = VideoSurface("")
    surface.resize(500, 400)
    try:
        surface.set_frame(np.zeros((1080, 1920, 3), np.uint8))
        assert surface.image.width()/surface.image.height() == pytest.approx(16/9, abs=.01)
        surface.set_frame(np.zeros((1920, 1080, 3), np.uint8))
        assert surface.image.width()/surface.image.height() == pytest.approx(9/16, abs=.01)
    finally:
        surface.close()


def test_event_date_filter_sort_playback_speed_and_seek(window, app):
    import cv2
    from datetime import datetime, timedelta
    from PySide6.QtCore import Qt
    desktop_ui.RECORDINGS.mkdir(parents=True, exist_ok=True)
    names = []
    for days in (0, 1):
        stamp = datetime.now()-timedelta(days=days)
        name = f"queda-ch04_{stamp:%Y-%m-%d}_12-00-00.mp4"
        names.append(name)
        writer = cv2.VideoWriter(str(desktop_ui.RECORDINGS/name), cv2.VideoWriter_fourcc(*"mp4v"), 2, (64, 64))
        assert writer.isOpened()
        for _ in range(10):
            writer.write(np.zeros((64, 64, 3), np.uint8))
        writer.release()
    window._refresh_recordings()
    window.show_library()
    window.period_filter.setCurrentIndex(1)
    assert window.recording_list.count() == 1
    assert window._current_clip.name == names[0]
    window.period_filter.setCurrentIndex(0)
    window.sort_filter.setCurrentIndex(1)
    assert window.recording_list.item(0).data(Qt.ItemDataRole.UserRole).endswith(names[1])
    window._seek_video(4)
    assert window._clip_position == 4
    window.speed.setCurrentIndex(window.speed.findData(2.0))
    window._toggle_playback()
    assert window._player_timer.interval() == 250
    window._pause_playback()
    assert not window._player_timer.isActive()


def test_alert_receipt_from_any_table_cell_preserves_event_identity(app, tmp_path):
    from camera_ai.alerts_ui import AlertsDialog
    from camera_ai.event_store import EventStore
    from camera_ai.notifications import TelegramSettings
    store = EventStore(tmp_path / "events.json")
    event = store.create_alert(4, 1, "Cozinha", shadow=True)
    service = SimpleNamespace(settings=TelegramSettings(), message="Alertas desativados", store=store)
    dialog = AlertsDialog(service)
    try:
        dialog.history.setCurrentCell(0, 4)
        dialog.acknowledge()
        assert store.alert(event["id"])["status"] == "acknowledged"
        assert "Recebimento confirmado" in dialog.result.text()
        assert dialog.history.item(0, 2).text() == "Recebido"
    finally:
        dialog.close()


def test_palette_filters_commands_and_executes_the_selected_action(app):
    from camera_ai.ui_components import CommandPalette
    calls = []
    palette = CommandPalette([("Visão geral", lambda: calls.append("mosaic")),
                              ("Calibração", lambda: calls.append("calibration"))])
    palette.search.setText("CALIB")
    assert palette.results.count() == 1
    palette.activate()
    assert calls == ["calibration"]
    palette.search.setText("inexistente")
    assert palette.results.count() == 0
    palette.activate()
    assert calls == ["calibration"]
