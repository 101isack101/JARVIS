"""
memory/notes.py - CRUD de notas markdown con frontmatter YAML.

Formato compatible con Obsidian:
  - Frontmatter YAML entre --- al inicio
  - Body markdown con [[wikilinks]]
  - tags: lista en frontmatter (Obsidian los reconoce)

Operaciones:
  - read_note(path)   -> Note (frontmatter dict + body str)
  - write_note(path, content, frontmatter)
  - append_to_note(path, section_title, content) - agrega seccion al final
  - add_link(from_path, to_title) - agrega [[to_title]] en frontmatter.related
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .obsidian_vault import ObsidianVault, VaultError

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


@dataclass
class Note:
    path: Path
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    @property
    def title(self) -> str:
        # Primer h1 si hay, sino nombre de archivo
        for line in self.body.splitlines():
            m = re.match(r"^#\s+(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        return self.path.stem

    @property
    def tags(self) -> list[str]:
        t = self.frontmatter.get("tags", [])
        if isinstance(t, str):
            return [t]
        return list(t) if t else []

    def to_markdown(self) -> str:
        fm = dict(self.frontmatter)
        if not fm:
            return self.body
        yaml_str = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_str}\n---\n\n{self.body.lstrip()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_note(text: str, path: Path) -> Note:
    """Separa frontmatter YAML del body. Si no hay frontmatter, body == text."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return Note(path=path, frontmatter={}, body=text)
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return Note(path=path, frontmatter=fm, body=m.group(2))


def read_note(vault: ObsidianVault, path: Path) -> Note:
    """Lee una nota validando que este en scope. Path absoluto."""
    vault.assert_readable(path)
    text = path.read_text(encoding="utf-8")
    return parse_note(text, path)


def write_note(
    vault: ObsidianVault,
    path: Path,
    body: str,
    frontmatter: dict | None = None,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> Note:
    """Crea o sobrescribe una nota. Path debe estar en memory_folder."""
    vault.assert_writable(path)
    fm = dict(frontmatter or {})
    fm.setdefault("created", _now_iso())
    fm["updated"] = _now_iso()
    if tags:
        existing = fm.get("tags", [])
        if isinstance(existing, str):
            existing = [existing]
        fm["tags"] = sorted(set(list(existing) + list(tags)))
    if related:
        existing = fm.get("related", [])
        if isinstance(existing, str):
            existing = [existing]
        fm["related"] = sorted(set(list(existing) + list(related)))
    note = Note(path=path, frontmatter=fm, body=body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note.to_markdown(), encoding="utf-8")
    return note


def append_section(
    vault: ObsidianVault,
    path: Path,
    section_title: str,
    content: str,
) -> Note:
    """Agrega una seccion '## section_title' con contenido al final de la nota."""
    if path.exists():
        note = read_note(vault, path)
    else:
        note = Note(path=path, frontmatter={}, body="")
    timestamp = _now_iso()
    new_section = f"\n\n## {section_title} ({timestamp[:10]})\n\n{content.strip()}\n"
    note.body = note.body.rstrip() + new_section
    return write_note(vault, path, note.body, note.frontmatter)


def add_related(vault: ObsidianVault, path: Path, related_title: str) -> Note:
    """Agrega [[related_title]] a la lista 'related' del frontmatter."""
    note = read_note(vault, path)
    related = note.frontmatter.get("related", [])
    if isinstance(related, str):
        related = [related]
    related = list(related)
    wikilink = f"[[{related_title}]]"
    if wikilink not in related:
        related.append(wikilink)
    note.frontmatter["related"] = sorted(set(related))
    return write_note(vault, path, note.body, note.frontmatter)


def list_notes(
    vault: ObsidianVault, folder: str | None = None, limit: int = 100
) -> list[tuple[Path, str]]:
    """Lista notas con (path, title). Si folder es None, lista todo el scope."""
    files = vault.list_md_files(scope="all")
    if folder:
        target = (vault.vault_path / folder).resolve()
        if not vault._is_inside(target, vault.vault_path):
            raise VaultError(f"Folder fuera del vault: {folder}")
        if not target.exists():
            return []
        files = [f for f in files if vault._is_inside(f, target)]
    out: list[tuple[Path, str]] = []
    for p in files[:limit]:
        try:
            note = parse_note(p.read_text(encoding="utf-8"), p)
            out.append((p, note.title))
        except Exception:
            out.append((p, p.stem))
    return out


# Smoke test
if __name__ == "__main__":
    import sys
    from pathlib import Path as _P

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env")

    v = ObsidianVault()
    print(f"Vault: {v.vault_path}\n")

    # 1) Crear nota
    test_path = v.memory_file("Test Jarvis primera nota")
    note = write_note(
        v, test_path,
        body="# Test note de Jarvis\n\nEsto es un test de la persistencia.",
        tags=["test", "jarvis-internal"],
        related=["[[Aoede voice]]"],
    )
    print(f"[OK] Creada: {test_path.relative_to(v.vault_path)}")
    print(f"     Frontmatter: {note.frontmatter}")

    # 2) Leer y verificar
    read_back = read_note(v, test_path)
    assert "Test note de Jarvis" in read_back.body
    print(f"[OK] Releida, titulo='{read_back.title}', tags={read_back.tags}")

    # 3) Append section
    note = append_section(
        v, test_path,
        section_title="Conversacion 1",
        content="Isaac dijo: 'que tal sona Aoede?'\nJarvis respondio: 'me gusto, sigamos'",
    )
    print(f"[OK] Append seccion. Body length: {len(note.body)} chars")

    # 4) Add related
    add_related(v, test_path, "Voces Gemini Live")
    print(f"[OK] Related agregado")

    # 5) List
    notes = list_notes(v, folder="Jarvis Memory", limit=5)
    print(f"\n[OK] {len(notes)} notas en Jarvis Memory:")
    for p, title in notes:
        print(f"  - {title}  ({p.relative_to(v.vault_path)})")

    # 6) Cleanup
    test_path.unlink()
    print(f"\n[OK] Cleanup hecho. notes.py PASS")
