"""
overlay/window.py - Ventana tkinter overlay para Jarvis.

Caracteristicas:
  - Borderless (overrideredirect=True), always-on-top, alpha 0.93
  - Invisible a Zoom/Teams/OBS via WDA_EXCLUDEFROMCAPTURE (Win32)
  - Drag-to-move por header
  - Indicador de estado: idle / listening / thinking / speaking
  - Indicador de modo: PTT / LIBRE
  - Transcript en vivo (input + output)
  - Footer de telemetria embebido (de telemetry_footer.py)

Thread-safety: el overlay corre en main thread. La sesion publica eventos
desde su thread asyncio; el orquestador (jarvis.py) marshalla via
root.after(0, callback) para tocar widgets desde fuera.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from typing import Callable

from overlay.telemetry_footer import TelemetryFooter
from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker

# Win32 para invisibilidad en screen capture
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# Paleta "JARVIS": cyan glow sobre dark casi negro azulado.
# Match con el splash visual que mando Isaac. Los bordes/labels usan
# el cyan core (mismo color del filamento del logo) atenuado por capa.
BG = "#080e16"            # fondo principal: casi negro con tinte azul
PANEL = "#0e1620"         # paneles internos un escalon mas claro
ACCENT = "#7ff4f8"        # cyan filamento (estado listening, "JARVIS" label)
ACCENT_DIM = "#4dd8dc"    # cyan halo (botones secundarios)
TEXT_PRIMARY = "#c8fafc"  # texto bright (lo que dice Jarvis, transcript output)
TEXT_DIM = "#7aa3ab"      # texto medio (input transcript, modo)
TEXT_FAINT = "#4a6e78"    # texto faint (hints, hotkeys)
BORDER = "#1a3f4a"        # borde sutil de paneles

STATE_COLORS = {
    "idle": "#4a6e78",        # cyan apagado (faint)
    "listening": "#7ff4f8",   # cyan brillante (matching ACCENT)
    "thinking": "#fcd34d",    # ambar (preservado: distingue de cyan)
    "speaking": "#c8fafc",    # cyan muy claro (matching TEXT_PRIMARY)
    "blocked": "#ef4444",     # rojo (preservado: alerta de seguridad)
}

STATE_GLYPHS = {
    "idle": "○",
    "listening": "🎙",
    "thinking": "◌",
    "speaking": "◉",
    "blocked": "■",
}


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

        self.root = tk.Tk()
        self.root.title("JARVIS")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.94)
        self.root.configure(bg=BG)
        # 640px da espacio al footer (Gemini + Claude + total sin truncado) y
        # al transcript de Jarvis, que ahora recibe texto completo de
        # output_transcription en lugar de solo audio.
        self.root.geometry("640x360+80+80")

        # Branding: ícono de taskbar + logo para el header.
        # Si los assets no existen (entorno minimal), seguimos sin branding.
        self._logo_image: tk.PhotoImage | None = None
        self._load_brand_assets()

        self._build_ui()
        self._enable_capture_invisibility()
        self._bind_drag()

        self._state = "idle"
        self._mode = "PTT"
        self._update_state_visual()

    # ---- UI construction ----

    def _load_brand_assets(self) -> None:
        """Carga icon.ico (taskbar) + logo_64.png (header) si existen.

        Mantenemos PhotoImage como atributo de instancia: sin esta referencia
        Tk garbage-collectea la imagen y desaparece del widget. Patron tipico
        de tkinter — no es un leak, es un workaround obligatorio.
        """
        from pathlib import Path
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
                # Para Linux/macOS donde iconbitmap no acepta .ico:
                try:
                    self.root.iconphoto(False, self._logo_image)
                except Exception:
                    pass
            except Exception as exc:
                print(f"[overlay] PhotoImage fallo: {exc}")

    def _build_ui(self) -> None:
        # Header (drag handle + logo + estado + modo + close)
        self.header = tk.Frame(self.root, bg=PANEL, height=40)
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)

        # Logo del hexagono JARVIS a la izquierda del titulo.
        # Solo aparece si los assets se cargaron.
        if self._logo_image is not None:
            logo_label = tk.Label(
                self.header, image=self._logo_image, bg=PANEL,
                padx=8,
            )
            logo_label.pack(side="left", padx=(4, 0))
            # Permitir drag desde el logo tambien
            self._logo_label = logo_label

        self.state_label = tk.Label(
            self.header, text="○ Jarvis", bg=PANEL, fg=TEXT_PRIMARY,
            font=("Consolas", 11, "bold"), padx=8,
        )
        self.state_label.pack(side="left")

        self.mode_label = tk.Label(
            self.header, text="PTT", bg=PANEL, fg=TEXT_DIM,
            font=("Consolas", 9, "bold"), padx=8,
        )
        self.mode_label.pack(side="left")

        self.connection_label = tk.Label(
            self.header, text="Gemini: iniciando", bg=PANEL, fg=TEXT_FAINT,
            font=("Consolas", 8), padx=8,
        )
        self.connection_label.pack(side="left")

        self.close_btn = tk.Label(
            self.header, text="×", bg=PANEL, fg=TEXT_DIM,
            font=("Segoe UI", 14), padx=12, cursor="hand2",
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda _: self.close())

        # Cuerpo: 2 paneles - input transcript + output transcript
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(8, 4))

        # Panel "Tu": header con label + boton copiar/limpiar
        self.input_text = self._build_transcript_panel(
            body, title="Tu", title_color=TEXT_FAINT,
            text_color=TEXT_DIM,
        )

        # Panel "JARVIS": mismo patron. Aqui aparece tanto el audio transcrito
        # (output_transcription de Gemini) como cualquier texto que escriba
        # Jarvis directamente (raros, tipo errores o respuestas con tool).
        self.output_text = self._build_transcript_panel(
            body, title="JARVIS", title_color=ACCENT,
            text_color=TEXT_PRIMARY,
        )

        # Hint legend (hotkeys)
        hint = tk.Label(
            self.root,
            text="Ctrl: hablar · Shift+M: libre · Shift+S: pantalla · Alt+S: region · Alt+Q: cerrar",
            bg=BG, fg=TEXT_FAINT, font=("Segoe UI", 8),
        )
        hint.pack(fill="x", padx=10, pady=(4, 4))

        # Footer telemetria
        self.footer = TelemetryFooter(self.root, self.tracker, self.gate, on_blocked=self._on_blocked)
        self.footer.pack(side="bottom", fill="x")

        # Border alrededor del overlay
        for side in ("top", "bottom", "left", "right"):
            tk.Frame(self.root, bg=BORDER, height=1, width=1).place(
                **{"x" if side in ("left",) else ("relx" if side == "right" else "x"): 0}
            )

    def _build_transcript_panel(
        self,
        parent: tk.Misc,
        title: str,
        title_color: str,
        text_color: str,
    ) -> tk.Text:
        """Construye un panel con header (titulo + botones copy/clear) + Text widget.

        El Text es de solo-lectura para el usuario (bloquea inserts via teclado)
        pero permite seleccion con mouse, Ctrl+C, Ctrl+A. Las escrituras
        programaticas (`.insert(...)` desde callbacks) siguen funcionando.

        Retorna el Text widget para que append_input/append_output lo usen.
        """
        panel = tk.Frame(parent, bg=BG)
        panel.pack(fill="both", expand=True, pady=(2, 6))

        # Header con titulo + acciones
        header = tk.Frame(panel, bg=BG)
        header.pack(fill="x")

        tk.Label(
            header, text=title, bg=BG, fg=title_color,
            font=("Segoe UI", 8, "bold"), anchor="w",
        ).pack(side="left")

        copy_btn = tk.Label(
            header, text="📋 copiar", bg=BG, fg=TEXT_FAINT,
            font=("Segoe UI", 8), cursor="hand2", padx=8,
        )
        copy_btn.pack(side="right")

        clear_btn = tk.Label(
            header, text="✕ limpiar", bg=BG, fg=TEXT_FAINT,
            font=("Segoe UI", 8), cursor="hand2", padx=4,
        )
        clear_btn.pack(side="right")

        # Cuerpo: Text + Scrollbar vertical (manual, sin scrolledtext)
        body = tk.Frame(panel, bg=BG)
        body.pack(fill="both", expand=True, pady=(2, 0))

        scrollbar = tk.Scrollbar(body, bg=BG, troughcolor=PANEL)
        scrollbar.pack(side="right", fill="y")

        text = tk.Text(
            body, height=4, bg=PANEL, fg=text_color,
            insertbackground=text_color,
            font=("Segoe UI", 10), borderwidth=0, highlightthickness=1,
            highlightbackground=BORDER, wrap="word",
            yscrollcommand=scrollbar.set,
        )
        text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text.yview)

        # Readonly para usuario, escribible por codigo
        self._make_readonly_but_copyable(text)

        # Wire botones (closures sobre `text` y `copy_btn`)
        copy_btn.bind("<Button-1>", lambda _: self._copy_to_clipboard(text, copy_btn))
        clear_btn.bind("<Button-1>", lambda _: text.delete("1.0", "end"))

        return text

    @staticmethod
    def _make_readonly_but_copyable(widget: tk.Text) -> None:
        """Bloquea teclas que insertan texto pero permite copy/select/navegacion.

        Truco: bind '<Key>' que retorna 'break' por default; permite explicitamente
        flechas, Home/End, PageUp/Down, y combos Ctrl+C / Ctrl+A / Ctrl+Insert.
        Los .insert() y .delete() programaticos NO disparan <Key>, asi que el
        codigo sigue pudiendo escribir.
        """
        nav_keys = (
            "Left", "Right", "Up", "Down",
            "Prior", "Next", "Home", "End",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
        )

        def on_key(event):
            ctrl = bool(event.state & 0x4)
            if event.keysym in nav_keys:
                return None  # propagar (mover cursor / extender seleccion)
            if ctrl and event.keysym.lower() in ("c", "a", "x", "insert"):
                return None
            return "break"  # bloquear cualquier otra tecla

        widget.bind("<Key>", on_key)

        # Ctrl+A: select all (Tk no lo trae por default en Text)
        def select_all(_):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
            return "break"
        widget.bind("<Control-a>", select_all)
        widget.bind("<Control-A>", select_all)

    def _copy_to_clipboard(self, widget: tk.Text, feedback_btn: tk.Label | None = None) -> None:
        """Copia todo el contenido del widget al portapapeles del SO.

        Si hay seleccion activa, copia solo eso. Si no, copia todo el panel.
        Feedback visual brevisimo en el boton para confirmar la accion.
        """
        try:
            # Preferir seleccion si la hay
            content = widget.selection_get()
        except tk.TclError:
            content = widget.get("1.0", "end-1c").strip()

        if not content:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        # En Windows, update() asegura que el clipboard sobreviva al cierre del overlay
        self.root.update()

        if feedback_btn is not None:
            original_text = feedback_btn.cget("text")
            original_fg = feedback_btn.cget("fg")
            feedback_btn.config(text="✓ copiado", fg=ACCENT)
            self.root.after(
                1200,
                lambda: feedback_btn.config(text=original_text, fg=original_fg),
            )

    def _enable_capture_invisibility(self) -> None:
        """Excluye la ventana de screen capture en Windows 10+.

        Controlado por env var JARVIS_HIDE_FROM_CAPTURE:
          - 'true' (o '1', 'yes'): overlay invisible en screenshots, OBS, Zoom, Teams.
            Util para stealth durante meetings o presentaciones.
          - cualquier otro valor (o no setear): overlay normal, capturable.
            Default. Permite hacer screenshots para debug, soporte, etc.
        """
        if sys.platform != "win32":
            return
        flag = os.environ.get("JARVIS_HIDE_FROM_CAPTURE", "false").strip().lower()
        if flag not in ("true", "1", "yes"):
            print("[overlay] capture invisibility OFF (overlay visible en screenshots)")
            return
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            print("[overlay] capture invisibility ON (overlay oculto en screenshots)")
        except Exception as exc:
            print(f"[overlay] no pude excluir de capture: {exc}")

    def _bind_drag(self) -> None:
        """Permite arrastrar la ventana presionando el header."""
        def start_drag(e):
            self._drag_x = e.x
            self._drag_y = e.y

        def do_drag(e):
            x = self.root.winfo_pointerx() - self._drag_x
            y = self.root.winfo_pointery() - self._drag_y
            self.root.geometry(f"+{x}+{y}")

        draggables = [self.header, self.state_label, self.mode_label]
        if hasattr(self, "_logo_label"):
            draggables.append(self._logo_label)
        for w in draggables:
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", do_drag)

    # ---- State updates (call from main thread) ----

    def set_state(self, state: str) -> None:
        if state not in STATE_COLORS:
            return
        self._state = state
        self._update_state_visual()

    def set_mode(self, mode: str) -> None:
        """mode: 'PTT' | 'LIBRE'"""
        self._mode = mode
        color = ACCENT if mode == "LIBRE" else TEXT_DIM
        text = "LIBRE 🟢" if mode == "LIBRE" else "PTT"
        self.mode_label.config(text=text, fg=color)

    def set_connection_status(self, status: str, detail: str = "") -> None:
        colors = {
            "connecting": "#fcd34d",
            "connected": ACCENT,
            "reconnecting": "#f97316",
            "error": "#ef4444",
            "stopped": TEXT_FAINT,
        }
        labels = {
            "connecting": "Gemini: conectando",
            "connected": "Gemini: vivo",
            "reconnecting": "Gemini: reconectando",
            "error": "Gemini: error",
            "stopped": "Gemini: detenido",
        }
        label = labels.get(status, f"Gemini: {status}")
        if detail:
            label = f"{label} · {detail[:36]}"
        self.connection_label.config(text=label, fg=colors.get(status, TEXT_FAINT))

    def _update_state_visual(self) -> None:
        glyph = STATE_GLYPHS.get(self._state, "○")
        color = STATE_COLORS.get(self._state, TEXT_DIM)
        text_map = {
            "idle": "esperando",
            "listening": "te escucho",
            "thinking": "pensando",
            "speaking": "respondiendo",
            "blocked": "BLOQUEADO (budget)",
        }
        label = text_map.get(self._state, self._state)
        self.state_label.config(text=f"{glyph}  Jarvis · {label}", fg=color)

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

    def reset_transcripts(self) -> None:
        self.input_text.delete("1.0", "end")
        self.output_text.delete("1.0", "end")

    def _on_blocked(self, provider: str) -> None:
        self.set_state("blocked")
        self.append_output(f"\n[Budget {provider} agotado — invocaciones nuevas bloqueadas]\n")

    def show_approval(self, action, on_decision) -> None:
        """Muestra una aprobacion HITL para acciones de riesgo."""
        win = tk.Toplevel(self.root)
        win.title("Jarvis approval")
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.geometry("520x260+120+120")
        win.resizable(False, False)

        resolved = {"done": False}

        def decide(approved: bool) -> None:
            if resolved["done"]:
                return
            resolved["done"] = True
            try:
                on_decision(action.id, approved)
            finally:
                try:
                    win.destroy()
                except Exception:
                    pass

        tk.Label(
            win,
            text=action.title,
            bg=BG,
            fg="#facc15" if action.risk != "destructive" else "#ef4444",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            padx=14,
            pady=10,
        ).pack(fill="x")

        tk.Label(
            win,
            text=f"Riesgo: {action.risk}",
            bg=BG,
            fg=TEXT_DIM,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=14,
        ).pack(fill="x")

        details = tk.Text(
            win,
            height=7,
            bg=PANEL,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=("Consolas", 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            wrap="word",
        )
        details.pack(fill="both", expand=True, padx=14, pady=10)
        details.insert("1.0", action.details)
        details.config(state="disabled")

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 14))

        tk.Button(
            btns,
            text="Rechazar",
            command=lambda: decide(False),
            bg="#1f2937",
            fg=TEXT_PRIMARY,
            activebackground="#374151",
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=16,
            pady=6,
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            btns,
            text="Aprobar",
            command=lambda: decide(True),
            bg=ACCENT_DIM,
            fg="#06110c",
            activebackground=ACCENT,
            activeforeground="#06110c",
            relief="flat",
            padx=16,
            pady=6,
        ).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", lambda: decide(False))
        win.bind("<Escape>", lambda _: decide(False))
        win.after(int(action.timeout_s * 1000), lambda: decide(False))
        try:
            win.focus_force()
        except Exception:
            pass

    # ---- Lifecycle ----

    def close(self) -> None:
        try:
            self._on_close()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def run(self) -> None:
        self.root.mainloop()


# Smoke test: muestra el overlay con estados simulados
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    tr = TokenTracker()
    gate = BudgetGate()
    overlay = JarvisOverlay(tr, gate)

    # Simular ciclo de estados cada 2s
    states = ["idle", "listening", "thinking", "speaking", "idle"]
    state_idx = [0]

    def cycle():
        s = states[state_idx[0] % len(states)]
        overlay.set_state(s)
        state_idx[0] += 1
        overlay.root.after(2000, cycle)

    overlay.root.after(500, cycle)

    # Simular transcripts
    overlay.append_input("Hola Jarvis, que tal?")
    overlay.append_output("Bien, listo para conversar. ")

    # Simular gasto crece
    def burn():
        tr.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=2000)
        tr.record("claude-sonnet-4-6", output_tokens=100)
        overlay.root.after(800, burn)

    overlay.root.after(800, burn)

    # Toggle mode cada 5s
    mode_idx = [0]
    def toggle_mode():
        mode_idx[0] += 1
        overlay.set_mode("LIBRE" if mode_idx[0] % 2 == 1 else "PTT")
        overlay.root.after(5000, toggle_mode)

    overlay.root.after(3000, toggle_mode)

    print("[INFO] Overlay abierto. Cierra con la X o Ctrl+C en terminal.")
    overlay.run()
    print("[OK] JarvisOverlay smoke test completed")
