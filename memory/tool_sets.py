"""Conjuntos de tools por modo de trabajo.

Gemini Live recibe el contrato completo (tests y schemas), pero el dispatcher
rechaza tools fuera del modo activo. Asi se reduce la superficie efectiva
sin reconectar la sesion de voz.
"""

from __future__ import annotations

ALWAYS_TOOLS: frozenset[str] = frozenset(
    {
        "jarvis_recall",
        "jarvis_session_recall",
        "jarvis_current_session_recall",
        "jarvis_remember",
        "jarvis_browse",
        "jarvis_link",
        "ask_grok_deep",
        "ask_claude_deep",  # alias de ask_grok_deep
        "ask_terra_code",
        "jarvis_skill",
        "jarvis_open_url",
        "jarvis_open_obsidian",
        "jarvis_set_mode",
        "jarvis_get_mode",
        "jarvis_listen_mode",
        "english_practice",
        "jarvis_security_status",
        "spotify_control",
        "obsidian_mcp",
        "jarvis_proactive_check",
        # Crear/consultar misiones L1 no es computer-use; el runtime sigue
        # fail-closed si esta apagado o fuera de root.
        "jarvis_task",
    }
)

GATED_TOOLS: dict[str, frozenset[str]] = {
    "coding": frozenset(
        {
            "jarvis_run_safe_command",
            "jarvis_open_powershell",
            "chrome_read_page",
            "screen_look",
        }
    ),
    "debugging": frozenset(
        {
            "jarvis_run_safe_command",
            "jarvis_open_powershell",
            "chrome_read_page",
            "screen_look",
            "camera_look",
            "camera_watch",
            "camera_focus",
        }
    ),
    "agentic": frozenset(
        {
            "jarvis_run_safe_command",
            "jarvis_open_powershell",
            "chrome_read_page",
            "screen_look",
            "file_organizer",
            "desktop_icons",
        }
    ),
    "planning": frozenset({"file_organizer"}),
    "study": frozenset(
        {
            "study_mode",
            "obs_memory",
            "screen_look",
            "camera_look",
            "camera_watch",
            "camera_focus",
            "chrome_read_page",
        }
    ),
    "meeting": frozenset({"screen_look", "chrome_read_page"}),
    "english": frozenset(),
    "general": frozenset(),
}


def tools_for_mode(mode: str | None) -> frozenset[str]:
    normalized = (mode or "general").strip().lower()
    extra = GATED_TOOLS.get(normalized, frozenset())
    return ALWAYS_TOOLS | extra


def is_tool_allowed_for_mode(name: str, mode: str | None) -> bool:
    return name in tools_for_mode(mode)
