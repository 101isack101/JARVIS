# JARVIS Fase 1: Continuidad entre sesiones — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que JARVIS recuerde la sesión anterior al arrancar (briefing auto-inyectado) y destile cada sesión en una nota-diario fechada en Obsidian, con durabilidad garantizada incluso ante kill-switch.

**Architecture:** Journal JSONL append-only crash-safe (un turno por línea) + síntesis con Claude reasoner que se dispara en cierre limpio O de forma diferida al próximo arranque si quedó huérfano (durabilidad por reconciliación). La nota se escribe en `Jarvis Memory/sessions/` (dentro de la barrera `assert_writable`) y RAG la indexa automáticamente. Al arrancar, el resumen de la última nota se concatena al system_prompt de Gemini Live.

**Tech Stack:** Python 3.11, JSONL (stdlib `json`), `ClaudeReasoner` (Sonnet 4.6 + caching), `ObsidianVault` + `memory/notes.py`, `security.secret_filter.redact_secrets`, pytest.

---

## Reconciliación con el código existente (leer antes de empezar)

Este plan **modifica comportamiento existente**, no parte de cero. Dos hechos del código actual que el plan respeta:

1. **`Jarvis._save_session_memory()` ya existe** ([jarvis.py:590-617](../../../jarvis.py)) y se llama en `stop()` ([jarvis.py:259](../../../jarvis.py)). Hoy hace un **volcado crudo** de los transcripts acumulados a `Jarvis Memory/` e indexa en RAG. La Tarea 5 **reemplaza su cuerpo** para delegar en los módulos nuevos. No se crea un mecanismo paralelo (DRY).

2. **`ObsidianVault.assert_writable`** ([obsidian_vault.py:100-105](../../../memory/obsidian_vault.py)) **solo permite escribir dentro de `memory_path`** (`Jarvis Memory/`). Por eso las notas de sesión viven en `Jarvis Memory/sessions/` (subcarpeta permitida) y **no** en `05-CLAUDE/context/sessions/` como decía el spec. Beneficio extra: RAG ya indexa esa carpeta, así que las notas también quedan recall-ables a mitad de sesión. **Esta es una desviación intencional del spec**, tomada por seguridad.

## File Structure

| Archivo | Responsabilidad | Cambio |
|---------|-----------------|--------|
| `memory/session_journal.py` | Persistir turnos crudos (JSONL append-only, thread-safe, redacta secretos) | **Nuevo** |
| `memory/session_summary.py` | Sintetizar journal→nota vía Claude; leer la última nota para recall | **Nuevo** |
| `jarvis.py` | Cablear en 3 puntos: `__init__` (recall + síntesis diferida), `_on_turn_complete` (append delta), `_save_session_memory` (reemplazo) | Modificar |
| `.env.example` | Documentar 3 vars nuevas | Modificar |
| `tests/test_session_journal.py` | Tests del journal | **Nuevo** |
| `tests/test_session_summary.py` | Tests de síntesis + recall | **Nuevo** |

**Convenciones del repo a respetar:**
- Tests montan vault temporal con `ObsidianVault(vault_path=tmp_path, memory_folder="Jarvis Memory", read_all=True)` y hacen `sys.path.insert(0, ...)` al inicio (ver [tests/test_memory_notes.py:10-30](../../../tests/test_memory_notes.py)).
- `redact_secrets(text) -> str` ([security/secret_filter.py:32](../../../security/secret_filter.py)).
- `ClaudeReasoner.ask(prompt, context_extra=None, max_tokens=DEFAULT_MAX_TOKENS) -> ReasonerResponse` con `.text` ([claude/reasoner.py:89-144](../../../claude/reasoner.py)).
- `write_note(vault, path, body, frontmatter=None, tags=None, related=None) -> Note` ([memory/notes.py:85](../../../memory/notes.py)).

---

## Task 1: SessionJournal — persistencia JSONL crash-safe

**Files:**
- Create: `memory/session_journal.py`
- Test: `tests/test_session_journal.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests para memory/session_journal.py — journal JSONL append-only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.session_journal import SessionJournal


def test_append_and_read_roundtrip(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn("hola jarvis", "buenas tardes señor")
    journal.append_turn("que hora es", "las tres")
    turns = journal.read_turns()
    assert len(turns) == 2
    assert turns[0]["user"] == "hola jarvis"
    assert turns[0]["jarvis"] == "buenas tardes señor"
    assert "ts" in turns[0]
    assert turns[1]["user"] == "que hora es"


def test_has_pending_and_turn_count(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    assert journal.has_pending() is False
    assert journal.turn_count() == 0
    journal.append_turn("uno", "dos")
    assert journal.has_pending() is True
    assert journal.turn_count() == 1


def test_clear_removes_journal(tmp_path: Path):
    path = tmp_path / "session_journal.jsonl"
    journal = SessionJournal(path)
    journal.append_turn("uno", "dos")
    journal.clear()
    assert journal.has_pending() is False
    assert journal.turn_count() == 0
    assert not path.exists()


def test_secrets_are_redacted_on_write(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn(
        "mi token es ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz",
    )
    raw = (tmp_path / "session_journal.jsonl").read_text(encoding="utf-8")
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in raw
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in raw
    assert "REDACTED" in raw


def test_corrupt_line_is_skipped(tmp_path: Path):
    path = tmp_path / "session_journal.jsonl"
    journal = SessionJournal(path)
    journal.append_turn("buena", "linea")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{esto no es json valido\n")
    journal.append_turn("otra", "buena")
    turns = journal.read_turns()
    assert len(turns) == 2
    assert turns[0]["user"] == "buena"
    assert turns[1]["user"] == "otra"


def test_empty_turn_is_ignored(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn("   ", "")
    assert journal.turn_count() == 0
    assert journal.has_pending() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_journal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.session_journal'`

- [ ] **Step 3: Write minimal implementation**

```python
"""
memory/session_journal.py - Journal JSONL append-only de turnos de conversación.

Responsabilidad única: persistir cada turno (Isaac dijo / Jarvis respondió) de
forma durable e inmediata, para que el resumen de sesión sobreviva a un cierre
sucio (kill-switch Ctrl+Alt+Q hace os._exit(130) y se salta stop()).

Diseño:
  - Un objeto JSON por línea: {"ts": ISO8601, "user": str, "jarvis": str}
  - Append-only: cada turno se escribe y flushea de inmediato (crash-safe)
  - Thread-safe: se llama desde callbacks de Gemini (RLock)
  - Redacta secretos ANTES de escribir a disco (security.secret_filter)
  - Nunca propaga excepciones de I/O a la conversación (fail-safe en el caller)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from security.secret_filter import redact_secrets


class SessionJournal:
    """Journal append-only de turnos, durable y thread-safe."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def append_turn(self, user: str, jarvis: str, ts: str | None = None) -> None:
        """Persiste un turno. Redacta secretos. Ignora turnos vacíos."""
        user = (user or "").strip()
        jarvis = (jarvis or "").strip()
        if not user and not jarvis:
            return
        record = {
            "ts": ts or datetime.now().isoformat(timespec="seconds"),
            "user": redact_secrets(user),
            "jarvis": redact_secrets(jarvis),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def has_pending(self) -> bool:
        """True si el journal existe y tiene al menos un turno legible."""
        return self.turn_count() > 0

    def read_turns(self) -> list[dict]:
        """Devuelve los turnos. Salta líneas corruptas sin romper."""
        with self._lock:
            if not self.path.exists():
                return []
            turns: list[dict] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and ("user" in obj or "jarvis" in obj):
                    turns.append(obj)
            return turns

    def turn_count(self) -> int:
        return len(self.read_turns())

    def clear(self) -> None:
        """Borra el journal (tras síntesis exitosa)."""
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_journal.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add memory/session_journal.py tests/test_session_journal.py
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "feat(memory): SessionJournal JSONL append-only crash-safe con redacción de secretos"
```

---

## Task 2: session_summary — síntesis con Claude → nota fechada

**Files:**
- Create: `memory/session_summary.py`
- Test: `tests/test_session_summary.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests para memory/session_summary.py — síntesis Claude + recall."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.obsidian_vault import ObsidianVault
from memory.session_journal import SessionJournal
from memory.session_summary import (
    SESSIONS_SUBDIR,
    synthesize_and_save,
    load_last_summary,
)


@pytest.fixture
def temp_vault(tmp_path: Path) -> ObsidianVault:
    return ObsidianVault(
        vault_path=tmp_path,
        memory_folder="Jarvis Memory",
        read_all=True,
    )


class StubReasoner:
    """ClaudeReasoner falso: devuelve un .text fijo sin tocar la red."""

    def __init__(self, text: str):
        self._text = text
        self.calls: list[str] = []

    def ask(self, prompt, context_extra=None, max_tokens=1024):
        self.calls.append(prompt)

        class _R:
            text = self._text

        return _R()


def test_synthesize_writes_note_with_frontmatter(temp_vault, tmp_path):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    journal.append_turn("revisemos agentics", "el agente corrió hace 18 min")
    journal.append_turn("algun fallo?", "ninguno, todo verde")
    journal.append_turn("dejemos polymath", "anotado como pendiente")
    reasoner = StubReasoner(
        "## Resumen\n- Revisamos Agentics.\n\n## Pendientes\n- Retomar Polymath.\n\n"
        "## Proyectos tocados\n- [[03-PROJECTS/jarvis]]"
    )

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is not None
    assert path.exists()
    assert SESSIONS_SUBDIR.replace("/", "\\") in str(path) or SESSIONS_SUBDIR in str(path.as_posix())
    text = path.read_text(encoding="utf-8")
    assert "type: session-journal" in text
    assert "session_id: abc12345" in text
    assert "## Resumen" in text
    assert "## Pendientes" in text
    # journal limpiado tras éxito
    assert journal.has_pending() is False


def test_synthesize_skips_when_below_min_turns(temp_vault, tmp_path):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    journal.append_turn("hola", "buenas")
    reasoner = StubReasoner("no debería llamarse")

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is None
    assert reasoner.calls == []  # no se gastó una llamada a Claude
    assert journal.has_pending() is True  # journal intacto


def test_synthesize_keeps_journal_if_write_fails(temp_vault, tmp_path, monkeypatch):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    for i in range(3):
        journal.append_turn(f"u{i}", f"j{i}")
    reasoner = StubReasoner("## Resumen\n- algo")

    import memory.session_summary as mod

    def boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(mod.notes_mod, "write_note", boom)

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is None
    assert journal.has_pending() is True  # NO se limpió → reintenta como huérfano


def test_load_last_summary_picks_most_recent(temp_vault):
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-26_1000_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# Sesión vieja\n\n"
        "## Resumen\n- viejo\n\n## Pendientes\n- nada\n",
        encoding="utf-8",
    )
    (base / "2026-05-28_1500_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# Sesión nueva\n\n"
        "## Resumen\n- nuevo\n\n## Pendientes\n- retomar X\n",
        encoding="utf-8",
    )

    out = load_last_summary(temp_vault, max_chars=1000)
    assert out is not None
    assert "nuevo" in out
    assert "retomar X" in out
    assert "viejo" not in out


def test_load_last_summary_returns_none_when_empty(temp_vault):
    assert load_last_summary(temp_vault, max_chars=1000) is None


def test_load_last_summary_respects_max_chars(temp_vault):
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-28_1500_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n## Resumen\n- "
        + ("x" * 5000)
        + "\n",
        encoding="utf-8",
    )
    out = load_last_summary(temp_vault, max_chars=200)
    assert out is not None
    assert len(out) <= 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.session_summary'`

- [ ] **Step 3: Write minimal implementation**

```python
"""
memory/session_summary.py - Síntesis del journal en nota fechada + recall.

Responsabilidad única: convertir un SessionJournal en una nota-diario destilada
por Claude, y leer la última nota para inyectarla al arranque.

Las notas viven en `Jarvis Memory/sessions/` (dentro de la barrera assert_writable
del vault). RAG las indexa automáticamente, así que también son recall-ables.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault
from memory.session_journal import SessionJournal

# Subcarpeta DENTRO de memory_folder (Jarvis Memory) → respeta assert_writable.
SESSIONS_SUBDIR = "sessions"

_SYNTHESIS_INSTRUCTIONS = (
    "Eres el cronista de JARVIS. Te paso el transcript crudo de una sesión de voz "
    "entre Isaac y JARVIS. Destílalo en una nota breve en español con EXACTAMENTE "
    "estas tres secciones markdown y nada más:\n\n"
    "## Resumen\n- 3 a 5 bullets de lo que se conversó o hizo.\n\n"
    "## Pendientes\n- Items accionables que quedaron abiertos (si no hay, escribe "
    "'- (ninguno)').\n\n"
    "## Proyectos tocados\n- Wikilinks tipo [[03-PROJECTS/nombre]] de proyectos "
    "mencionados (si ninguno, escribe '- (ninguno)').\n\n"
    "No inventes. Sé conciso. No agregues encabezado de título ni frontmatter."
)


def _format_transcript(turns: list[dict]) -> str:
    lines: list[str] = []
    for t in turns:
        user = (t.get("user") or "").strip()
        jarvis = (t.get("jarvis") or "").strip()
        if user:
            lines.append(f"Isaac: {user}")
        if jarvis:
            lines.append(f"JARVIS: {jarvis}")
    return "\n".join(lines)


def synthesize_and_save(
    journal: SessionJournal,
    reasoner,
    vault: ObsidianVault,
    min_turns: int,
    session_id: str,
) -> Path | None:
    """Sintetiza el journal en una nota fechada. Devuelve el path o None.

    - Si turn_count < min_turns → None (sesión trivial, sin nota, sin gastar Claude).
    - Si la escritura falla → NO limpia el journal (reintenta como huérfano).
    - Solo limpia el journal tras escribir con éxito.
    """
    turns = journal.read_turns()
    if len(turns) < min_turns:
        return None
    if reasoner is None:
        return None

    transcript = _format_transcript(turns)
    try:
        resp = reasoner.ask(
            _SYNTHESIS_INSTRUCTIONS,
            context_extra="TRANSCRIPT:\n" + transcript,
            max_tokens=600,
        )
        synthesized = (resp.text or "").strip()
    except Exception:
        return None  # Claude caído → journal queda como huérfano

    if not synthesized:
        return None

    now = datetime.now()
    fname = f"{now.strftime('%Y-%m-%d_%H%M')}_sesion.md"
    path = (vault.memory_path / SESSIONS_SUBDIR / fname)

    frontmatter = {
        "type": "session-journal",
        "project": "[[03-PROJECTS/jarvis]]",
        "date": now.strftime("%Y-%m-%d"),
        "session_id": session_id,
        "generated_by": "claude-sonnet-4-6",
    }
    body = f"# Sesión {now.strftime('%Y-%m-%d %H:%M')}\n\n{synthesized}\n"

    try:
        notes_mod.write_note(
            vault,
            path,
            body=body,
            frontmatter=frontmatter,
            tags=["jarvis-session", "session-journal"],
        )
    except Exception:
        return None  # NO limpiar journal: se reintenta al próximo arranque

    journal.clear()
    return path


def _sessions_dir(vault: ObsidianVault) -> Path:
    return vault.memory_path / SESSIONS_SUBDIR


def _extract_recall_sections(text: str) -> str:
    """Devuelve Resumen + Pendientes del cuerpo (omite frontmatter y Proyectos)."""
    # Quitar frontmatter YAML si existe.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    keep: list[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            capture = heading in ("resumen", "pendientes")
        if capture:
            keep.append(line)
    return "\n".join(keep).strip()


def load_last_summary(vault: ObsidianVault, max_chars: int) -> str | None:
    """Lee la nota de sesión más reciente. Devuelve Resumen + Pendientes o None.

    Ordena por nombre de archivo descendente: el naming YYYY-MM-DD_HHMM es
    cronológico, así que el primero es el más nuevo.
    """
    base = _sessions_dir(vault)
    if not base.exists():
        return None
    files = sorted(base.glob("*_sesion.md"), reverse=True)
    if not files:
        return None
    try:
        text = files[0].read_text(encoding="utf-8")
    except OSError:
        return None
    sections = _extract_recall_sections(text)
    if not sections:
        return None
    return sections[:max_chars]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_summary.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add memory/session_summary.py tests/test_session_summary.py
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "feat(memory): session_summary - síntesis Claude del journal + recall de última sesión"
```

---

## Task 3: Construir el recall block (helper puro, testeable)

**Files:**
- Modify: `memory/session_summary.py`
- Test: `tests/test_session_summary.py`

Razón: el bloque que se concatena al system_prompt debe ser una función pura
para poder testearlo sin levantar Gemini.

- [ ] **Step 1: Write the failing test (añadir al final de tests/test_session_summary.py)**

```python
def test_build_recall_block_wraps_with_header():
    from memory.session_summary import build_recall_block

    block = build_recall_block("## Resumen\n- algo\n\n## Pendientes\n- retomar X")
    assert "CONTEXTO DE SESIÓN ANTERIOR" in block
    assert "retomar X" in block


def test_build_recall_block_empty_returns_empty_string():
    from memory.session_summary import build_recall_block

    assert build_recall_block(None) == ""
    assert build_recall_block("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_summary.py::test_build_recall_block_wraps_with_header -v`
Expected: FAIL with `ImportError: cannot import name 'build_recall_block'`

- [ ] **Step 3: Write minimal implementation (añadir a memory/session_summary.py)**

```python
def build_recall_block(summary: str | None) -> str:
    """Envuelve el resumen recuperado en un bloque para el system_prompt.

    Devuelve "" si no hay nada que inyectar (degradación elegante).
    """
    if not summary or not summary.strip():
        return ""
    bar = "═" * 11
    return (
        f"{bar} CONTEXTO DE SESIÓN ANTERIOR {bar}\n"
        f"{summary.strip()}\n"
        f"{bar}{bar}\n"
        "(Usa esto solo si Isaac retoma algo de la sesión previa; "
        "no lo recites sin que venga al caso.)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/test_session_summary.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add memory/session_summary.py tests/test_session_summary.py
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "feat(memory): build_recall_block para inyectar contexto de sesión previa"
```

---

## Task 4: Cablear journal + recall en `jarvis.py __init__`

**Files:**
- Modify: `jarvis.py` (imports, `__init__` config + recall injection)

Sin test unitario nuevo (es wiring de orquestación; se valida con la suite
completa + E2E manual). El riesgo se acota porque todo va envuelto en try/except.

- [ ] **Step 1: Añadir imports**

En [jarvis.py:44-46](../../../jarvis.py), junto a los otros `from memory ...`, añadir:

```python
from memory.session_journal import SessionJournal
from memory.session_summary import (
    synthesize_and_save,
    load_last_summary,
    build_recall_block,
)
```

- [ ] **Step 2: Leer config de .env y crear el journal en `__init__`**

En [jarvis.py:107-114](../../../jarvis.py), tras `self._output_transcript: list[str] = []`, añadir:

```python
        # Fase 1 — Continuidad entre sesiones.
        self.session_continuity_enabled = (
            os.environ.get("JARVIS_SESSION_JOURNAL_ENABLED", "true").lower() == "true"
        )
        self.session_min_turns = int(os.environ.get("JARVIS_SESSION_MIN_TURNS", "3"))
        self.session_recall_max_chars = int(
            os.environ.get("JARVIS_SESSION_RECALL_MAX_CHARS", "1000")
        )
        self.session_journal = SessionJournal(ROOT / "data" / "session_journal.jsonl")
        self._session_saved = False  # guard idempotente para síntesis en stop()
        # Índice del último volcado a journal por turno (delta, no acumulado).
        self._journal_input_idx = 0
        self._journal_output_idx = 0
```

- [ ] **Step 3: Reconciliar huérfano y construir el recall block ANTES del system_prompt**

El system_prompt se fija al construir `JarvisSession` en [jarvis.py:214-227](../../../jarvis.py). La inyección debe ocurrir antes. Tras la pre-carga de RAG ([jarvis.py:148](../../../jarvis.py), línea `log.info("Modelo pre-cargado.")`), añadir:

```python
        # Fase 1 — Continuidad: reconciliar journal huérfano (síntesis diferida)
        # y recuperar el resumen de la última sesión para inyectarlo al prompt.
        recall_block = ""
        if self.session_continuity_enabled:
            try:
                if self.session_journal.has_pending():
                    log.info("Journal huérfano detectado; sintetizando sesión previa...")
                    p = synthesize_and_save(
                        self.session_journal,
                        self.reasoner,
                        self.vault,
                        min_turns=self.session_min_turns,
                        session_id=self.session_id,
                    )
                    if p is not None:
                        try:
                            self.rag.index_file(p)
                            self.rag.save()
                        except Exception as exc:
                            log.warning(f"[WARN] no se indexó nota huérfana: {exc}")
            except Exception as exc:
                log.warning(f"[WARN] síntesis diferida falló: {exc}")
            try:
                prev = load_last_summary(self.vault, self.session_recall_max_chars)
                recall_block = build_recall_block(prev)
                if recall_block:
                    log.info("Contexto de sesión anterior inyectado al system_prompt.")
            except Exception as exc:
                log.warning(f"[WARN] recall de sesión previa falló: {exc}")
```

- [ ] **Step 4: Concatenar el recall block al system_prompt**

Modificar [jarvis.py:218](../../../jarvis.py):

```python
                system_prompt=(
                    SYSTEM_PROMPT
                    + "\n\n"
                    + preferences_prompt_block(self.preferences)
                    + (("\n\n" + recall_block) if recall_block else "")
                ),
```

- [ ] **Step 5: Verificar que la suite completa sigue verde + smoke import**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -c "import jarvis"`
Expected: sin errores de import.

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/ -q`
Expected: toda la suite previa + los nuevos tests en verde (sin regresión).

- [ ] **Step 6: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add jarvis.py
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "feat(jarvis): cablear journal + síntesis diferida + recall al arranque"
```

---

## Task 5: Append del delta por turno + reemplazo de `_save_session_memory`

**Files:**
- Modify: `jarvis.py` (`_on_turn_complete`, `_save_session_memory`)

- [ ] **Step 1: Persistir el delta del turno en `_on_turn_complete`**

`_input_transcript` / `_output_transcript` **acumulan toda la sesión**, así que
hay que volcar solo lo nuevo desde el último turno usando los índices creados en
la Task 4. En [jarvis.py:475-486](../../../jarvis.py), dentro de `_on_turn_complete`,
tras `self._tk(lambda: self.overlay.append_output("\n"))`, añadir:

```python
        # Fase 1 — Continuidad: persistir el DELTA de este turno al journal.
        if self.session_continuity_enabled:
            try:
                user_delta = " ".join(
                    self._input_transcript[self._journal_input_idx:]
                ).strip()
                jarvis_delta = "".join(
                    self._output_transcript[self._journal_output_idx:]
                ).strip()
                self._journal_input_idx = len(self._input_transcript)
                self._journal_output_idx = len(self._output_transcript)
                if user_delta or jarvis_delta:
                    self.session_journal.append_turn(user_delta, jarvis_delta)
            except Exception as exc:
                self._log(f"[WARN] journal append falló: {exc}")
```

- [ ] **Step 2: Reemplazar el cuerpo de `_save_session_memory`**

Reemplazar TODO el método actual ([jarvis.py:590-617](../../../jarvis.py)) por la
versión que delega en la síntesis con Claude (idempotente vía `_session_saved`):

```python
    def _save_session_memory(self) -> None:
        """Cierre limpio: sintetiza el journal en una nota-diario fechada.

        Idempotente (corre una sola vez por proceso). Si la continuidad está
        desactivada o no hay reasoner, no hace nada. Si la síntesis falla, el
        journal queda intacto y se reintenta como huérfano al próximo arranque.
        """
        if self._session_saved or not self.session_continuity_enabled:
            return
        self._session_saved = True
        p = synthesize_and_save(
            self.session_journal,
            self.reasoner,
            self.vault,
            min_turns=self.session_min_turns,
            session_id=self.session_id,
        )
        if p is None:
            return
        try:
            self.rag.index_file(p)
            self.rag.save()
        except Exception as exc:
            self._log(f"[WARN] no se indexó nota de sesión: {exc}")
        self._log(f"nota de sesión guardada: {p.relative_to(self.vault.vault_path)}")
```

Nota: `stop()` ya llama `_save_session_memory()` dentro de try/except en
[jarvis.py:259-260](../../../jarvis.py), así que no hay que tocar `stop()`.

- [ ] **Step 3: Verificar suite + smoke import**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -c "import jarvis"`
Expected: sin errores.

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/ -q`
Expected: todo verde, sin regresión.

- [ ] **Step 4: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add jarvis.py
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "feat(jarvis): append delta por turno + _save_session_memory delega en síntesis Claude"
```

---

## Task 6: Documentar config en `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Añadir las 3 variables**

Al final de `.env.example`, añadir:

```bash
# --- Fase 1: Continuidad entre sesiones ---
# Master switch de toda la feature (journal + síntesis + recall).
JARVIS_SESSION_JOURNAL_ENABLED=true
# Mínimo de turnos para generar nota de sesión (evita ruido en sesiones triviales).
JARVIS_SESSION_MIN_TURNS=3
# Cap de caracteres del bloque de contexto inyectado al system_prompt al arrancar.
JARVIS_SESSION_RECALL_MAX_CHARS=1000
```

- [ ] **Step 2: Commit**

```bash
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" add .env.example
git -C "c:/Users/Isaac/Desktop/PROYECTOS/JARVIS" commit -m "docs(env): documentar variables de continuidad de sesión Fase 1"
```

---

## Task 7: Verificación final + E2E manual

**Files:** ninguno (solo verificación).

- [ ] **Step 1: Suite completa verde**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python -m pytest tests/ -q`
Expected: todos los tests previos + 14 nuevos (6 journal + 8 summary) en verde.

- [ ] **Step 2: Healthcheck**

Run: `cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS && python jarvis_health.py`
Expected: verde (no se tocó código de healthcheck).

- [ ] **Step 3: E2E manual (lo ejecuta Isaac, no el agente)**

1. Correr JARVIS, sostener una conversación corta de ≥3 turnos, cerrar limpio
   (cerrar overlay). → Verificar que aparece
   `Jarvis Memory/sessions/<fecha>_sesion.md` en el vault con Resumen +
   Pendientes + Proyectos tocados.
2. Reabrir JARVIS y preguntar algo como "¿en qué quedamos la última vez?".
   → JARVIS debe referenciar la sesión anterior.
3. Probar kill-switch (Ctrl+Alt+Q) a mitad de una sesión de ≥3 turnos. Reabrir.
   → En el log de arranque debe verse "Journal huérfano detectado; sintetizando
   sesión previa..." y debe aparecer la nota.
4. Verificar en el grafo de Obsidian que la nota se conecta a `[[03-PROJECTS/jarvis]]`.

- [ ] **Step 4: Revisar log de la primera sesión real**

`data/jarvis.log` no debe tener errores nuevos. Confirmar que no hay warning de
prompt demasiado largo (el recall añade ≤1000 chars al prompt de ~16KB).

---

## Verificación de done (resumen del spec)

- [ ] `pytest` verde (suite existente + 2 archivos de test nuevos).
- [ ] Smoke import de los módulos nuevos (`import jarvis` OK).
- [ ] E2E manual: los 3 escenarios pasan.
- [ ] Nota de sesión aparece en el grafo de Obsidian conectada a `[[03-PROJECTS/jarvis]]`.
- [ ] `redact_secrets()` aplicado antes de persistir (verificado en `test_secrets_are_redacted_on_write`).

## Lo que NO se toca (zonas tabú)

- `claude/reasoner.py` — se reutiliza `ask()`; no se cambia `max_tokens` global ni el modelo.
- `gemini/session.py` — WebSocket, reconexión, tools intactos.
- `runtime_modes.py`, security (HITL, kill-switch, secret_filter), concurrencia — sin cambios estructurales.
- `assert_writable` — NO se relaja; por eso las notas van a `Jarvis Memory/sessions/`.
