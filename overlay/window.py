"""
overlay/window.py - Ventana tkinter overlay para Jarvis.

Caracteristicas:
  - Borderless, always-on-top, alpha 0.96
  - Invisible a Zoom/Teams/OBS via WDA_EXCLUDEFROMCAPTURE cuando se activa
  - Drag-to-move por header
  - Indicador de estado, modo, conexion y privacidad
  - Transcript en vivo con modo compacto/expandido
  - Tira de eventos recientes para acciones importantes
  - Footer de telemetria embebido

Thread-safety: el overlay corre en main thread. La sesion publica eventos
desde su thread asyncio; el orquestador marshalla via root.after(0, callback)
para tocar widgets desde fuera.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from typing import Any, Callable

from overlay.approval_dialog import ApprovalDialog
from overlay.command_center import CommandCenter
from overlay.jarvis_core import JarvisCoreCanvas
from overlay.telemetry_footer import TelemetryFooter
from overlay.ui_theme import (
    ACCENT,
    BG,
    BORDER,
    BORDER_SOFT,
    CONTROL,
    CONTROL_ACTIVE,
    CONTROL_HOVER,
    DANGER,
    DANGER_BG,
    FONT_DISPLAY,
    FONT_UI,
    OK,
    PANEL,
    PANEL_SOFT,
    STATE_COLORS,
    STATE_DETAILS,
    STATE_LABELS,
    SURFACE,
    SURFACE_ALT,
    TEXT_DIM,
    TEXT_FAINT,
    TEXT_PRIMARY,
    WARN,
    WARN_BG,
    WINDOW_RADIUS,
)
from overlay.ui_widgets import apply_window_rounding, attach_tooltip
from jarvis_version import JARVIS_VERSION_LABEL
from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker

WDA_EXCLUDEFROMCAPTURE = 0x00000011

EXPANDED_SIZE = "780x700"
COMPACT_SIZE = "780x310"


class JarvisOverlay:
    """Overlay principal. Una clase thin que orquesta widgets + estado."""

    def __init__(
        self,
        tracker: TokenTracker,
        gate: BudgetGate,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.tracker = tracker
        self.gate = gate
        self._on_close = on_close or (lambda: None)
        self._events: list[str] = []
        self._event_history: list[tuple[str, str, str]] = []
        self._memory_events: list[dict[str, Any]] = []
        self._approval_dialogs: list[ApprovalDialog] = []
        self._recursion_handling = False
        self._expanded = True
        self._closed = False
        self._memory_active: dict[str, dict[str, Any]] = {}
        self._log_path = Path(__file__).resolve().parent.parent / "data" / "jarvis.log"
        self.command_center = CommandCenter(self)

        self.root = tk.Tk()
        self.root.title(f"JARVIS {JARVIS_VERSION_LABEL}")
        self.root.report_callback_exception = self._report_callback_exception
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.985)
        self.root.configure(bg=BG)
        self.root.geometry(f"{EXPANDED_SIZE}+80+80")

        from overlay.camera_preview import CameraPreviewWindow
        self._camera_preview = CameraPreviewWindow(self.root)

        self._logo_image: tk.PhotoImage | None = None
        self._load_brand_assets()

        self._build_ui()
        apply_window_rounding(self.root, WINDOW_RADIUS)
        self._enable_capture_invisibility()
        self._bind_drag()

        self._state = "idle"
        self._mode = "PTT"
        self._update_state_visual()
        self.log_event("Overlay listo")

    # ---- Timer / scheduling ----

    def after(self, delay_ms: int | float, fn: "Callable[[], None]") -> None:
        """Delega root.after() — interfaz comun con WebJarvisOverlay.after()."""
        self.root.after(int(delay_ms), fn)

    # ---- UI construction ----

    def _load_brand_assets(self) -> None:
        """Carga icon.ico (taskbar) + logo_64.png (header) si existen."""
        assets = Path(__file__).resolve().parent.parent / "assets"
        icon_path = assets / "icon.ico"
        logo_path = assets / "logo_64.png"

        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except Exception as exc:
                print(f"[overlay] iconbitmap fallo: {exc}")

        if logo_path.exists():
            try:
                self._logo_image = tk.PhotoImage(file=str(logo_path))
                try:
                    self.root.iconphoto(False, self._logo_image)
                except Exception:
                    pass
            except Exception as exc:
                print(f"[overlay] PhotoImage fallo: {exc}")

    def _build_ui(self) -> None:
        self.header = tk.Frame(self.root, bg=PANEL, height=58)
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)

        if self._logo_image is not None:
            logo_label = tk.Label(self.header, image=self._logo_image, bg=PANEL, padx=8)
            logo_label.pack(side="left", padx=(8, 0))
            self._logo_label = logo_label

        brand = tk.Frame(self.header, bg=PANEL)
        brand.pack(side="left", fill="y", padx=(10, 0))

        tk.Label(
            brand,
            text=f"JARVIS {JARVIS_VERSION_LABEL}",
            bg=PANEL,
            fg=TEXT_PRIMARY,
            font=(FONT_DISPLAY, 13, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(7, 0))
        tk.Label(
            brand,
            text="local neural interface",
            bg=PANEL,
            fg=TEXT_FAINT,
            font=(FONT_UI, 7),
            anchor="w",
        ).pack(anchor="w")
        self._brand_frame = brand

        self.close_btn = self._make_header_button("X", self.close, DANGER, "Cerrar Jarvis")
        self.close_btn.pack(side="right", padx=(2, 8))

        self.compact_btn = self._make_header_button("-", self.toggle_compact, tooltip="Compactar overlay")
        self.compact_btn.pack(side="right", padx=(0, 2))

        self.dashboard_btn = self._make_header_button(
            "CENTER",
            self.open_dashboard,
            tooltip="Abrir Command Center",
            width=7,
        )
        self.dashboard_btn.pack(side="right", padx=(0, 2))

        self.header_rule = tk.Frame(self.root, bg=BORDER_SOFT, height=1)
        self.header_rule.pack(fill="x", side="top")

        self.status_bar = tk.Frame(self.root, bg=SURFACE, height=54)
        self.status_bar.pack(fill="x", side="top")
        self.status_bar.pack_propagate(False)

        status_left = tk.Frame(self.status_bar, bg=SURFACE)
        status_left.pack(side="left", fill="both", expand=True, padx=(10, 8))

        self.state_dot = tk.Canvas(
            status_left,
            width=18,
            height=18,
            bg=SURFACE,
            highlightthickness=0,
        )
        self.state_dot.pack(side="left", pady=(17, 0))
        self.state_ring_id = self.state_dot.create_oval(2, 2, 16, 16, fill="", outline=BORDER, width=1)
        self.state_dot_id = self.state_dot.create_oval(6, 6, 12, 12, fill=TEXT_FAINT, outline="")

        state_copy = tk.Frame(status_left, bg=SURFACE)
        state_copy.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.state_label = tk.Label(
            state_copy,
            text="Listo",
            bg=SURFACE,
            fg=TEXT_PRIMARY,
            font=(FONT_DISPLAY, 12, "bold"),
            anchor="w",
        )
        self.state_label.pack(anchor="w", pady=(7, 0))
        self.state_detail_label = tk.Label(
            state_copy,
            text="Ctrl para hablar",
            bg=SURFACE,
            fg=TEXT_FAINT,
            font=(FONT_UI, 8),
            anchor="w",
        )
        self.state_detail_label.pack(anchor="w")

        chips = tk.Frame(self.status_bar, bg=SURFACE)
        chips.pack(side="right", fill="y", padx=(0, 10))

        self.mode_label = self._make_chip(chips, "Modo PTT")
        self.mode_label.pack(side="left", padx=(0, 6), pady=10)

        self.connection_label = self._make_chip(chips, "Gemini iniciando")
        self.connection_label.pack(side="left", padx=(0, 6), pady=10)

        self.privacy_label = self._make_chip(
            chips,
            self._privacy_label_text(),
            fg=self._privacy_label_color(),
        )
        self.privacy_label.pack(side="left", pady=10)

        self.event_bar = tk.Frame(self.root, bg=PANEL_SOFT, height=32)
        self.event_bar.pack(fill="x", side="top")
        self.event_bar.pack_propagate(False)
        tk.Label(
            self.event_bar,
            text="LIVE FEED",
            bg=PANEL_SOFT,
            fg=TEXT_FAINT,
            font=(FONT_DISPLAY, 7, "bold"),
            anchor="w",
            padx=10,
        ).pack(side="left")
        self.event_label = tk.Label(
            self.event_bar,
            text="",
            bg=PANEL_SOFT,
            fg=TEXT_DIM,
            font=(FONT_UI, 8),
            anchor="w",
            padx=2,
        )
        self.event_label.pack(side="left", fill="both", expand=True)

        self.core_visual = JarvisCoreCanvas(self.root, height=224)
        self.core_visual.pack(fill="x", side="top", padx=0, pady=(0, 0))

        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=14, pady=(12, 4))

        self.input_text = self._build_transcript_panel(
            self.body,
            title="TU",
            title_color=TEXT_FAINT,
            text_color=TEXT_DIM,
            text_height=2,
            expand=False,
        )
        self.output_text = self._build_transcript_panel(
            self.body,
            title="JARVIS",
            title_color=ACCENT,
            text_color=TEXT_PRIMARY,
            text_height=7,
            expand=True,
        )

        self.hint_label = tk.Label(
            self.root,
            text="Ctrl hablar   Shift+M modo libre   Shift+S pantalla   Alt+S region   Alt+Q salir",
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_UI, 8),
        )
        self.hint_label.pack(fill="x", padx=14, pady=(3, 5))

        self.footer = TelemetryFooter(
            self.root,
            self.tracker,
            self.gate,
            on_blocked=self._on_blocked,
        )
        self.footer.pack(side="bottom", fill="x")

    def _make_chip(self, parent: tk.Misc, text: str, fg: str = TEXT_DIM) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=CONTROL,
            fg=fg,
            font=(FONT_DISPLAY, 8, "bold"),
            padx=12,
            pady=5,
        )

    def _make_header_button(
        self,
        text: str,
        command: Callable[[], None],
        fg: str = TEXT_DIM,
        tooltip: str | None = None,
        width: int = 3,
    ) -> tk.Label:
        btn = tk.Label(
            self.header,
            text=text,
            bg=PANEL,
            fg=fg,
            font=(FONT_DISPLAY, 9, "bold"),
            width=width,
            padx=10,
            cursor="hand2",
        )
        btn.bind("<Button-1>", lambda _: command())
        self._wire_hover(btn, PANEL, CONTROL_HOVER)
        if tooltip:
            attach_tooltip(btn, tooltip)
        return btn

    @staticmethod
    def _wire_hover(widget: tk.Widget, normal_bg: str, hover_bg: str) -> None:
        widget.bind("<Enter>", lambda _: widget.config(bg=hover_bg))
        widget.bind("<Leave>", lambda _: widget.config(bg=normal_bg))

    def _build_transcript_panel(
        self,
        parent: tk.Misc,
        title: str,
        title_color: str,
        text_color: str,
        text_height: int = 4,
        expand: bool = True,
    ) -> tk.Text:
        """Construye un panel con header + Text widget."""
        panel = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER_SOFT)
        panel.pack(fill="both" if expand else "x", expand=expand, pady=(2, 9))

        header = tk.Frame(panel, bg=SURFACE)
        header.pack(fill="x", padx=10, pady=(8, 3))

        tk.Label(
            header,
            text=title,
            bg=SURFACE,
            fg=title_color,
            font=(FONT_DISPLAY, 8, "bold"),
            anchor="w",
        ).pack(side="left")

        copy_btn = tk.Label(
            header,
            text="Copiar",
            bg=CONTROL,
            fg=TEXT_DIM,
            font=(FONT_DISPLAY, 8, "bold"),
            cursor="hand2",
            padx=9,
            pady=3,
        )
        copy_btn.pack(side="right")

        clear_btn = tk.Label(
            header,
            text="Limpiar",
            bg=CONTROL,
            fg=TEXT_DIM,
            font=(FONT_DISPLAY, 8, "bold"),
            cursor="hand2",
            padx=9,
            pady=3,
        )
        clear_btn.pack(side="right")

        self._wire_hover(copy_btn, CONTROL, CONTROL_HOVER)
        self._wire_hover(clear_btn, CONTROL, CONTROL_HOVER)

        body = tk.Frame(panel, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=10, pady=(3, 10))

        scrollbar = tk.Scrollbar(body, bg=BG, troughcolor=PANEL)
        scrollbar.pack(side="right", fill="y")

        text = tk.Text(
            body,
            height=text_height,
            bg=SURFACE_ALT,
            fg=text_color,
            insertbackground=text_color,
            font=(FONT_UI, 10),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            highlightcolor=BORDER,
            wrap="word",
            yscrollcommand=scrollbar.set,
        )
        text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text.yview)

        self._make_readonly_but_copyable(text)

        copy_btn.bind("<Button-1>", lambda _: self._copy_to_clipboard(text, copy_btn))
        clear_btn.bind("<Button-1>", lambda _: self._clear_text(text))

        return text

    @staticmethod
    def _make_readonly_but_copyable(widget: tk.Text) -> None:
        """Bloquea escritura de usuario, permite seleccion/copy/navegacion."""
        nav_keys = (
            "Left", "Right", "Up", "Down",
            "Prior", "Next", "Home", "End",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
        )

        def on_key(event):
            ctrl = bool(event.state & 0x4)
            if event.keysym in nav_keys:
                return None
            if ctrl and event.keysym.lower() in ("c", "a", "x", "insert"):
                return None
            return "break"

        widget.bind("<Key>", on_key)

        def select_all(_):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
            return "break"

        widget.bind("<Control-a>", select_all)
        widget.bind("<Control-A>", select_all)

    def _copy_to_clipboard(self, widget: tk.Text, feedback_btn: tk.Label | None = None) -> None:
        """Copia la seleccion o todo el contenido del widget."""
        try:
            content = widget.selection_get()
        except tk.TclError:
            content = widget.get("1.0", "end-1c").strip()

        if not content:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update()
        self.log_event("Transcript copiado")

        if feedback_btn is not None:
            original_text = feedback_btn.cget("text")
            original_fg = feedback_btn.cget("fg")
            feedback_btn.config(text="Listo", fg=ACCENT)
            self.root.after(
                1200,
                lambda: feedback_btn.config(text=original_text, fg=original_fg),
            )

    def _clear_text(self, widget: tk.Text) -> None:
        widget.delete("1.0", "end")
        self.log_event("Transcript limpiado")

    def _enable_capture_invisibility(self) -> None:
        """Excluye la ventana de screen capture si JARVIS_HIDE_FROM_CAPTURE=true."""
        if sys.platform != "win32":
            return
        flag = os.environ.get("JARVIS_HIDE_FROM_CAPTURE", "false").strip().lower()
        if flag not in ("true", "1", "yes"):
            print("[overlay] capture invisibility OFF")
            return
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            print("[overlay] capture invisibility ON")
            self.log_event("Overlay oculto en capturas")
        except Exception as exc:
            print(f"[overlay] no pude excluir de capture: {exc}")
            self.privacy_label.config(text="Captura error", fg=DANGER, bg=DANGER_BG)

    def _bind_drag(self) -> None:
        """Permite arrastrar la ventana presionando el header.

        Con overrideredirect(True), geometry() llama a SetWindowPos que genera
        mensajes WM_WINDOWPOSCHANGED procesados sincronicamente por Tk. Usar
        after(0) no evita la re-entrada porque el stack de Tcl no se ha
        desenrollado. after(16) garantiza que el stack actual termina antes
        de ejecutar geometry(), cortando la cadena de recursion.
        Las coordenadas se guardan en instancia (no closure) para aplicar
        siempre la posicion mas reciente aunque se descarten eventos intermedios.
        """
        self._drag_x = 0
        self._drag_y = 0
        self._drag_target_x = 0
        self._drag_target_y = 0
        self._drag_pending = False

        def start_drag(e):
            self._drag_x = e.x
            self._drag_y = e.y

        def do_drag(e):
            self._drag_target_x = self.root.winfo_pointerx() - self._drag_x
            self._drag_target_y = self.root.winfo_pointery() - self._drag_y
            if self._drag_pending:
                return
            self._drag_pending = True
            self.root.after(16, self._apply_drag)

        draggables = [
            self.header,
            self._brand_frame,
            self.status_bar,
            self.event_bar,
            self.core_visual,
            self.state_label,
            self.state_detail_label,
            self.mode_label,
            self.connection_label,
            self.privacy_label,
        ]
        if hasattr(self, "_logo_label"):
            draggables.append(self._logo_label)
        for w in draggables:
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", do_drag)

    def _apply_drag(self) -> None:
        """Aplica la ultima posicion de drag y libera el flag."""
        self._drag_pending = False
        self.root.geometry(f"+{self._drag_target_x}+{self._drag_target_y}")

    @property
    def state(self) -> str:
        return self._state

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def event_history(self) -> list[tuple[str, str, str]]:
        return self._event_history

    @property
    def memory_events(self) -> list[dict[str, Any]]:
        return self._memory_events

    @property
    def log_path(self) -> Path:
        return self._log_path

    def privacy_label_text(self) -> str:
        return self._privacy_label_text()

    # ---- State updates (call from main thread) ----

    def set_state(self, state: str) -> None:
        if state not in STATE_COLORS:
            return
        self._state = state
        self._update_state_visual()
        self.core_visual.set_state(state)

    def set_mode(self, mode: str) -> None:
        """mode: 'PTT' | 'LIBRE'"""
        self._mode = mode
        color = ACCENT if mode == "LIBRE" else TEXT_DIM
        text = "Modo libre" if mode == "LIBRE" else "Modo PTT"
        self.mode_label.config(text=text, fg=color, bg=CONTROL_ACTIVE if mode == "LIBRE" else CONTROL)
        self.log_event(f"Modo {text}")

    def set_connection_status(self, status: str, detail: str = "") -> None:
        colors = {
            "connecting": WARN,
            "connected": ACCENT,
            "reconnecting": "#f97316",
            "error": DANGER,
            "stopped": TEXT_FAINT,
        }
        labels = {
            "connecting": "Gemini conectando",
            "connected": "Gemini listo",
            "reconnecting": "Gemini reconectando",
            "error": "Gemini error",
            "stopped": "Gemini detenido",
        }
        label = labels.get(status, f"Gemini: {status}")
        if detail:
            label = f"{label} - {detail[:36]}"
        bg = WARN_BG if status == "reconnecting" else DANGER_BG if status == "error" else CONTROL
        self.connection_label.config(text=label, fg=colors.get(status, TEXT_FAINT), bg=bg)
        if status in {"connected", "reconnecting", "error", "stopped"}:
            self.log_event(label)

    def log_event(self, message: str, level: str = "info") -> None:
        """Muestra una tira compacta con los ultimos eventos importantes."""
        message = " ".join((message or "").split())
        if not message:
            return
        stamp = time.strftime("%H:%M")
        self._event_history.append((stamp, level, message))
        self._event_history = self._event_history[-200:]
        display_message = message
        if len(display_message) > 56:
            display_message = display_message[:53].rstrip() + "..."
        self._events.append(f"{stamp} {display_message}")
        self._events = self._events[-3:]
        color = {
            "info": TEXT_DIM,
            "ok": ACCENT,
            "warn": WARN,
            "error": DANGER,
        }.get(level, TEXT_DIM)
        self.event_label.config(text="  |  ".join(self._events), fg=color)
        self._refresh_dashboard_once()

    def record_memory_tool_start(self, name: str, args: dict[str, Any] | None = None) -> None:
        """Registra una tool relacionada con memoria sin guardar contenido sensible."""
        if not self._is_memory_tool(name):
            return
        args = args or {}
        stamp = time.strftime("%H:%M:%S")
        summary = self._memory_args_summary(name, args)
        self._memory_active[name] = {
            "stamp": stamp,
            "name": name,
            "summary": summary,
        }
        self._memory_events.append({
            "stamp": stamp,
            "name": name,
            "summary": summary,
            "status": "running",
            "detail": "En progreso",
            "elapsed_ms": None,
        })
        self._memory_events = self._memory_events[-120:]
        self.log_event(f"Memoria: {summary}", "info")

    def record_memory_tool_end(
        self,
        name: str,
        elapsed_ms: float,
        ok: bool,
        response: Any = None,
    ) -> None:
        """Actualiza el resultado resumido de una tool de memoria."""
        if not self._is_memory_tool(name):
            return
        active = self._memory_active.pop(name, {})
        summary = active.get("summary") or self._memory_args_summary(name, {})
        detail = self._memory_response_summary(name, response)
        status = "ok" if ok and not self._response_failed(response) else "error"
        event = {
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": summary,
            "status": status,
            "detail": detail,
            "elapsed_ms": elapsed_ms,
        }
        for idx in range(len(self._memory_events) - 1, -1, -1):
            existing = self._memory_events[idx]
            if existing.get("name") == name and existing.get("status") == "running":
                self._memory_events[idx] = event
                break
        else:
            self._memory_events.append(event)
        self._memory_events = self._memory_events[-120:]
        self.log_event(f"Memoria: {detail}", "ok" if status == "ok" else "error")

    def record_tool_start(self, name: str, args: dict | None = None) -> None:
        """Compatibilidad con WebJarvisOverlay: en Tk solo actualiza memoria."""
        self.record_memory_tool_start(name, args or {})

    def record_tool_end(self, name: str, elapsed_ms: float, ok: bool, response=None) -> None:
        """Compatibilidad con WebJarvisOverlay: en Tk solo actualiza memoria."""
        self.record_memory_tool_end(name, elapsed_ms, ok, response)

    def record_audio_telemetry(self, payload: dict) -> None:
        return None

    def record_turn_latency(self, line: str) -> None:
        return None

    @staticmethod
    def _is_memory_tool(name: str) -> bool:
        return name in {
            "jarvis_recall",
            "jarvis_session_recall",
            "jarvis_remember",
            "jarvis_browse",
            "jarvis_link",
            "obsidian_mcp",
            "study_mode",
        }

    def _memory_args_summary(self, name: str, args: dict[str, Any]) -> str:
        if name == "jarvis_recall":
            query = self._clip(str(args.get("query", "")), 72)
            top_k = args.get("top_k", 3)
            return f"recall '{query}' top {top_k}"
        if name == "jarvis_session_recall":
            query = self._clip(str(args.get("query", "")), 52)
            when = self._clip(str(args.get("when", "")), 24)
            return f"session recall '{query}' {when}".strip()
        if name == "jarvis_remember":
            title = self._clip(str(args.get("title", "(sin titulo)")), 72)
            content_len = len(str(args.get("content", "")))
            tags = args.get("tags") or []
            tags_text = ", ".join(map(str, tags[:4])) if isinstance(tags, list) else str(tags)
            suffix = f" tags={tags_text}" if tags_text else ""
            return f"remember '{title}' ({content_len} chars){suffix}"
        if name == "jarvis_browse":
            folder = self._clip(str(args.get("folder") or "<vault>"), 72)
            limit = args.get("limit", 20)
            return f"browse {folder} limit {limit}"
        if name == "jarvis_link":
            src = self._clip(str(args.get("note_from", "")), 44)
            dst = self._clip(str(args.get("note_to", "")), 44)
            return f"link {src} -> {dst}"
        if name == "obsidian_mcp":
            op = str(args.get("operation", "operation"))
            target = (
                args.get("path")
                or args.get("note_from")
                or args.get("destination")
                or ""
            )
            return f"obsidian {op} {self._clip(str(target), 72)}".strip()
        if name == "study_mode":
            action = str(args.get("action", "status"))
            title = str(args.get("title") or args.get("note_path") or "")
            return f"study {action} {self._clip(title, 72)}".strip()
        return name

    def _memory_response_summary(self, name: str, response: Any) -> str:
        if not isinstance(response, dict):
            return f"{name} completo"
        if name == "jarvis_recall":
            found = response.get("found", 0)
            titles = []
            for item in response.get("results", [])[:3]:
                if isinstance(item, dict):
                    titles.append(item.get("title") or item.get("path") or "nota")
            title_text = f": {', '.join(map(str, titles))}" if titles else ""
            return f"recall encontro {found}{title_text}"
        if name == "jarvis_session_recall":
            found = response.get("found", 0)
            dates = [
                str(item.get("date") or item.get("title") or "sesion")
                for item in response.get("sessions", [])[:3]
                if isinstance(item, dict)
            ]
            suffix = f": {', '.join(dates)}" if dates else ""
            return f"session recall encontro {found}{suffix}"
        if name == "jarvis_remember":
            if not response.get("saved"):
                return f"remember no guardo: {self._clip(str(response.get('reason') or response.get('error') or 'sin detalle'), 80)}"
            op = response.get("operation", "saved")
            path = self._clip(str(response.get("path", response.get("title", ""))), 80)
            chunks = response.get("chunks_indexed")
            suffix = f", {chunks} chunks" if chunks is not None else ""
            return f"memory {op}: {path}{suffix}"
        if name == "jarvis_browse":
            return f"browse listo: {response.get('count', 0)} notas en {response.get('folder', '<vault>')}"
        if name == "jarvis_link":
            if response.get("linked"):
                return f"link creado: {response.get('from')} -> {response.get('to')}"
            return f"link fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
        if name == "obsidian_mcp":
            if response.get("ok") is False or response.get("error"):
                return f"obsidian fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
            op_detail = response.get("operation") or response.get("path") or response.get("message") or "operacion completa"
            return f"obsidian listo: {self._clip(str(op_detail), 80)}"
        if name == "study_mode":
            if response.get("ok") is False or response.get("error"):
                return f"study fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
            action = response.get("action") or response.get("status") or response.get("state") or "actualizado"
            note = response.get("note_path") or response.get("path") or response.get("title") or ""
            return f"study {action} {self._clip(str(note), 72)}".strip()
        if response.get("error"):
            return f"{name} fallo: {self._clip(str(response.get('error')), 80)}"
        return f"{name} completo"

    @staticmethod
    def _response_failed(response: Any) -> bool:
        return isinstance(response, dict) and (
            response.get("ok") is False
            or response.get("saved") is False and response.get("blocked") is True
            or bool(response.get("error"))
        )

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        text = " ".join((text or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."

    def toggle_compact(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.core_visual.set_compact(False)
            self.footer.pack_forget()
            self.body.pack(fill="both", expand=True, padx=14, pady=(12, 4))
            self.hint_label.pack(fill="x", padx=14, pady=(3, 5))
            self.footer.pack(side="bottom", fill="x")
            self.compact_btn.config(text="-")
            self._resize_keep_position(EXPANDED_SIZE)
            self.log_event("Overlay expandido")
        else:
            self.body.pack_forget()
            self.hint_label.pack_forget()
            self.core_visual.set_compact(True)
            self.compact_btn.config(text="+")
            self._resize_keep_position(COMPACT_SIZE)
            self.log_event("Overlay compacto")

    def open_dashboard(self) -> None:
        """Abre Command Center: resumen, eventos y tail de logs."""
        self.command_center.open()

    def _select_dashboard_tab(self, tab: str) -> None:
        """Compatibility wrapper for tests and older callers."""
        self.command_center.select_tab(tab)

    def _refresh_dashboard_once(self) -> None:
        """Compatibility wrapper for tests and older callers."""
        self.command_center.refresh_once()

    def _resize_keep_position(self, size: str) -> None:
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            self.root.geometry(f"{size}+{x}+{y}")
        except Exception:
            self.root.geometry(size)

    def _update_state_visual(self) -> None:
        color = STATE_COLORS.get(self._state, TEXT_DIM)
        label = STATE_LABELS.get(self._state, self._state)
        detail = STATE_DETAILS.get(self._state, "")
        self.state_dot.itemconfig(self.state_ring_id, outline=color)
        self.state_dot.itemconfig(self.state_dot_id, fill=color)
        self.state_label.config(text=label, fg=color)
        self.state_detail_label.config(text=detail)

    def append_input(self, text: str) -> None:
        if not text:
            return
        self.input_text.insert("end", text + " ")
        self.input_text.see("end")

    def append_output(self, text: str) -> None:
        if not text:
            return
        self.output_text.insert("end", text)
        self.output_text.see("end")

    def feed_voice_audio(self, pcm_bytes: bytes) -> None:
        if self._closed:
            return
        self.core_visual.feed_audio(pcm_bytes)

    def _report_callback_exception(self, exc_type, exc_value, exc_tb) -> None:
        """Evita que una excepcion tardia de Tkinter tumbe el proceso durante cierre."""
        if self._closed:
            try:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.__stderr__)
            except Exception:
                pass
            try:
                self.root.quit()
            except Exception:
                pass
            return
        if issubclass(exc_type, RecursionError):
            try:
                sys.setrecursionlimit(max(sys.getrecursionlimit() + 5000, 12000))
            except BaseException:
                pass
            self._handle_fatal_recursion(exc_type, exc_value, exc_tb)
            return
        # Excepcion de UI no-recursiva: logueamos a stderr Y al crash log para no
        # perder forense (antes solo iba a stderr y se perdia al cerrar la consola).
        try:
            traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.__stderr__)
        except Exception:
            pass
        self._append_crash_log("Tkinter callback exception", exc_type, exc_value, exc_tb)

    def _handle_fatal_recursion(self, exc_type, exc_value, exc_tb) -> None:
        """Maneja un RecursionError de Tkinter SIN matar el proceso.

        Lecciones de los crashes reales:
          - report_callback_exception corre DENTRO de la pila recursiva profunda
            (Tk lo llama en el punto de fallo, sin desenrollar). Cualquier trabajo
            pesado aqui re-dispara RecursionError. Por eso TODO va aislado en
            try/except BaseException y el margen extra es modesto (uno enorme
            arriesga desbordar el stack C de Tcl -> access violation).
          - Solo manejamos la PRIMERA recursion: durante el desplome, este handler
            se dispara muchas veces; un guard de reentrada evita amplificarlo.
        """
        try:
            if self.__dict__.get("_recursion_handling", False):
                return
            self.__dict__["_recursion_handling"] = True
            self.__dict__["_closed"] = True
        except BaseException:
            return
        # Aire modesto para que el diagnostico minimo no re-dispare de inmediato.
        try:
            sys.setrecursionlimit(max(sys.getrecursionlimit() + 3000, 12000))
        except BaseException:
            pass
        # Diagnostico TOTALMENTE aislado: si re-lanza (RecursionError u otra), lo
        # tragamos para que NUNCA tumbe el proceso.
        try:
            self._dump_recursion_diagnostic(exc_type, exc_value)
        except BaseException:
            pass
        # Romper el mainloop con el minimo trabajo Tcl. El cleanup pesado corre en
        # run()'s finally, con el stack ya desenrollado.
        try:
            self.root.quit()
        except BaseException:
            pass

    def _crash_log_path(self) -> Path:
        return self._log_path.parent / "jarvis_crash.log"

    def _append_crash_log(self, header: str, exc_type, exc_value, exc_tb) -> None:
        """Append-only a data/jarvis_crash.log. I/O puro: seguro desde el handler."""
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self._crash_log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"\n{'=' * 70}\n{stamp} | {header}\n{'=' * 70}\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        except Exception:
            pass

    def _dump_recursion_diagnostic(self, exc_type, exc_value) -> None:
        """Vuelca el CICLO de recursion recorriendo la PILA VIVA (no el traceback,
        que aqui es superficial). El recorrido por f_back es ITERATIVO: no agrega
        profundidad. La funcion que aparece cientos de veces ES el disparador.
        """
        import collections

        counts: "collections.Counter[tuple[str, str]]" = collections.Counter()
        top_frames: list[str] = []
        f = sys._getframe()
        depth = 0
        while f is not None:
            code = f.f_code
            counts[(code.co_name, code.co_filename)] += 1
            if depth < 60:
                top_frames.append(f"  {code.co_name}  {code.co_filename}:{f.f_lineno}")
            depth += 1
            f = f.f_back
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self._crash_log_path(), "a", encoding="utf-8") as fh:
            fh.write(f"\n{'=' * 70}\n{stamp} | RecursionError CAPTURADO (no fatal)\n{'=' * 70}\n")
            fh.write(f"Excepcion: {getattr(exc_type, '__name__', exc_type)}: {exc_value}\n")
            fh.write(f"Profundidad de la pila viva: {depth} frames\n")
            fh.write("Funciones mas repetidas en la pila (el top es el CICLO):\n")
            for (name, filename), n in counts.most_common(10):
                fh.write(f"  {n:>5}x  {name}  ({filename})\n")
            fh.write("\nPrimeros 60 frames desde el tope de la pila:\n")
            for line in top_frames:
                fh.write(line + "\n")
            fh.flush()

    def reset_transcripts(self) -> None:
        self.input_text.delete("1.0", "end")
        self.output_text.delete("1.0", "end")
        self.log_event("Transcript reiniciado")

    def _on_blocked(self, provider: str) -> None:
        self.set_state("blocked")
        self.log_event(f"Budget {provider} agotado", "error")
        self.append_output(f"\n[Budget {provider} agotado - invocaciones nuevas bloqueadas]\n")

    def show_approval(self, action, on_decision) -> None:
        """Muestra una aprobacion HITL para acciones de riesgo."""
        dialog = ApprovalDialog(
            root=self.root,
            action=action,
            on_decision=on_decision,
            log_event=self.log_event,
        )
        self._approval_dialogs.append(dialog)
        dialog.set_on_close(self._forget_approval_dialog)
        dialog.show()

    def _forget_approval_dialog(self, dialog: ApprovalDialog) -> None:
        try:
            self._approval_dialogs.remove(dialog)
        except ValueError:
            pass

    def _privacy_enabled(self) -> bool:
        flag = os.environ.get("JARVIS_HIDE_FROM_CAPTURE", "false").strip().lower()
        return flag in ("true", "1", "yes")

    def _privacy_label_text(self) -> str:
        return "Captura oculta" if self._privacy_enabled() else "Captura visible"

    def _privacy_label_color(self) -> str:
        return OK if self._privacy_enabled() else WARN

    # ---- Camera preview ----

    def set_camera_active(self, active: bool) -> None:
        """Muestra/oculta el preview e indica visualmente que la camara esta ON."""
        if active:
            self._camera_preview.show()
            self.log_event("CAMARA ACTIVA (modo vision)", "warn")
        else:
            self._camera_preview.hide()
            self.log_event("Camara apagada", "ok")

    def update_camera_preview(self, frame) -> None:
        self._camera_preview.update_frame(frame.jpeg_bytes)

    def set_camera_focus(self, box_px, label: str = "") -> None:
        self._camera_preview.set_focus_box(box_px, label)

    # ---- Lifecycle ----

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_recurring_callbacks()
        for dialog in list(self._approval_dialogs):
            try:
                dialog.decide(False)
            except Exception:
                pass
        self._approval_dialogs.clear()
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self._on_close()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _stop_recurring_callbacks(self) -> None:
        try:
            after_ids = self.root.tk.call("after", "info")
            if isinstance(after_ids, str):
                after_ids = after_ids.split()
            for after_id in after_ids:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.command_center.close()
        except Exception:
            pass
        try:
            self.footer.stop()
        except Exception:
            pass
        try:
            self.core_visual.stop_animation()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    tr = TokenTracker()
    gate = BudgetGate()
    overlay = JarvisOverlay(tr, gate)

    states = ["idle", "listening", "thinking", "speaking", "idle"]
    state_idx = [0]

    def cycle():
        s = states[state_idx[0] % len(states)]
        overlay.set_state(s)
        overlay.log_event(f"Estado: {STATE_LABELS[s]}")
        state_idx[0] += 1
        overlay.root.after(2000, cycle)

    overlay.root.after(500, cycle)
    overlay.append_input("Hola Jarvis, que tal?")
    overlay.append_output("Bien, listo para conversar. ")

    def burn():
        tr.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=2000)
        tr.record("claude-sonnet-4-6", output_tokens=100)
        overlay.root.after(800, burn)

    overlay.root.after(800, burn)

    mode_idx = [0]

    def toggle_mode():
        mode_idx[0] += 1
        overlay.set_mode("LIBRE" if mode_idx[0] % 2 == 1 else "PTT")
        overlay.root.after(5000, toggle_mode)

    overlay.root.after(3000, toggle_mode)

    print("[INFO] Overlay abierto. Cierra con la X o Ctrl+C en terminal.")
    overlay.run()
    print("[OK] JarvisOverlay smoke test completed")
