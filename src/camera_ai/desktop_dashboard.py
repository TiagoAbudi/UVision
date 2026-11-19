from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, Button, Canvas, Frame, Label, Listbox, Scrollbar, StringVar, Tk, messagebox

import cv2
from PIL import Image, ImageTk

from camera_ai.web_dashboard import DVRSettings, DashboardState


PROJECT_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_DIR / "logs"
RECORDINGS_DIR = PROJECT_DIR / "recordings" / "falls"
LOGGER = logging.getLogger("camera_ai.desktop")

BG = "#080d14"
PANEL = "#101720"
PANEL_SOFT = "#141d28"
BORDER = "#243140"
TEXT = "#eef3f8"
MUTED = "#8f9dad"
ACCENT = "#3dcba5"
AMBER = "#ffbd59"
DANGER = "#ff6b72"

FALL_LABELS = {
    "normal": "NORMAL",
    "possible_fall": "POSSÍVEL QUEDA",
    "down": "NO CHÃO",
    "alert_pending": "ALERTA PENDENTE",
}


def fit_size(source_width: int, source_height: int, area_width: int, area_height: int) -> tuple[int, int]:
    if min(source_width, source_height, area_width, area_height) <= 0:
        return 1, 1
    scale = min(area_width / source_width, area_height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def mosaic_tile_at(
    x: int,
    y: int,
    bounds: tuple[int, int, int, int],
    camera_count: int,
) -> int | None:
    left, top, width, height = bounds
    if width <= 0 or height <= 0 or not (left <= x < left + width and top <= y < top + height):
        return None
    column = min(2, int((x - left) * 3 / width))
    row = min(2, int((y - top) * 3 / height))
    index = row * 3 + column
    return index if index < camera_count else None


def configure_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOGS_DIR / "desktop-app.log",
        maxBytes=2_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


class SingleInstance:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        import msvcrt

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is None or os.name != "nt":
            return
        import msvcrt

        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


class DesktopDashboard:
    RENDER_INTERVAL_MS = 50
    STATUS_INTERVAL_MS = 500

    def __init__(self, root: Tk) -> None:
        self.root = root
        self.state: DashboardState | None = None
        self.selected_index: int | None = None
        self.current_view = "mosaic"
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._closing = False
        self._photo: ImageTk.PhotoImage | None = None
        self._image_bounds = (0, 0, 0, 0)
        self._last_sequence = -1
        self._last_status_at = 0
        self._fullscreen = False
        self._ptz_action: str | None = None
        self._video_paths: list[Path] = []
        self._video_capture: cv2.VideoCapture | None = None
        self._video_photo: ImageTk.PhotoImage | None = None
        self._video_after_id: str | None = None
        self._video_playing = False
        self._video_current_path: Path | None = None

        self.status_var = StringVar(value="Inicializando câmeras e inteligência artificial…")
        self.camera_metric = StringVar(value="0/9")
        self.people_metric = StringVar(value="—")
        self.ai_metric = StringVar(value="—")
        self.fall_metric = StringVar(value="CÂMERAS · CALIBRANDO")
        self.view_title = StringVar(value="TODAS AS CÂMERAS")
        self.video_name_var = StringVar(value="Selecione uma gravação")
        self.video_meta_var = StringVar(value="Os próximos eventos aparecerão aqui automaticamente")

        self._build_window()
        self.root.after_idle(lambda: self.root.state("zoomed"))
        self.root.after(100, self._poll_events)
        self.root.after(120, self._render)
        self.root.after(150, self._refresh_status)
        threading.Thread(target=self._start_backend, name="desktop-backend-start", daemon=True).start()

    def _build_window_legacy(self) -> None:
        self.root.title("UStudio Vision")
        self.root.configure(bg=BG)
        self.root.geometry("1600x920")
        self.root.minsize(1024, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Control-Key-1>", lambda _event: self.show_mosaic())
        self.root.bind("<Control-Key-2>", lambda _event: self.show_video_library())

        top = Frame(self.root, bg=BG, height=68, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill=X, padx=16, pady=(14, 8))
        top.pack_propagate(False)

        brand = Frame(top, bg=BG)
        brand.pack(side=LEFT, fill=BOTH, padx=14)
        Label(brand, text="USTUDIO", bg=BG, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(side=LEFT, pady=13)
        Label(brand, text="VISION", bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(8, 0), pady=16)
        Label(brand, textvariable=self.view_title, bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(24, 0), pady=16)

        metrics = Frame(top, bg=BG)
        metrics.pack(side=RIGHT, padx=10, pady=7)
        self._metric(metrics, self.camera_metric, "CÂMERAS", TEXT)
        self._metric(metrics, self.people_metric, "PESSOAS", AMBER)
        self._metric(metrics, self.ai_metric, "IA / MS", ACCENT)
        self._metric(metrics, self.fall_metric, "QUEDA", ACCENT, wide=True)

        # Canvas image items do not change the widget's requested size. A Label
        # would request the Full HD image dimensions and squeeze the controls,
        # which could push the last camera row below the visible client area.
        self.image_canvas = Canvas(
            self.root,
            bg="#030507",
            cursor="hand2",
            highlightthickness=0,
            borderwidth=0,
        )
        self.image_canvas.pack(fill=BOTH, expand=True, padx=16, pady=0)
        self.image_canvas.bind("<Button-1>", self._image_clicked)
        self.image_canvas.bind("<Configure>", lambda _event: setattr(self, "_last_sequence", -1))

        bottom = Frame(self.root, bg=BG, height=86)
        bottom.pack(fill=X, padx=16, pady=(8, 12))
        bottom.pack_propagate(False)

        left_controls = Frame(bottom, bg=BG)
        left_controls.pack(side=LEFT, fill=BOTH, expand=True)
        self.back_button = self._button(left_controls, "← MOSAICO", self.show_mosaic, width=12)
        self.back_button.pack(side=LEFT, padx=(0, 8), pady=7)

        self.channel_buttons: list[Button] = []
        for index in range(9):
            button = self._button(
                left_controls,
                f"CH {index + 1:02d}",
                lambda selected=index: self.select_camera(selected),
                width=7,
            )
            button.pack(side=LEFT, padx=3, pady=7)
            self.channel_buttons.append(button)

        self.ptz_frame = Frame(bottom, bg=PANEL, highlightbackground=ACCENT, highlightthickness=1)
        Label(self.ptz_frame, text="PTZ", bg=PANEL, fg=ACCENT, font=("Segoe UI", 8, "bold")).grid(row=0, column=0, rowspan=2, padx=8)
        for column, (label, action) in enumerate((("←", "left"), ("↑", "up"), ("⌂", "home"), ("↓", "down"), ("→", "right")), start=1):
            button = Button(
                self.ptz_frame,
                text=label,
                width=3,
                bg=PANEL_SOFT,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground=BG,
                relief="flat",
                font=("Segoe UI", 12, "bold"),
            )
            button.grid(row=0, column=column, padx=2, pady=8)
            if action == "home":
                button.configure(command=lambda: self._send_ptz("home"))
            else:
                button.bind("<ButtonPress-1>", lambda _event, value=action: self._start_ptz(value))
                button.bind("<ButtonRelease-1>", lambda _event: self._stop_ptz())
                button.bind("<Leave>", lambda _event: self._stop_ptz())

        status = Frame(self.root, bg=PANEL, height=28)
        status.pack(fill=X, side="bottom")
        status.pack_propagate(False)
        Label(status, textvariable=self.status_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side=LEFT, padx=18, pady=5)
        Label(status, text="F11: tela cheia · ESC: voltar/fechar", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side=RIGHT, padx=18, pady=5)

    def _build_window(self) -> None:
        self.root.title("UStudio Vision")
        self.root.configure(bg=BG)
        self.root.geometry("1600x920")
        self.root.minsize(1180, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<F11>", self._toggle_fullscreen)

        shell = Frame(self.root, bg=BG)
        shell.pack(fill=BOTH, expand=True)

        sidebar = Frame(shell, bg="#0c121b", width=214, highlightbackground=BORDER, highlightthickness=1)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)

        brand = Frame(sidebar, bg="#0c121b", height=86)
        brand.pack(fill=X, padx=18, pady=(10, 4))
        brand.pack_propagate(False)
        Label(brand, text="◆", bg="#0c121b", fg=ACCENT, font=("Segoe UI Symbol", 18, "bold")).pack(side=LEFT, pady=18)
        brand_text = Frame(brand, bg="#0c121b")
        brand_text.pack(side=LEFT, padx=10, pady=17)
        Label(brand_text, text="USTUDIO", bg="#0c121b", fg=TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        Label(brand_text, text="VISION · DESKTOP", bg="#0c121b", fg=ACCENT, font=("Segoe UI", 7, "bold")).pack(anchor="w")

        Label(sidebar, text="NAVEGAÇÃO", bg="#0c121b", fg="#607084", font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=20, pady=(8, 6))
        self.mosaic_button = self._sidebar_button(sidebar, "▦   Visão geral", self.show_mosaic)
        self.mosaic_button.pack(fill=X, padx=12, pady=3)
        self.videos_button = self._sidebar_button(sidebar, "▶   Vídeos de quedas", self.show_video_library)
        self.videos_button.pack(fill=X, padx=12, pady=3)

        Label(sidebar, text="CÂMERAS", bg="#0c121b", fg="#607084", font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=20, pady=(18, 6))
        self.channel_buttons = []
        for index in range(9):
            button = self._sidebar_button(
                sidebar,
                f"CH {index + 1:02d}   Conectando…",
                lambda selected=index: self.select_camera(selected),
                compact=True,
            )
            button.pack(fill=X, padx=12, pady=1)
            self.channel_buttons.append(button)

        sidebar_footer = Frame(sidebar, bg="#0c121b")
        sidebar_footer.pack(side="bottom", fill=X, padx=16, pady=16)
        Label(sidebar_footer, text="●  SISTEMA LOCAL", bg="#0c121b", fg=ACCENT, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        Label(sidebar_footer, text="Sem servidor web", bg="#0c121b", fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

        main = Frame(shell, bg=BG)
        main.pack(side=RIGHT, fill=BOTH, expand=True)

        header = Frame(main, bg=BG, height=82)
        header.pack(fill=X, padx=20, pady=(12, 8))
        header.pack_propagate(False)
        title_block = Frame(header, bg=BG)
        title_block.pack(side=LEFT, fill=BOTH, expand=True, padx=4)
        Label(title_block, textvariable=self.view_title, bg=BG, fg=TEXT, font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(12, 0))
        Label(title_block, text="Monitoramento local em tempo real", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

        metrics = Frame(header, bg=BG)
        metrics.pack(side=RIGHT, pady=7)
        self._metric(metrics, self.camera_metric, "CÂMERAS", TEXT)
        self._metric(metrics, self.people_metric, "PESSOAS", AMBER)
        self._metric(metrics, self.ai_metric, "IA / MS", ACCENT)
        self._metric(metrics, self.fall_metric, "QUEDAS · CÂMERAS", ACCENT, wide=True)

        self.content_host = Frame(main, bg="#05080c", highlightbackground=BORDER, highlightthickness=1)
        self.content_host.pack(fill=BOTH, expand=True, padx=20, pady=(0, 10))

        self.image_canvas = Canvas(
            self.content_host,
            bg="#030507",
            cursor="hand2",
            highlightthickness=0,
            borderwidth=0,
        )
        self.image_canvas.pack(fill=BOTH, expand=True)
        self.image_canvas.bind("<Button-1>", self._image_clicked)
        self.image_canvas.bind("<Configure>", lambda _event: setattr(self, "_last_sequence", -1))

        self.video_library = self._build_video_library(self.content_host)

        self.ptz_frame = Frame(self.content_host, bg=PANEL, highlightbackground=ACCENT, highlightthickness=1)
        Label(self.ptz_frame, text="CONTROLE PTZ", bg=PANEL, fg=ACCENT, font=("Segoe UI", 8, "bold")).grid(row=0, column=0, columnspan=5, padx=10, pady=(8, 3))
        for column, (label, action) in enumerate((("←", "left"), ("↑", "up"), ("⌂", "home"), ("↓", "down"), ("→", "right"))):
            button = Button(
                self.ptz_frame,
                text=label,
                width=3,
                bg=PANEL_SOFT,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground=BG,
                relief="flat",
                font=("Segoe UI", 12, "bold"),
                cursor="hand2",
            )
            button.grid(row=1, column=column, padx=3, pady=(3, 9))
            if action == "home":
                button.configure(command=lambda: self._send_ptz("home"))
            else:
                button.bind("<ButtonPress-1>", lambda _event, value=action: self._start_ptz(value))
                button.bind("<ButtonRelease-1>", lambda _event: self._stop_ptz())
                button.bind("<Leave>", lambda _event: self._stop_ptz())

        status = Frame(main, bg=PANEL, height=32)
        status.pack(fill=X, side="bottom")
        status.pack_propagate(False)
        Label(status, textvariable=self.status_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side=LEFT, padx=20, pady=7)
        Label(status, text="F11  TELA CHEIA     ESC  VOLTAR", bg=PANEL, fg="#66788c", font=("Segoe UI", 7, "bold")).pack(side=RIGHT, padx=20, pady=7)
        self._refresh_navigation()

    @staticmethod
    def _sidebar_button(parent: Frame, text: str, command, *, compact: bool = False) -> Button:
        return Button(
            parent,
            text=text,
            command=command,
            anchor="w",
            padx=12,
            pady=5 if compact else 9,
            bg="#0c121b",
            fg=MUTED,
            activebackground=PANEL_SOFT,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 8 if compact else 9, "bold"),
            cursor="hand2",
        )

    @staticmethod
    def _metric(parent: Frame, variable: StringVar, label: str, color: str, *, wide: bool = False) -> None:
        block = Frame(parent, bg=PANEL, width=200 if wide else 78, height=50, highlightbackground=BORDER, highlightthickness=1)
        block.pack(side=LEFT, padx=3)
        block.pack_propagate(False)
        Label(block, textvariable=variable, bg=PANEL, fg=color, font=("Segoe UI", 9 if wide else 11, "bold")).pack(pady=(5, 0))
        Label(block, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 7, "bold")).pack()

    @staticmethod
    def _button(parent: Frame, text: str, command, *, width: int) -> Button:
        return Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=PANEL,
            fg=MUTED,
            activebackground=ACCENT,
            activeforeground=BG,
            disabledforeground="#586575",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )

    def _build_video_library(self, parent: Frame) -> Frame:
        library = Frame(parent, bg=BG)

        intro = Frame(library, bg=BG, height=72)
        intro.pack(fill=X, padx=20, pady=(16, 8))
        intro.pack_propagate(False)
        text = Frame(intro, bg=BG)
        text.pack(side=LEFT, fill=BOTH, expand=True)
        Label(text, text="Histórico de quedas", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(6, 0))
        Label(text, text="Clipes locais com 8 segundos antes e depois do evento", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
        open_button = self._button(intro, "ABRIR PASTA", self._open_recordings_folder, width=14)
        open_button.configure(bg=PANEL_SOFT, fg=ACCENT, pady=8)
        open_button.pack(side=RIGHT, pady=11)

        body = Frame(library, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=20, pady=(0, 20))

        list_card = Frame(body, bg=PANEL, width=340, highlightbackground=BORDER, highlightthickness=1)
        list_card.pack(side=LEFT, fill=Y, padx=(0, 12))
        list_card.pack_propagate(False)
        Label(list_card, text="GRAVAÇÕES", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(16, 9))

        list_body = Frame(list_card, bg=PANEL)
        list_body.pack(fill=BOTH, expand=True, padx=12, pady=(0, 10))
        scrollbar = Scrollbar(list_body, orient="vertical")
        scrollbar.pack(side=RIGHT, fill=Y)
        self.video_list = Listbox(
            list_body,
            bg="#0c121b",
            fg=TEXT,
            selectbackground="#1c4f46",
            selectforeground="#ffffff",
            activestyle="none",
            highlightthickness=0,
            borderwidth=0,
            font=("Segoe UI", 9),
            yscrollcommand=scrollbar.set,
        )
        self.video_list.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.configure(command=self.video_list.yview)
        self.video_list.bind("<<ListboxSelect>>", self._video_selected)
        self.video_list.bind("<Double-Button-1>", lambda _event: self._play_selected_video())

        controls = Frame(list_card, bg=PANEL)
        controls.pack(fill=X, padx=12, pady=(0, 12))
        self.video_play_button = self._button(controls, "▶  REPRODUZIR", self._toggle_video_playback, width=15)
        self.video_play_button.configure(bg=ACCENT, fg="#06130f", activebackground="#64dfbe", activeforeground="#06130f", pady=8)
        self.video_play_button.pack(side=LEFT, fill=X, expand=True)
        refresh = self._button(controls, "↻", self._refresh_video_list, width=4)
        refresh.configure(bg=PANEL_SOFT, fg=TEXT, pady=8)
        refresh.pack(side=RIGHT, padx=(8, 0))

        player_card = Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        player_card.pack(side=RIGHT, fill=BOTH, expand=True)
        player_header = Frame(player_card, bg=PANEL, height=64)
        player_header.pack(fill=X, padx=16, pady=(4, 0))
        player_header.pack_propagate(False)
        Label(player_header, textvariable=self.video_name_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(9, 0))
        Label(player_header, textvariable=self.video_meta_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))
        self.video_canvas = Canvas(player_card, bg="#020406", highlightthickness=0, borderwidth=0)
        self.video_canvas.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        self.video_canvas.create_text(
            20,
            20,
            text="Selecione um vídeo para visualizar",
            anchor="nw",
            fill=MUTED,
            font=("Segoe UI", 11),
            tags=("video-placeholder",),
        )
        return library

    def _refresh_navigation(self) -> None:
        mosaic_active = self.current_view == "mosaic"
        videos_active = self.current_view == "videos"
        self.mosaic_button.configure(bg=PANEL_SOFT if mosaic_active else "#0c121b", fg=ACCENT if mosaic_active else MUTED)
        self.videos_button.configure(bg=PANEL_SOFT if videos_active else "#0c121b", fg=ACCENT if videos_active else MUTED)
        for index, button in enumerate(self.channel_buttons):
            selected = self.current_view == "camera" and self.selected_index == index
            button.configure(bg=PANEL_SOFT if selected else "#0c121b")

    def show_video_library(self) -> None:
        self._stop_ptz()
        self._stop_video_playback()
        self.current_view = "videos"
        self.selected_index = None
        self.view_title.set("VÍDEOS DE QUEDAS")
        self.image_canvas.pack_forget()
        if not self.video_library.winfo_ismapped():
            self.video_library.pack(fill=BOTH, expand=True)
        self._refresh_video_list()
        self._update_ptz_visibility()
        self._refresh_navigation()

    def _refresh_video_list(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        paths = sorted(RECORDINGS_DIR.glob("queda-ch*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
        if paths == self._video_paths and self.video_list.size() > 0:
            return
        selected_path = None
        selection = self.video_list.curselection()
        if selection and selection[0] < len(self._video_paths):
            selected_path = self._video_paths[selection[0]]
        self._video_paths = paths
        self.video_list.delete(0, END)
        for path in paths:
            try:
                stamp = path.stem.split("_", 1)[1]
                occurred = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S")
                title = occurred.strftime("%d/%m/%Y  ·  %H:%M:%S")
            except (IndexError, ValueError):
                title = path.stem
            size_mb = path.stat().st_size / 1_048_576
            channel = path.stem.split("_", 1)[0].replace("queda-ch", "CH ")
            self.video_list.insert(END, f"  {title}   ·   {channel}   ·   {size_mb:.1f} MB")
        if not paths:
            self.video_list.insert(END, "  Nenhum vídeo gravado ainda")
            self.video_play_button.configure(state="disabled")
            self.video_name_var.set("Nenhuma gravação")
            self.video_meta_var.set("O buffer já está ativo para os próximos eventos")
        else:
            self.video_play_button.configure(state="normal")
            index = paths.index(selected_path) if selected_path in paths else 0
            self.video_list.selection_set(index)
            self.video_list.activate(index)
            self._preview_selected_video()

    def _selected_video_path(self) -> Path | None:
        selection = self.video_list.curselection()
        if not selection or selection[0] >= len(self._video_paths):
            return None
        return self._video_paths[selection[0]]

    def _video_selected(self, _event=None) -> None:
        if self.current_view == "videos":
            self._preview_selected_video()

    def _preview_selected_video(self) -> None:
        path = self._selected_video_path()
        if path is None:
            return
        self._stop_video_playback()
        capture = cv2.VideoCapture(str(path))
        ok, frame = capture.read()
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        capture.release()
        self.video_name_var.set(path.stem.replace("queda-", "Queda · ").replace("_", "  "))
        duration = frame_count / fps if fps else 0
        channel = path.stem.split("_", 1)[0].replace("queda-ch", "CH ")
        self.video_meta_var.set(f"{channel}  ·  {duration:.1f} segundos  ·  {path.stat().st_size / 1_048_576:.1f} MB")
        if ok:
            self._display_video_frame(frame)

    def _toggle_video_playback(self) -> None:
        if self._video_playing:
            self._video_playing = False
            self.video_play_button.configure(text="▶  CONTINUAR")
            return
        path = self._selected_video_path()
        if path is None:
            return
        if self._video_capture is None or self._video_current_path != path:
            self._stop_video_playback()
            self._video_capture = cv2.VideoCapture(str(path))
            self._video_current_path = path
        self._video_playing = True
        self.video_play_button.configure(text="Ⅱ  PAUSAR")
        self._video_tick()

    def _play_selected_video(self) -> None:
        self._stop_video_playback()
        self._toggle_video_playback()

    def _video_tick(self) -> None:
        if not self._video_playing or self._video_capture is None or self.current_view != "videos":
            return
        ok, frame = self._video_capture.read()
        if not ok:
            self._video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._video_playing = False
            self.video_play_button.configure(text="↻  REPRODUZIR")
            return
        self._display_video_frame(frame)
        fps = self._video_capture.get(cv2.CAP_PROP_FPS) or 8.0
        self._video_after_id = self.root.after(max(20, round(1000 / fps)), self._video_tick)

    def _display_video_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        area_width = max(1, self.video_canvas.winfo_width())
        area_height = max(1, self.video_canvas.winfo_height())
        width, height = fit_size(image.width, image.height, area_width, area_height)
        image = image.resize((width, height), Image.Resampling.BILINEAR)
        self._video_photo = ImageTk.PhotoImage(image)
        self.video_canvas.delete("video-frame")
        self.video_canvas.delete("video-placeholder")
        self.video_canvas.create_image(
            area_width // 2,
            area_height // 2,
            image=self._video_photo,
            anchor="center",
            tags=("video-frame",),
        )

    def _stop_video_playback(self) -> None:
        self._video_playing = False
        if self._video_after_id is not None:
            self.root.after_cancel(self._video_after_id)
            self._video_after_id = None
        if self._video_capture is not None:
            self._video_capture.release()
            self._video_capture = None
        self._video_current_path = None
        if hasattr(self, "video_play_button"):
            self.video_play_button.configure(text="▶  REPRODUZIR")

    @staticmethod
    def _open_recordings_folder() -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(RECORDINGS_DIR)

    def _start_backend(self) -> None:
        try:
            settings = DVRSettings.load()
            state = DashboardState(settings, native=True)
            state.start()
            self._events.put(("ready", state))
        except Exception as exc:
            LOGGER.exception("Falha ao iniciar aplicativo desktop")
            self._events.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event == "ready":
                state = payload
                if not isinstance(state, DashboardState):
                    continue
                if self._closing:
                    state.stop()
                    self.root.destroy()
                    return
                self.state = state
                for index, feed in enumerate(state.feeds[:9]):
                    self.channel_buttons[index].configure(text=f"CH {feed.channel:02d}   {feed.name}")
                self.status_var.set("Conectando aos canais do DVR…")
            elif event == "error":
                self.status_var.set("Falha ao iniciar")
                messagebox.showerror("UStudio Vision", str(payload))
                self.root.destroy()
                return
            elif event == "stopped":
                self.root.destroy()
                return
            elif event == "ptz_error":
                self.status_var.set(f"Falha no PTZ: {payload}")
        if self.root.winfo_exists():
            self.root.after(100, self._poll_events)

    def _render(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        state = self.state
        if state is not None and self.current_view != "videos":
            try:
                if self.selected_index is None:
                    frame, sequence = state.mosaic.image_snapshot()
                else:
                    feed = state.feeds[self.selected_index]
                    _, sequence = feed.frame_snapshot()
                    frame = feed.render_image() if sequence != self._last_sequence else None
                if frame is not None and sequence != self._last_sequence:
                    self._show_pil_image(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
                    self._last_sequence = sequence
            except Exception:
                LOGGER.exception("Falha ao renderizar imagem")
        self.root.after(self.RENDER_INTERVAL_MS, self._render)

    def _show_jpeg(self, jpeg: bytes) -> None:
        with Image.open(BytesIO(jpeg)) as source:
            image = source.convert("RGB")
        self._show_pil_image(image)

    def _show_pil_image(self, image: Image.Image) -> None:
        area_width = max(1, self.image_canvas.winfo_width())
        area_height = max(1, self.image_canvas.winfo_height())
        width, height = fit_size(image.width, image.height, area_width, area_height)
        image = image.resize((width, height), Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(image)
        left = (area_width - width) // 2
        top = (area_height - height) // 2
        self.image_canvas.delete("camera-image")
        self.image_canvas.create_image(
            area_width // 2,
            area_height // 2,
            image=self._photo,
            anchor="center",
            tags=("camera-image",),
        )
        self._image_bounds = (left, top, width, height)

    def _refresh_status(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        state = self.state
        if state is not None:
            statuses = [feed.status() for feed in state.feeds]
            online = sum(bool(item["online"]) for item in statuses)
            people = sum(int(item["personCount"]) for item in statuses)
            ai = state.detection_worker.status()
            self.camera_metric.set(f"{online}/{len(statuses)}")
            self.people_metric.set(str(people))
            self.ai_metric.set(str(round(float(ai.get("inferenceMs", 0)))) if ai.get("ready") else "…")

            candidates = [item for item in statuses if item.get("fall", {}).get("enabled")]
            priority = {"normal": 0, "possible_fall": 1, "down": 2, "alert_pending": 3}
            monitored = max(candidates, key=lambda item: (bool(item["online"]), priority.get(item["fall"].get("state"), 0)), default=None)
            if monitored:
                fall = monitored["fall"]
                label = FALL_LABELS.get(str(fall.get("state")), "CALIBRANDO")
                features = fall.get("features") or {}
                score = round(float(features.get("score", 0)) * 100)
                self.fall_metric.set(f"CH {monitored['id']:02d} · {label} · {score}%" if fall.get("state") != "normal" else f"{len(candidates)} CÂMERAS · CALIBRANDO")

            if ai.get("error"):
                self.status_var.set(f"IA indisponível: {ai['error']}")
            elif not ai.get("ready"):
                self.status_var.set("Aquecendo YOLO na GPU…")
            elif online != len(statuses):
                self.status_var.set(f"{online} de {len(statuses)} câmeras online")
            elif people:
                self.status_var.set(f"{people} {'pessoa detectada' if people == 1 else 'pessoas detectadas'}")
            else:
                self.status_var.set("Todas as câmeras online · ambiente sem pessoas")

            for index, (button, status) in enumerate(zip(self.channel_buttons, statuses, strict=False)):
                fall_state = status.get("fall", {}).get("state")
                if fall_state in {"down", "alert_pending"}:
                    color = DANGER
                elif fall_state == "possible_fall":
                    color = AMBER
                elif status["personCount"]:
                    color = ACCENT
                elif not status["online"]:
                    color = DANGER
                else:
                    color = TEXT if self.selected_index == index else MUTED
                button.configure(fg=color)

            self._update_ptz_visibility()
            if self.current_view == "videos":
                self._refresh_video_list()
        self.root.after(self.STATUS_INTERVAL_MS, self._refresh_status)

    def _image_clicked(self, event) -> None:
        if self.state is None or self.selected_index is not None:
            return
        index = mosaic_tile_at(event.x, event.y, self._image_bounds, len(self.state.feeds))
        if index is not None:
            self.select_camera(index)

    def select_camera(self, index: int) -> None:
        if self.state is None or not (0 <= index < len(self.state.feeds)):
            return
        self._stop_ptz()
        self._stop_video_playback()
        self.current_view = "camera"
        self.selected_index = index
        self._last_sequence = -1
        self.video_library.pack_forget()
        if not self.image_canvas.winfo_ismapped():
            self.image_canvas.pack(fill=BOTH, expand=True)
        feed = self.state.feeds[index]
        self.view_title.set(f"CH {feed.channel:02d} · {feed.name}")
        self._update_ptz_visibility()
        self._refresh_navigation()

    def show_mosaic(self) -> None:
        self._stop_ptz()
        self._stop_video_playback()
        self.current_view = "mosaic"
        self.selected_index = None
        self._last_sequence = -1
        self.video_library.pack_forget()
        if not self.image_canvas.winfo_ismapped():
            self.image_canvas.pack(fill=BOTH, expand=True)
        self.view_title.set("TODAS AS CÂMERAS")
        self._update_ptz_visibility()
        self._refresh_navigation()

    def _update_ptz_visibility(self) -> None:
        supports_ptz = (
            self.state is not None
            and self.selected_index is not None
            and self.state.feeds[self.selected_index].supports_ptz
        )
        if supports_ptz:
            if not self.ptz_frame.winfo_ismapped():
                self.ptz_frame.place(relx=0.985, rely=0.98, anchor="se")
        elif self.ptz_frame.winfo_ismapped():
            self.ptz_frame.place_forget()

    def _start_ptz(self, action: str) -> None:
        self._ptz_action = action
        self._send_ptz(action)

    def _stop_ptz(self) -> None:
        if self._ptz_action is None:
            return
        self._ptz_action = None
        self._send_ptz("stop")

    def _send_ptz(self, action: str) -> None:
        state = self.state
        if state is None or state.ptz_controller is None:
            return

        def send() -> None:
            try:
                state.ptz_controller.control(action)
            except Exception as exc:
                LOGGER.warning("Falha no PTZ: %s", exc)
                self._events.put(("ptz_error", str(exc)))

        threading.Thread(target=send, name=f"desktop-ptz-{action}", daemon=True).start()

    def _on_escape(self, _event=None) -> None:
        if self.current_view != "mosaic":
            self.show_mosaic()
        else:
            self.close()

    def _toggle_fullscreen(self, _event=None) -> None:
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_ptz()
        self._stop_video_playback()
        self.status_var.set("Encerrando câmeras com segurança…")
        state = self.state
        if state is None:
            return

        def stop() -> None:
            try:
                state.stop()
            finally:
                self._events.put(("stopped", None))

        threading.Thread(target=stop, name="desktop-backend-stop", daemon=True).start()


def self_test() -> int:
    settings = DVRSettings.load()
    channels = settings.fall_channels()
    profiles = {
        channel: {"rotation": settings.fall_rotation(channel), "perspectiveGuard": settings.fall_geometry(channel) is not None}
        for channel in channels
    }
    model_path = Path(settings.yolo_model)
    if not model_path.is_absolute():
        model_path = PROJECT_DIR / model_path
    result = {
        "ok": model_path.is_file(),
        "model": model_path.name,
        "channels": settings.channel_count + int(bool(settings.extra_camera_host)),
        "fallChannel": settings.fall_monitor_channel,
        "fallChannels": channels,
        "fallProfiles": profiles,
        "fallShadowMode": settings.fall_shadow_mode,
        "fallRotation": settings.fall_camera_rotation,
        "fallGroundConfirmSeconds": settings.fall_ground_confirm_seconds,
        "fallPerspectiveGuard": profiles.get(settings.fall_monitor_channel, {}).get("perspectiveGuard", False),
        "animalFilter": settings.animal_filter_enabled,
        "fallVideo": settings.fall_video_enabled,
        "fallVideoPreSeconds": settings.fall_video_pre_seconds,
        "fallVideoPostSeconds": settings.fall_video_post_seconds,
        "webServer": False,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UStudio Vision desktop")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--classic-ui", action="store_true", help="Interface Qt anterior para diagnóstico local")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    from PySide6.QtWidgets import QApplication, QMessageBox
    from camera_ai.desktop_ui import ProfessionalWindow, configure_application, dark_titlebar
    if not args.classic_ui:
        from camera_ai.hybrid_ui import VisionWindow, register_scheme
        register_scheme()

    app = QApplication(sys.argv[:1])
    configure_application(app)
    configure_logging()
    def log_ui_exception(kind, value, traceback_object):
        logging.getLogger("camera_ai.desktop").error("Erro na interface", exc_info=(kind, value, traceback_object))
    sys.excepthook = log_ui_exception
    instance = SingleInstance(LOGS_DIR / "desktop-app.lock")
    if not instance.acquire():
        QMessageBox.information(None, "UStudio Vision", "O UStudio Vision já está aberto.")
        return 0

    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.SetPriorityClass.restype = ctypes.c_int
        if kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00008000):
            LOGGER.info("Prioridade do monitoramento: acima do normal")
        else:
            LOGGER.warning("Não foi possível priorizar o monitoramento (Windows %s)", ctypes.get_last_error())

    try:
        window = ProfessionalWindow() if args.classic_ui else VisionWindow()
        window.showMaximized()
        dark_titlebar(window)
        return app.exec()
    except Exception:
        LOGGER.exception("Erro fatal no aplicativo desktop")
        QMessageBox.critical(None, "UStudio Vision", "O aplicativo encontrou um erro. Consulte logs/desktop-app.log.")
        return 1
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
