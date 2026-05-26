"""
mcp_obsidian/server.py - Servidor MCP stdio para Obsidian.

Uso manual:
  & "H:\\Python311\\python.exe" -m mcp_obsidian.server

Jarvis lo lanza por stdio mediante MCPClient cada vez que invoca la tool
obsidian_mcp. Mantener este server separado permite que en el futuro tambien
lo usen otros clientes MCP.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from . import ops

mcp = FastMCP(
    "Jarvis Obsidian MCP",
    instructions=(
        "Operaciones seguras sobre el vault Obsidian de Isaac: listar, leer, "
        "crear notas, actualizar, append, crear carpetas, mover paths y linkear notas."
    ),
    log_level="ERROR",
)


@mcp.tool()
def obsidian_list_folder(path: str = "", limit: int = 100) -> dict:
    """Lista archivos/carpetas dentro del vault."""
    return ops.list_folder(path=path, limit=limit)


@mcp.tool()
def obsidian_read_note(path: str) -> dict:
    """Lee una nota Markdown por path relativo al vault."""
    return ops.read_note(path=path)


@mcp.tool()
def obsidian_create_folder(path: str) -> dict:
    """Crea una carpeta dentro del vault."""
    return ops.create_folder(path=path)


@mcp.tool()
def obsidian_create_note(
    path: str,
    content: str,
    tags: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """Crea una nota Markdown dentro del vault."""
    return ops.create_note(path=path, content=content, tags=tags, overwrite=overwrite)


@mcp.tool()
def obsidian_update_note(path: str, content: str, tags: list[str] | None = None) -> dict:
    """Sobrescribe una nota Markdown dentro del vault."""
    return ops.update_note(path=path, content=content, tags=tags)


@mcp.tool()
def obsidian_append_note(path: str, content: str, section_title: str = "JARVIS") -> dict:
    """Agrega una seccion al final de una nota."""
    return ops.append_note(path=path, content=content, section_title=section_title)


@mcp.tool()
def obsidian_move_path(path: str, destination: str, overwrite: bool = False) -> dict:
    """Mueve/renombra una nota o carpeta dentro del vault."""
    return ops.move_path(path=path, destination=destination, overwrite=overwrite)


@mcp.tool()
def obsidian_delete_path(path: str) -> dict:
    """Borra un archivo/carpeta si JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true."""
    return ops.delete_path(path=path)


@mcp.tool()
def obsidian_link_notes(note_from: str, note_to: str) -> dict:
    """Agrega [[note_to]] al frontmatter related de note_from."""
    return ops.link_notes(note_from=note_from, note_to=note_to)


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
