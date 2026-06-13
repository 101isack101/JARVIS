"""Animated JARVIS core canvas for the overlay."""

from __future__ import annotations

import array
import math
import random
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from overlay.ui_theme import ACCENT, BG, BORDER, BORDER_SOFT, DANGER, OK, TEXT_FAINT, WARN

AA_SCALE = 2
FRAME_MS_ACTIVE = 40
FRAME_MS_THINKING = 70
FRAME_MS_IDLE = 120


class JarvisCoreCanvas(tk.Canvas):
    """Voice-reactive cyan core inspired by the JARVIS visual identity."""

    def __init__(self, master: tk.Misc, height: int = 190) -> None:
        super().__init__(
            master,
            height=height,
            bg=BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self._state = "idle"
        self._voice_level = 0.0
        self._target_level = 0.0
        self._hover = False
        self._pulse = 0.0
        self._t = 0.0
        self._compact = False
        self._after_id: str | None = None
        self._destroyed = False
        self._image_refs: list[ImageTk.PhotoImage] = []
        self._aa_layer: Image.Image | None = None
        self._aa_draw: ImageDraw.ImageDraw | None = None
        self._aa_glow_layer: Image.Image | None = None
        self._aa_glow_draw: ImageDraw.ImageDraw | None = None
        self._aa_origin = (0, 0)
        self._aa_photo_cache: dict[tuple, tuple[ImageTk.PhotoImage, tuple[int, int]]] = {}
        self._aa_cache_order: list[tuple] = []
        self._canvas_w = 1
        self._canvas_h = 1

        rng = random.Random(7)
        self._particles = [
            (
                rng.random() * math.tau,
                math.sqrt(rng.random()),
                rng.uniform(-0.9, 0.9),
                rng.uniform(1.0, 2.4),
                rng.random() * math.tau,
            )
            for _ in range(82)
        ]
        self._nodes = [
            (
                rng.random(),
                rng.random(),
                rng.uniform(0.2, 0.8),
                rng.random() * math.tau,
            )
            for _ in range(24)
        ]

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "speaking":
            self._pulse = max(self._pulse, 0.35)
        elif state == "listening":
            self._pulse = max(self._pulse, 0.18)
        elif state == "thinking":
            self._pulse = max(self._pulse, 0.12)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.configure(height=136 if compact else 224)
        self._pulse = max(self._pulse, 0.28)

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Feed PCM int16 audio bytes so the rings follow Jarvis' voice."""
        if self._destroyed:
            return
        if len(pcm_bytes) < 2:
            return
        even_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
        samples = array.array("h")
        samples.frombytes(pcm_bytes[:even_len])
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return

        step = max(1, len(samples) // 700)
        picked = samples[::step]
        total = 0.0
        for sample in picked:
            total += float(sample) * float(sample)
        rms = math.sqrt(total / max(1, len(picked))) / 32768.0
        self._target_level = min(1.0, max(self._target_level, rms * 7.5))

    def ping(self) -> None:
        self._pulse = 1.0

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._pulse = max(self._pulse, 0.35)

    def _on_leave(self, _event=None) -> None:
        self._hover = False

    def _on_click(self, _event=None) -> None:
        self.ping()

    def _on_destroy(self, _event=None) -> None:
        self.stop_animation()

    def stop_animation(self) -> None:
        self._destroyed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule(self, delay_ms: int | None = None) -> None:
        if self._destroyed:
            return
        self._after_id = self.after(delay_ms or self._frame_delay_ms(), self._tick)

    def _tick(self) -> None:
        if self._destroyed:
            return
        self._after_id = None
        frame_ms = self._frame_delay_ms()
        dt = frame_ms / 1000.0
        frame_factor = max(0.35, dt / 0.033)
        self._t += dt
        self._target_level *= 0.90 ** frame_factor
        self._voice_level += (self._target_level - self._voice_level) * min(1.0, 0.28 * frame_factor)
        self._pulse *= 0.92 ** frame_factor
        self._draw()
        self._schedule(frame_ms)

    def _frame_delay_ms(self) -> int:
        if self._state in {"listening", "speaking"} or self._voice_level > 0.035 or self._target_level > 0.035 or self._pulse > 0.04:
            return FRAME_MS_ACTIVE
        if self._state == "thinking" or self._hover:
            return FRAME_MS_THINKING
        return FRAME_MS_IDLE

    def _state_energy(self) -> float:
        if self._state == "speaking":
            return 0.22
        if self._state == "listening":
            return 0.12
        if self._state == "thinking":
            return 0.09
        if self._state == "blocked":
            return 0.05
        return 0.026

    def _accent(self) -> str:
        if self._state == "thinking":
            return WARN
        if self._state == "blocked":
            return DANGER
        if self._state == "speaking":
            return OK
        return ACCENT

    def _draw(self) -> None:
        self.delete("all")
        self._image_refs = []
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        self._canvas_w = w
        self._canvas_h = h
        cx = w / 2
        cy = h * (0.50 if self._compact else 0.52)
        base = min(42.0 if self._compact else 58.0, h * 0.28, w * 0.095)
        energy = min(1.0, max(self._state_energy(), self._voice_level + self._pulse * 0.22))
        accent = self._accent()

        self._draw_network(w, h, cx)
        if not self._compact:
            self._draw_horizon(w, h)
        self._draw_side_waves(w, h, cx, cy, energy)
        aa_key = self._aa_key(w, h, energy)
        cached = self._aa_photo_cache.get(aa_key)
        if cached is None:
            max_r = base + 104 + energy * 28
            self._begin_aa_layer(w, h, cx, cy, max_r)
            self._draw_rings(cx, cy, base, energy, accent)
            self._draw_state_signature(cx, cy, base, energy, accent)
            self._draw_core_marker(cx, cy, energy, accent)
            self._flush_aa_layer(aa_key)
        else:
            self._draw_cached_aa_layer(cached)
        self._draw_particle_core(cx, cy, base, energy, accent)
        self._draw_live_signature(cx, cy, base, energy)

    def _aa_key(self, w: int, h: int, energy: float) -> tuple:
        return (
            w,
            h,
            self._compact,
            self._state,
            int(energy * 3),
        )

    def _begin_aa_layer(self, w: int, h: int, cx: float | None = None, cy: float | None = None, radius: float | None = None) -> None:
        """Start a supersampled transparent layer for smooth rings/arcs."""
        if cx is None or cy is None or radius is None:
            x0, y0, x1, y1 = 0, 0, w, h
        else:
            pad = 10
            x0 = max(0, int(cx - radius - pad))
            y0 = max(0, int(cy - radius - pad))
            x1 = min(w, int(cx + radius + pad))
            y1 = min(h, int(cy + radius + pad))
        self._aa_origin = (x0, y0)
        layer_w = max(1, x1 - x0)
        layer_h = max(1, y1 - y0)
        self._aa_layer = Image.new("RGBA", (layer_w * AA_SCALE, layer_h * AA_SCALE), (0, 0, 0, 0))
        self._aa_draw = ImageDraw.Draw(self._aa_layer)
        self._aa_glow_layer = Image.new("RGBA", (layer_w * AA_SCALE, layer_h * AA_SCALE), (0, 0, 0, 0))
        self._aa_glow_draw = ImageDraw.Draw(self._aa_glow_layer)

    def _flush_aa_layer(self, cache_key: tuple | None = None) -> None:
        if self._aa_layer is None:
            return
        if self._aa_glow_layer is not None:
            layer = self._aa_glow_layer
            layer.alpha_composite(self._aa_layer)
        else:
            layer = self._aa_layer
        out_w = max(1, layer.width // AA_SCALE)
        out_h = max(1, layer.height // AA_SCALE)
        img = layer.resize((out_w, out_h), Image.Resampling.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        if cache_key is not None:
            self._store_aa_cache(cache_key, photo, self._aa_origin)
        self._image_refs.append(photo)
        self.create_image(self._aa_origin[0], self._aa_origin[1], image=photo, anchor="nw")
        self._aa_layer = None
        self._aa_draw = None
        self._aa_glow_layer = None
        self._aa_glow_draw = None

    def _store_aa_cache(self, key: tuple, photo: ImageTk.PhotoImage, origin: tuple[int, int]) -> None:
        if key not in self._aa_photo_cache:
            self._aa_cache_order.append(key)
        self._aa_photo_cache[key] = (photo, origin)
        while len(self._aa_cache_order) > 72:
            oldest = self._aa_cache_order.pop(0)
            self._aa_photo_cache.pop(oldest, None)

    def _draw_cached_aa_layer(self, cached: tuple[ImageTk.PhotoImage, tuple[int, int]]) -> None:
        photo, origin = cached
        self._image_refs.append(photo)
        self.create_image(
            origin[0],
            origin[1],
            image=photo,
            anchor="nw",
        )

    @staticmethod
    def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
        color = color.lstrip("#")
        return (
            int(color[0:2], 16),
            int(color[2:4], 16),
            int(color[4:6], 16),
            max(0, min(255, alpha)),
        )

    def _aa_oval(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        outline: str | None = None,
        width: int = 1,
        fill: str | None = None,
        alpha: int = 255,
        soft: bool = False,
    ) -> None:
        if self._aa_draw is None:
            return
        s = AA_SCALE
        ox, oy = self._aa_origin
        box = (
            int((cx - r - ox) * s),
            int((cy - r - oy) * s),
            int((cx + r - ox) * s),
            int((cy + r - oy) * s),
        )
        self._aa_draw.ellipse(
            box,
            outline=self._rgba(outline, alpha) if outline else None,
            width=max(1, int(width * s)),
            fill=self._rgba(fill, alpha) if fill else None,
        )
        if soft and outline and self._aa_glow_draw is not None:
            self._aa_glow_draw.ellipse(
                box,
                outline=self._rgba(outline, max(24, int(alpha * 0.30))),
                width=max(1, int((width + 2) * s)),
            )

    def _aa_arc(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        start: float,
        extent: float,
        outline: str,
        width: int = 1,
        alpha: int = 255,
        soft: bool = False,
    ) -> None:
        if self._aa_draw is None:
            return
        s = AA_SCALE
        ox, oy = self._aa_origin
        box = (
            int((cx - r - ox) * s),
            int((cy - r - oy) * s),
            int((cx + r - ox) * s),
            int((cy + r - oy) * s),
        )
        self._aa_draw.arc(
            box,
            start=float(start),
            end=float(start + extent),
            fill=self._rgba(outline, alpha),
            width=max(1, int(width * s)),
        )
        if soft and self._aa_glow_draw is not None:
            self._aa_glow_draw.arc(
                box,
                start=float(start),
                end=float(start + extent),
                fill=self._rgba(outline, max(22, int(alpha * 0.34))),
                width=max(1, int((width + 2) * s)),
            )

    def _aa_line(
        self,
        points: list[tuple[float, float]],
        *,
        fill: str,
        width: int = 1,
        alpha: int = 255,
    ) -> None:
        if self._aa_draw is None or len(points) < 2:
            return
        s = AA_SCALE
        ox, oy = self._aa_origin
        scaled = [(int((x - ox) * s), int((y - oy) * s)) for x, y in points]
        self._aa_draw.line(scaled, fill=self._rgba(fill, alpha), width=max(1, int(width * s)), joint="curve")

    def _draw_horizon(self, w: int, h: int) -> None:
        y = h - 16
        for idx, color in enumerate(("#080d12", "#0c151b", "#10242b")):
            self.create_line(0, y + idx * 4, w, y + idx * 4, fill=color, width=1)
        self.create_line(w * 0.30, y - 2, w * 0.70, y - 2, fill=BORDER_SOFT, width=1)

    def _draw_network(self, w: int, h: int, cx: float) -> None:
        if self._compact:
            return
        points: list[tuple[float, float, float]] = []
        for x_norm, y_norm, alpha, phase in self._nodes:
            side = -1 if x_norm < 0.5 else 1
            spread = (0.08 + abs(x_norm - 0.5) * 1.8) * w
            x = cx + side * spread
            y = y_norm * h + math.sin(self._t * 0.7 + phase) * 4
            if 0 <= x <= w and 0 <= y <= h:
                points.append((x, y, alpha))

        for idx, (x1, y1, _) in enumerate(points):
            for x2, y2, _ in points[idx + 1: idx + 5]:
                if (x1 < cx < x2) or (x2 < cx < x1):
                    continue
                dist = math.hypot(x2 - x1, y2 - y1)
                if dist < 104:
                    self.create_line(x1, y1, x2, y2, fill="#111f26", width=1)
        for x, y, alpha in points:
            color = "#2b8f98" if alpha > 0.55 else "#284a52"
            self.create_oval(x - 1.15, y - 1.15, x + 1.15, y + 1.15, fill=color, outline="")

    def _draw_side_waves(self, w: int, h: int, cx: float, cy: float, energy: float) -> None:
        dot = 2.3 if self._compact else 2.6
        spacing = 6 if self._compact else 7
        columns = 3 if self._compact else 4
        rows = 8 if self._compact else 10
        state_boost = {
            "idle": 0.28,
            "listening": 0.86,
            "thinking": 0.48,
            "speaking": 1.0,
            "blocked": 0.45,
        }.get(self._state, 0.5)
        offset = math.sin(self._t * 7.0) * 5 * energy * state_boost
        for side in (-1, 1):
            origin_x = cx + side * min(w * (0.30 if self._compact else 0.36), 260)
            for col in range(columns):
                for row in range(rows):
                    taper = abs(row - rows / 2) / (rows / 2)
                    if taper > 0.82 - col * 0.08:
                        continue
                    wave = math.sin(self._t * 6.5 + col * 0.9 + row * 0.35)
                    intensity = (0.25 + energy * 0.72 + max(0.0, wave) * 0.25) * state_boost
                    x = origin_x + side * (col * spacing * 1.8 + abs(row - rows / 2) * 2.0 + offset)
                    y = cy + (row - rows / 2) * spacing
                    r = dot * min(1.4, intensity)
                    if self._state == "blocked":
                        fill = "#ff6b72" if intensity > 0.66 else "#6c2730"
                    elif self._state == "thinking":
                        fill = "#f6d77a" if intensity > 0.70 else "#655326"
                    else:
                        fill = "#d8fdff" if intensity > 0.82 else "#87edf4" if intensity > 0.55 else "#2b7078"
                    self.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline="")

    def _draw_rings(self, cx: float, cy: float, base: float, energy: float, accent: str) -> None:
        breathing = energy * 5
        for idx, grow in enumerate((34, 24, 15, 6)):
            r = base + grow + breathing + energy * (18 - idx * 3)
            color = ("#10252c", "#1b444d", "#4fc8d2", accent)[idx]
            width = (8, 5, 3, 2)[idx]
            self._aa_oval(cx, cy, r, outline=color, width=width, alpha=225, soft=True)

        for idx in range(3):
            phase = idx * 1.28 + energy * 0.35
            r = base + 42 + idx * 13 + energy * 22
            extent = 70 + energy * 80
            self._aa_arc(
                cx,
                cy,
                r,
                start=math.degrees(phase),
                extent=extent,
                outline=accent,
                width=1,
                alpha=210,
                soft=True,
            )

        inner = base * 0.74
        self._aa_oval(cx, cy, inner, fill=BG, outline=BORDER, width=1, alpha=245)
        self._aa_oval(cx, cy, inner * 0.82, outline=BORDER_SOFT, width=1, alpha=220)

    def _draw_state_signature(self, cx: float, cy: float, base: float, energy: float, accent: str) -> None:
        if self._state == "listening":
            self._draw_listening_signature(cx, cy, base, energy)
        elif self._state == "blocked":
            self._draw_blocked_signature(cx, cy, base)
        else:
            self._draw_idle_signature(cx, cy, base, accent)

    def _draw_live_signature(self, cx: float, cy: float, base: float, energy: float) -> None:
        if self._state == "thinking":
            self._draw_thinking_live(cx, cy, base, energy)
        elif self._state == "speaking":
            self._draw_speaking_live(cx, cy, base, energy)

    def _draw_idle_signature(self, cx: float, cy: float, base: float, accent: str) -> None:
        r = base + 62
        self._aa_arc(cx, cy, r, start=205, extent=28, outline="#24464d", width=1, alpha=190, soft=True)
        self._aa_arc(cx, cy, r, start=327, extent=28, outline="#24464d", width=1, alpha=190, soft=True)
        marker_r = base + 48
        for a in (math.radians(90), math.radians(210), math.radians(330)):
            x = cx + math.cos(a) * marker_r
            y = cy + math.sin(a) * marker_r
            self._aa_oval(x, y, 1.5, fill=accent, alpha=230)

    def _draw_listening_signature(self, cx: float, cy: float, base: float, energy: float) -> None:
        span = 22 + energy * 30
        for side in (-1, 1):
            for idx, offset in enumerate((0, 15, 30)):
                r = base + 54 + offset + energy * 3
                start = 150 if side < 0 else -30
                self._aa_arc(
                    cx,
                    cy,
                    r,
                    start=start,
                    extent=span,
                    outline="#a9f7ff" if idx == 0 else "#3b7780",
                    width=2 if idx == 0 else 1,
                    alpha=220 if idx == 0 else 165,
                    soft=True,
                )
        gate_h = base * 0.72
        for x in (cx - base * 0.95, cx + base * 0.95):
            self._aa_line([(x, cy - gate_h), (x, cy + gate_h)], fill="#0f6670", width=1, alpha=180)

    def _draw_thinking_live(self, cx: float, cy: float, base: float, energy: float) -> None:
        sweep = self._t * 2.8
        length = base + 82 + energy * 15
        x = cx + math.cos(sweep) * length
        y = cy + math.sin(sweep) * length
        self.create_line(cx, cy, x, y, fill="#f6d77a", width=1)
        for idx in range(4):
            a = sweep + idx * math.tau / 4
            orbit = base + 47 + idx * 5
            ox = cx + math.cos(a) * orbit
            oy = cy + math.sin(a) * orbit
            self.create_oval(ox - 1.8, oy - 1.8, ox + 1.8, oy + 1.8, fill="#f6d77a", outline="")
        r = base + 76
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=math.degrees(sweep) - 20, extent=85, style="arc", outline="#7a6219", width=2)

    def _draw_speaking_live(self, cx: float, cy: float, base: float, energy: float) -> None:
        width = 220 if not self._compact else 155
        samples = 38 if not self._compact else 28
        y_mid = cy + base + (54 if not self._compact else 34)
        points: list[float] = []
        amp = 6 + energy * (22 if not self._compact else 14)
        for idx in range(samples):
            x = cx - width / 2 + width * idx / (samples - 1)
            wave = (
                math.sin(self._t * 11 + idx * 0.55)
                + math.sin(self._t * 6.3 + idx * 0.21) * 0.45
            )
            y = y_mid + wave * amp * (0.35 + self._voice_level)
            points.extend((x, y))
        if len(points) >= 4:
            self.create_line(*points, fill="#a9f7ff", width=2, smooth=True)
            self.create_line(cx - width / 2, y_mid, cx + width / 2, y_mid, fill="#203941", width=1)

    def _draw_blocked_signature(self, cx: float, cy: float, base: float) -> None:
        r = base + 76
        self._aa_arc(cx, cy, r, start=18, extent=64, outline=DANGER, width=3, alpha=230, soft=True)
        self._aa_arc(cx, cy, r, start=198, extent=64, outline=DANGER, width=3, alpha=230, soft=True)
        size = base * 0.42
        self._aa_line([(cx - size, cy - size), (cx + size, cy + size)], fill="#7a1d2a", width=2, alpha=230)
        self._aa_line([(cx + size, cy - size), (cx - size, cy + size)], fill="#7a1d2a", width=2, alpha=230)

    def _draw_particle_core(self, cx: float, cy: float, base: float, energy: float, accent: str) -> None:
        radius = base * 0.54 + energy * 4
        for angle, dist, speed, size, phase in self._particles:
            a = angle + self._t * speed
            wobble = math.sin(self._t * 2.2 + phase) * 0.12 * energy
            r = radius * min(1.0, dist + wobble)
            x = cx + math.cos(a) * r
            y = cy + math.sin(a) * r * 0.88
            if (x - cx) ** 2 + ((y - cy) / 0.88) ** 2 > (radius + 2) ** 2:
                continue
            pulse = 0.7 + 0.3 * math.sin(self._t * 5.0 + phase)
            s = size * (0.75 + energy * 0.7) * pulse
            color = accent if pulse > 0.88 else "#8ceff6" if pulse > 0.68 else "#2b7078"
            self.create_oval(x - s, y - s, x + s, y + s, fill=color, outline="")

    def _draw_core_marker(self, cx: float, cy: float, energy: float, accent: str) -> None:
        core_r = 7 + energy * 5
        self._aa_oval(cx, cy, core_r * 2.4, outline="#203941", width=1, alpha=220, soft=True)
        self._aa_oval(cx, cy, core_r, fill=accent, alpha=245)
