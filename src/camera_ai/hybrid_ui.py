from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QApplication

from camera_ai.presentation import PresentationBridge


def register_scheme():
    if QWebEngineUrlScheme.schemeByName(b"uvision").name():
        return
    scheme = QWebEngineUrlScheme(b"uvision")
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    scheme.setDefaultPort(443)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalScheme |
                    QWebEngineUrlScheme.Flag.LocalAccessAllowed | QWebEngineUrlScheme.Flag.CorsEnabled |
                    QWebEngineUrlScheme.Flag.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = (url.scheme() == "uvision" and url.host() == "app") or (url.scheme() == "qrc" and url.path() == "/qtwebchannel/qwebchannel.js") or url.scheme() == "data"
        if not allowed:
            info.block(True)


class AppPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() == "uvision" and url.host() == "app"


class LocalResources(QWebEngineUrlSchemeHandler):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.assets = Path(__file__).with_name("frontend_dist").resolve()

    def requestStarted(self, job):
        path = unquote(job.requestUrl().path()).lstrip("/")
        if job.requestMethod() != b"GET" or job.requestUrl().host() != "app":
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        if path.startswith("image/"):
            data = self.bridge.image(path[6:])
            mime = "image/jpeg"
        else:
            try:
                if len(path) > 1024 or "\x00" in path:
                    raise ValueError("Invalid resource")
                target = (self.assets/(path or "index.html")).resolve()
            except (ValueError, OSError):
                job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
                return
            if not target.is_relative_to(self.assets) or not target.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            data = target.read_bytes()
            mime = {".js": "application/javascript", ".css": "text/css", ".woff2": "font/woff2"}.get(target.suffix, mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        if data is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode(), buffer)


class VisionWindow(QMainWindow):
    def __init__(self, *, start_backend=True, bridge=None):
        super().__init__()
        self.setWindowTitle("UStudio Vision Control 3.0")
        self.resize(1600, 960)
        self.setMinimumSize(1000, 650)
        if not Path(__file__).with_name("frontend_dist").joinpath("index.html").is_file():
            raise RuntimeError("Arquivos da interface ausentes. Reconstrua ou reinstale a interface desktop.")
        self._closing = False
        self.bridge = bridge or PresentationBridge(start_backend=start_backend, parent=self)
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self.interceptor = LocalOnly(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.resources = LocalResources(self.bridge, self.profile)
        self.profile.installUrlSchemeHandler(b"uvision", self.resources)
        self.page = AppPage(self.profile, self.view)
        self.page.setBackgroundColor(Qt.GlobalColor.black)
        self.view.setPage(self.page)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("vision", self.bridge)
        self.page.setWebChannel(self.channel)
        self.bridge.windowAction.connect(self.window_action)
        self.bridge.closingFinished.connect(self._closed)
        self.setCentralWidget(self.view)
        self.view.setUrl(QUrl("uvision://app/index.html"))
        for key, action in (("F11", self.toggle_fullscreen), ("Escape", self.escape)):
            shortcut = QAction(self)
            shortcut.setShortcut(QKeySequence(key))
            shortcut.triggered.connect(action)
            self.addAction(shortcut)

    def window_action(self, action):
        if action == "fullscreen":
            self.toggle_fullscreen()

    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def escape(self):
        if self.isFullScreen():
            self.showMaximized()
            return
        self.page.runJavaScript("""if(document.querySelector('[role=dialog]')) {
            document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true,cancelable:true}));
        } else {window.dispatchEvent(new CustomEvent('vision-escape'));}""")

    def closeEvent(self, event):
        event.ignore()
        if not self._closing:
            self._closing = True
            self.setEnabled(False)
            self.bridge.stop()

    def _closed(self):
        self.hide()
        QApplication.instance().quit()
