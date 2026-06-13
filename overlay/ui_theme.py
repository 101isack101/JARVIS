"""Shared Tkinter colors and labels for the Jarvis overlay UI."""

BG = "#05070b"
PANEL = "#0b0f15"
PANEL_SOFT = "#0d141b"
SURFACE = "#10161d"
SURFACE_ALT = "#070b10"
CONTROL = "#151d25"
CONTROL_HOVER = "#1c2a34"
CONTROL_ACTIVE = "#142b34"
ACCENT = "#a9f7ff"
ACCENT_DIM = "#6edee9"
OK = "#9cf5d5"
TEXT_PRIMARY = "#f0fbfc"
TEXT_DIM = "#a6bbc2"
TEXT_FAINT = "#647981"
BORDER = "#263842"
BORDER_SOFT = "#17232b"
WARN = "#f6d77a"
DANGER = "#ff6b72"
WARN_BG = "#211b0e"
DANGER_BG = "#241115"

WINDOW_RADIUS = 22
CONTROL_RADIUS = 10

FONT_DISPLAY = "Bahnschrift SemiCondensed"
FONT_UI = "Lato"
FONT_MONO = "Consolas"

STATE_COLORS = {
    "idle": TEXT_FAINT,
    "listening": ACCENT,
    "thinking": WARN,
    "speaking": OK,
    "blocked": DANGER,
}

STATE_LABELS = {
    "idle": "Listo",
    "listening": "Escuchando",
    "thinking": "Procesando",
    "speaking": "Respondiendo",
    "blocked": "Bloqueado por budget",
}

STATE_DETAILS = {
    "idle": "Mantener Ctrl para hablar",
    "listening": "Escuchando tu voz",
    "thinking": "Procesando la respuesta",
    "speaking": "Audio en reproduccion",
    "blocked": "Revisa budgets para continuar",
}
