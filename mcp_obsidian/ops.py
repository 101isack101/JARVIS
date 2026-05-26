"""
mcp_obsidian/ops.py - Operaciones seguras sobre el vault Obsidian.

Estas funciones son la capa comun usada por el servidor MCP y los tests.
Guardrails:
  - Todo path queda dentro del vault.
  - Se bloquean carpetas internas como .obsidian, .trash y data oculta.
  - No se sobreescribe ni se mueve encima de algo existente salvo overwrite=True.
  - Borrado desactivado por default: JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault, VaultError
from security.policy import is_secret_path
from security.secret_filter import redact_secrets

BLOCKED_PARTS = {".obsidian", ".trash", ".git"}


def _vault(vault_path: str | None = None) -> ObsidianVault:
    return ObsidianVault(vault_path=vault_path, read_all=True)


def _sanitize_rel_path(path: str, *, ensure_md: bool = False) -> str:
    rel = (path or "").strip().replace("\\", "/").strip("/")
    if not rel:
        raise VaultError("path vacio")
    if ensure_md and not rel.lower().endswith(".md"):
        rel += ".md"
    return rel


def _resolve(vault: ObsidianVault, path: str, *, ensure_md: bool = False) -> Path:
    rel = _sanitize_rel_path(path, ensure_md=ensure_md)
    target = (vault.vault_path / rel).resolve()
    try:
        parts = target.relative_to(vault.vault_path).parts
    except ValueError:
        raise VaultError(f"path fuera del vault: {path}")
    if any(part in BLOCKED_PARTS or part.startswith(".") for part in parts):
        raise VaultError(f"path bloqueado por guardrails: {path}")
    if is_secret_path(target):
        raise VaultError(f"path sensible bloqueado: {path}")
    return target


def _rel(vault: ObsidianVault, path: Path) -> str:
    return str(path.relative_to(vault.vault_path)).replace("\\", "/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_note_anywhere(
    target: Path,
    body: str,
    tags: list[str] | None = None,
    existing_frontmatter: dict | None = None,
) -> notes_mod.Note:
    fm = dict(existing_frontmatter or {})
    fm.setdefault("created", _now_iso())
    fm["updated"] = _now_iso()
    if tags:
        existing = fm.get("tags", [])
        if isinstance(existing, str):
            existing = [existing]
        fm["tags"] = sorted(set(list(existing) + list(tags)))
    note = notes_mod.Note(path=target, frontmatter=fm, body=body)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(note.to_markdown(), encoding="utf-8")
    return note


def list_folder(path: str = "", limit: int = 100, vault_path: str | None = None) -> dict:
    vault = _vault(vault_path)
    target = vault.vault_path if not path else _resolve(vault, path)
    if not target.exists():
        return {"ok": False, "error": f"no existe: {path}", "items": []}
    if not target.is_dir():
        return {"ok": False, "error": f"no es carpeta: {path}", "items": []}
    limit = max(1, min(int(limit or 100), 500))
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:limit]:
        if child.name.startswith(".") or child.name in BLOCKED_PARTS:
            continue
        items.append({
            "path": _rel(vault, child),
            "name": child.name,
            "type": "folder" if child.is_dir() else "note" if child.suffix.lower() == ".md" else "file",
        })
    return {"ok": True, "path": _rel(vault, target) if target != vault.vault_path else "", "items": items}


def read_note(path: str, vault_path: str | None = None) -> dict:
    vault = _vault(vault_path)
    target = _resolve(vault, path, ensure_md=True)
    if not target.exists():
        return {"ok": False, "error": f"nota no existe: {path}"}
    note = notes_mod.read_note(vault, target)
    return {
        "ok": True,
        "path": _rel(vault, target),
        "title": note.title,
        "frontmatter": note.frontmatter,
        "body": redact_secrets(note.body),
    }


def create_folder(path: str, vault_path: str | None = None) -> dict:
    vault = _vault(vault_path)
    target = _resolve(vault, path)
    if target.exists() and not target.is_dir():
        return {"ok": False, "error": f"existe un archivo con ese nombre: {path}"}
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "created": True, "path": _rel(vault, target), "type": "folder"}


def create_note(
    path: str,
    content: str,
    tags: list[str] | None = None,
    overwrite: bool = False,
    vault_path: str | None = None,
) -> dict:
    vault = _vault(vault_path)
    target = _resolve(vault, path, ensure_md=True)
    if target.exists() and not overwrite:
        return {"ok": False, "error": f"nota ya existe: {_rel(vault, target)}"}
    existing_fm = None
    if target.exists():
        existing_fm = notes_mod.parse_note(target.read_text(encoding="utf-8"), target).frontmatter
    note = _write_note_anywhere(target, content, tags=tags or [], existing_frontmatter=existing_fm)
    return {"ok": True, "path": _rel(vault, target), "title": note.title, "tags": note.tags}


def update_note(
    path: str,
    content: str,
    tags: list[str] | None = None,
    vault_path: str | None = None,
) -> dict:
    return create_note(path, content, tags=tags, overwrite=True, vault_path=vault_path)


def append_note(
    path: str,
    content: str,
    section_title: str = "JARVIS",
    vault_path: str | None = None,
) -> dict:
    vault = _vault(vault_path)
    target = _resolve(vault, path, ensure_md=True)
    if target.exists():
        existing = notes_mod.parse_note(target.read_text(encoding="utf-8"), target)
    else:
        existing = notes_mod.Note(path=target, frontmatter={}, body="")
    timestamp = _now_iso()
    section = f"\n\n## {section_title} ({timestamp[:10]})\n\n{content.strip()}\n"
    note = _write_note_anywhere(
        target,
        existing.body.rstrip() + section,
        existing_frontmatter=existing.frontmatter,
    )
    return {"ok": True, "path": _rel(vault, target), "title": note.title}


def move_path(
    path: str,
    destination: str,
    overwrite: bool = False,
    vault_path: str | None = None,
) -> dict:
    vault = _vault(vault_path)
    src = _resolve(vault, path)
    dst = _resolve(vault, destination)
    if not src.exists():
        return {"ok": False, "error": f"origen no existe: {path}"}
    if dst.exists() and not overwrite:
        return {"ok": False, "error": f"destino ya existe: {destination}"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and overwrite:
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))
    return {"ok": True, "from": path, "to": _rel(vault, dst)}


def delete_path(path: str, vault_path: str | None = None) -> dict:
    if os.environ.get("JARVIS_OBSIDIAN_MCP_ALLOW_DELETE", "false").lower() != "true":
        return {"ok": False, "error": "delete desactivado; set JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true"}
    vault = _vault(vault_path)
    target = _resolve(vault, path)
    if not target.exists():
        return {"ok": False, "error": f"no existe: {path}"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True, "deleted": _rel(vault, target)}


def link_notes(note_from: str, note_to: str, vault_path: str | None = None) -> dict:
    vault = _vault(vault_path)
    src = _resolve(vault, note_from, ensure_md=True)
    if not src.exists():
        return {"ok": False, "error": f"nota origen no existe: {note_from}"}
    note = notes_mod.parse_note(src.read_text(encoding="utf-8"), src)
    related = note.frontmatter.get("related", [])
    if isinstance(related, str):
        related = [related]
    wikilink = f"[[{note_to}]]"
    if wikilink not in related:
        related.append(wikilink)
    note.frontmatter["related"] = sorted(set(related))
    note = _write_note_anywhere(src, note.body, existing_frontmatter=note.frontmatter)
    return {"ok": True, "path": _rel(vault, src), "title": note.title, "related": note.frontmatter.get("related", [])}
