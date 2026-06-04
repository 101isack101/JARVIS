# Fase 3 (A) — Motor de Proactividad Determinista — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el motor determinista de proactividad de JARVIS en un módulo aislado `proactivity/` que lee del vault (Project Memory Cards + notas de sesión) y del RAG, deriva estado de proyectos, detecta oportunidades (pendientes stale, proyectos sin tocar, open loops, conexiones cross-proyecto) y las prioriza con dedup/cooldown/persistencia anti-spam — todo sin tocar runtime ni LLM.

**Architecture:** Cuatro submódulos con responsabilidad única encadenados como pipeline: `project_state.py` (snapshot por proyecto) → `signals.py` (detectores puros → `Signal`) → `opportunity_queue.py` (scoring + dedup + cooldown + persistencia en `data/proactivity_state.json`) → `briefing.py` (bloque estructurado de arranque). La configuración vive en `ProactivityConfig` (lee de env con defaults). Determinista y fail-safe: cada fuente se envuelve en try/except y un fallo nunca propaga.

**Tech Stack:** Python 3.11, pytest, dataclasses, stdlib (`json`, `hashlib`, `datetime`). Sin dependencias nuevas. Heurística de staleness por fechas; sin ML.

**Convenciones del repo:**
- Tests se corren con: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest <ruta> -v`
- Patrón de test: vault temporal `ObsidianVault(tmp_path, read_all=True)` + `FakeRAG` (mismo patrón que `tests/test_context_assembler.py`).
- Firmas confirmadas en el repo:
  - `triage.PROJECT_ALIASES: dict[str, tuple[str,...]]`, `triage.detect_project(text) -> str|None`, `triage.project_card_path(vault, project) -> Path`.
  - Card real: secciones `## Objective / Current State / Facts / Decisions / Pending / Procedures / Preferences / Learning Notes / Risks / Sources`; bullets `- YYYY-MM-DD [importance/confidence] texto (source: [[title]])`; placeholder de sección vacía `- (pending)`; frontmatter incluye `importance`, `confidence`.
  - `notes.read_note(vault, path) -> Note` con `.body`, `.frontmatter`, `.tags`, `.title`.
  - Sesiones en `vault.memory_path / "sessions"`, archivos `YYYY-MM-DD_HHMM_sesion.md`; el frontmatter `project` está hardcodeado a `[[03-PROJECTS/jarvis]]` (NO fiable); la señal real de proyectos tocados es la sección de cuerpo `## Proyectos tocados` con wikilinks `[[03-PROJECTS/nombre]]`. `session_summary.SESSIONS_SUBDIR == "sessions"`.
  - RAG: `rag.search(query, top_k) -> list[SearchResult]` con `r.score: float` y `r.chunk` con `.title`, `.rel_path`, `.text`.

---

## File Structure

| Archivo | Responsabilidad |
|---------|-----------------|
| `proactivity/__init__.py` | **nuevo** — marca el paquete; reexporta `ProactivityConfig` |
| `proactivity/config.py` | **nuevo** — `ProactivityConfig` (dataclass) + `from_env` |
| `proactivity/project_state.py` | **nuevo** — `ProjectState` + parseo de cards + `build_project_states` |
| `proactivity/signals.py` | **nuevo** — `Signal` + 5 detectores deterministas |
| `proactivity/opportunity_queue.py` | **nuevo** — `Opportunity` + scoring + `OpportunityQueue` (dedup/cooldown/persistencia) |
| `proactivity/briefing.py` | **nuevo** — `render_briefing` (bloque estructurado de arranque) |
| `tests/test_proactivity_config.py` | **nuevo** |
| `tests/test_proactivity_project_state.py` | **nuevo** |
| `tests/test_proactivity_signals.py` | **nuevo** |
| `tests/test_proactivity_queue.py` | **nuevo** |
| `tests/test_proactivity_briefing.py` | **nuevo** |

> El cableado en `jarvis.py`, la tool `jarvis_proactive_check`, `system_prompt.py` y `.env.example` se cubren en el **Plan B (runtime)**, que depende de este Plan A.

---

## Task 1: `ProactivityConfig` desde entorno

**Files:**
- Create: `proactivity/__init__.py`
- Create: `proactivity/config.py`
- Test: `tests/test_proactivity_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_config.py
from proactivity.config import ProactivityConfig


def test_defaults_when_env_missing():
    cfg = ProactivityConfig.from_env({})
    assert cfg.enabled is True
    assert cfg.stale_pending_days == 7
    assert cfg.stale_project_days == 14
    assert cfg.max_per_session == 3
    assert cfg.cooldown_days == 7
    assert cfg.briefing_top_k == 3
    assert cfg.min_score == 0.35


def test_reads_overrides_from_env():
    env = {
        "JARVIS_PROACTIVITY_ENABLED": "false",
        "JARVIS_PROACTIVITY_STALE_PENDING_DAYS": "3",
        "JARVIS_PROACTIVITY_STALE_PROJECT_DAYS": "21",
        "JARVIS_PROACTIVITY_MAX_PER_SESSION": "1",
        "JARVIS_PROACTIVITY_COOLDOWN_DAYS": "10",
        "JARVIS_PROACTIVITY_BRIEFING_TOP_K": "5",
        "JARVIS_PROACTIVITY_MIN_SCORE": "0.6",
    }
    cfg = ProactivityConfig.from_env(env)
    assert cfg.enabled is False
    assert cfg.stale_pending_days == 3
    assert cfg.stale_project_days == 21
    assert cfg.max_per_session == 1
    assert cfg.cooldown_days == 10
    assert cfg.briefing_top_k == 5
    assert cfg.min_score == 0.6


def test_malformed_values_fall_back_to_default():
    cfg = ProactivityConfig.from_env({"JARVIS_PROACTIVITY_STALE_PENDING_DAYS": "abc"})
    assert cfg.stale_pending_days == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_config.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/__init__.py
"""Motor de proactividad determinista de JARVIS (Fase 3)."""

from .config import ProactivityConfig

__all__ = ["ProactivityConfig"]
```

```python
# proactivity/config.py
"""Configuración del motor de proactividad, leída de entorno con defaults seguros."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProactivityConfig:
    enabled: bool = True
    stale_pending_days: int = 7
    stale_project_days: int = 14
    max_per_session: int = 3
    cooldown_days: int = 7
    briefing_top_k: int = 3
    min_score: float = 0.35

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProactivityConfig":
        env = env if env is not None else os.environ
        d = cls()  # defaults

        def _bool(key: str, default: bool) -> bool:
            raw = env.get(key)
            if raw is None:
                return default
            return raw.strip().lower() in _TRUE

        def _int(key: str, default: int) -> int:
            try:
                return int(str(env.get(key, default)).strip())
            except (ValueError, TypeError):
                return default

        def _float(key: str, default: float) -> float:
            try:
                return float(str(env.get(key, default)).strip())
            except (ValueError, TypeError):
                return default

        return cls(
            enabled=_bool("JARVIS_PROACTIVITY_ENABLED", d.enabled),
            stale_pending_days=_int("JARVIS_PROACTIVITY_STALE_PENDING_DAYS", d.stale_pending_days),
            stale_project_days=_int("JARVIS_PROACTIVITY_STALE_PROJECT_DAYS", d.stale_project_days),
            max_per_session=_int("JARVIS_PROACTIVITY_MAX_PER_SESSION", d.max_per_session),
            cooldown_days=_int("JARVIS_PROACTIVITY_COOLDOWN_DAYS", d.cooldown_days),
            briefing_top_k=_int("JARVIS_PROACTIVITY_BRIEFING_TOP_K", d.briefing_top_k),
            min_score=_float("JARVIS_PROACTIVITY_MIN_SCORE", d.min_score),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/__init__.py proactivity/config.py tests/test_proactivity_config.py
git commit -m "feat(proactivity): ProactivityConfig desde entorno con defaults"
```

---

## Task 2: Parseo de secciones de una Project Memory Card

**Files:**
- Create: `proactivity/project_state.py`
- Test: `tests/test_proactivity_project_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_project_state.py
from proactivity.project_state import parse_card_sections, section_bullets


CARD = (
    "# Polymath IDE - Memory Card\n\n"
    "## Current State\n\n"
    "- 2026-05-01 [normal/high] editor Monaco integrado (source: [[s1]])\n\n"
    "## Decisions\n\n"
    "- 2026-05-02 [high/high] usar WebSocket para el agente (source: [[s2]])\n\n"
    "## Pending\n\n"
    "- 2026-05-03 [high/high] conectar el agente al server (source: [[s3]])\n"
    "- 2026-05-04 [normal/medium] escribir tests e2e (source: [[s4]])\n\n"
    "## Procedures\n\n"
    "- (pending)\n"
)


def test_parse_card_sections_splits_by_heading():
    sections = parse_card_sections(CARD)
    assert "Pending" in sections
    assert "Decisions" in sections
    assert "Current State" in sections
    # el placeholder de sección vacía se descarta
    assert sections["Procedures"] == []


def test_section_bullets_strips_marker_and_ignores_placeholder():
    sections = parse_card_sections(CARD)
    pend = section_bullets(sections, "Pending")
    assert len(pend) == 2
    assert pend[0].startswith("2026-05-03")
    assert "conectar el agente al server" in pend[0]
    assert section_bullets(sections, "Procedures") == []
    assert section_bullets(sections, "NoExiste") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_project_state.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.project_state'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/project_state.py
"""Snapshot determinista de estado por proyecto (Fase 3).

Deriva, por proyecto conocido, un ProjectState a partir de:
- la Project Memory Card (secciones Pending / Decisions / Current State),
- las notas de sesión (última fecha que mencionó el proyecto).

Solo lee archivos; sin LLM, sin embeddings. Fail-safe por proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

PLACEHOLDER = "- (pending)"


def parse_card_sections(body: str) -> dict[str, list[str]]:
    """Devuelve {nombre_seccion: [linea_bullet, ...]} para cada `## Heading`.

    Las líneas de bullet conservan su texto pero sin el marcador inicial `- `.
    El placeholder de sección vacía `- (pending)` se descarta.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not stripped or stripped == PLACEHOLDER:
            continue
        if stripped.startswith("- "):
            sections[current].append(stripped[2:].strip())
    return sections


def section_bullets(sections: dict[str, list[str]], name: str) -> list[str]:
    return list(sections.get(name, []))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_project_state.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/project_state.py tests/test_proactivity_project_state.py
git commit -m "feat(proactivity): parseo de secciones de Project Memory Card"
```

---

## Task 3: `build_project_states` — staleness desde sesiones

**Files:**
- Modify: `proactivity/project_state.py`
- Test: `tests/test_proactivity_project_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_project_state.py  (añadir)
from datetime import date

from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from proactivity.project_state import ProjectState, build_project_states


def _write_card(vault, project, body, frontmatter=None):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "project-memory-card", "importance": "high", "confidence": "medium"}
    fm.update(frontmatter or {})
    notes_mod.write_note(vault, path, body=body, frontmatter=fm)
    return path


def _write_session(vault, name, body):
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(body, encoding="utf-8")


def test_build_states_derives_pendings_and_staleness(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault,
        "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n\n"
        "## Decisions\n\n- 2026-05-02 [high/high] usar WebSocket\n",
        frontmatter={"importance": "high", "confidence": "high"},
    )
    # Sesión que tocó Polymath el 2026-05-20
    _write_session(
        vault,
        "2026-05-20_2100_sesion.md",
        "# Sesion\n\n## Resumen\n- algo\n\n## Pendientes\n- (ninguno)\n\n"
        "## Proyectos tocados\n- [[03-PROJECTS/polymath]]\n",
    )

    states = build_project_states(vault, today=date(2026, 5, 30))
    by_name = {s.project: s for s in states}

    assert "Polymath IDE" in by_name
    poly = by_name["Polymath IDE"]
    assert isinstance(poly, ProjectState)
    assert poly.last_touched == date(2026, 5, 20)
    assert poly.staleness_days == 10
    assert any("conectar el agente" in p for p in poly.open_pendings)
    assert any("WebSocket" in d for d in poly.open_decisions)
    assert poly.importance == "high"


def test_project_without_card_or_session_is_absent(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n\n- algo\n")

    states = build_project_states(vault, today=date(2026, 5, 30))
    names = {s.project for s in states}
    assert "Polymath IDE" in names
    assert "Agentics_Code_Team" not in names  # sin card ni sesión
    # sin sesión: staleness desconocido
    assert {s.project: s for s in states}["Polymath IDE"].last_touched is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_project_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProjectState'` / `build_project_states`

- [ ] **Step 3: Write minimal implementation**

Añade al final de `proactivity/project_state.py`:

```python
# proactivity/project_state.py  (añadir)
from datetime import datetime
from pathlib import Path

from memory import notes as notes_mod
from memory import triage as triage_mod
from memory.obsidian_vault import ObsidianVault

SESSIONS_SUBDIR = "sessions"


@dataclass(frozen=True)
class ProjectState:
    project: str
    last_touched: date | None
    staleness_days: int | None
    open_pendings: list[str]
    open_decisions: list[str]
    current_state: list[str]
    importance: str
    confidence: str


def _extract_section_text(body: str, heading: str) -> str:
    """Devuelve el texto crudo bajo `## heading` hasta el siguiente `## `."""
    out: list[str] = []
    capture = False
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            capture = line[3:].strip().lower() == heading.lower()
            continue
        if capture:
            out.append(line)
    return "\n".join(out)


def _session_files(vault: ObsidianVault) -> list[Path]:
    base = vault.memory_path / SESSIONS_SUBDIR
    if not base.exists():
        return []
    return sorted(base.glob("*_sesion.md"), reverse=True)


def _session_date(path: Path) -> date | None:
    try:
        return datetime.strptime(path.name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _last_touched_map(vault: ObsidianVault) -> dict[str, date]:
    """Para cada proyecto conocido, la fecha de la sesión más reciente que lo
    mencionó en su sección `## Proyectos tocados`. Determinista por aliases."""
    touched: dict[str, date] = {}
    for path in _session_files(vault):
        sdate = _session_date(path)
        if sdate is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        haystack = _extract_section_text(text, "Proyectos tocados").lower()
        if not haystack.strip():
            continue
        for project, aliases in triage_mod.PROJECT_ALIASES.items():
            if any(alias.lower() in haystack for alias in aliases):
                if project not in touched or sdate > touched[project]:
                    touched[project] = sdate
    return touched


def _load_card(vault: ObsidianVault, project: str):
    try:
        path = triage_mod.project_card_path(vault, project)
    except Exception:
        return None
    if not path.exists():
        return None
    try:
        return notes_mod.read_note(vault, path)
    except Exception:
        return None


def build_project_states(
    vault: ObsidianVault, *, today: date | None = None
) -> list[ProjectState]:
    today = today or date.today()
    touched = _last_touched_map(vault)

    states: list[ProjectState] = []
    for project in triage_mod.PROJECT_ALIASES:
        note = _load_card(vault, project)
        last = touched.get(project)
        if note is None and last is None:
            continue  # ni card ni sesión: el proyecto no existe para el motor

        if note is not None:
            sections = parse_card_sections(note.body or "")
            importance = str(note.frontmatter.get("importance", "normal"))
            confidence = str(note.frontmatter.get("confidence", "medium"))
        else:
            sections = {}
            importance, confidence = "normal", "medium"

        staleness = (today - last).days if last is not None else None
        states.append(
            ProjectState(
                project=project,
                last_touched=last,
                staleness_days=staleness,
                open_pendings=section_bullets(sections, "Pending"),
                open_decisions=section_bullets(sections, "Decisions"),
                current_state=section_bullets(sections, "Current State"),
                importance=importance,
                confidence=confidence,
            )
        )
    return states
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_project_state.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/project_state.py tests/test_proactivity_project_state.py
git commit -m "feat(proactivity): build_project_states con staleness desde sesiones"
```

---

## Task 4: `Signal` + detector `stale_pending`

**Files:**
- Create: `proactivity/signals.py`
- Test: `tests/test_proactivity_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_signals.py
from datetime import date

from proactivity.config import ProactivityConfig
from proactivity.project_state import ProjectState
from proactivity.signals import Signal, detect_startup_signals


def _state(project="Polymath IDE", **kw):
    base = dict(
        project=project,
        last_touched=date(2026, 5, 20),
        staleness_days=10,
        open_pendings=["2026-05-03 [high/high] conectar el agente al server"],
        open_decisions=[],
        current_state=[],
        importance="high",
        confidence="high",
    )
    base.update(kw)
    return ProjectState(**base)


def test_stale_pending_fires_when_project_stale():
    cfg = ProactivityConfig(stale_pending_days=7)
    signals = detect_startup_signals([_state()], cfg)
    kinds = {s.kind for s in signals}
    assert "stale_pending" in kinds
    sp = next(s for s in signals if s.kind == "stale_pending")
    assert sp.project == "Polymath IDE"
    assert "conectar el agente al server" in sp.payload["pending"]
    assert sp.base_priority > 0


def test_stale_pending_does_not_fire_when_recent():
    cfg = ProactivityConfig(stale_pending_days=7)
    fresh = _state(staleness_days=2)
    signals = detect_startup_signals([fresh], cfg)
    assert all(s.kind != "stale_pending" for s in signals)


def test_no_pendings_no_stale_pending_signal():
    cfg = ProactivityConfig(stale_pending_days=7)
    empty = _state(open_pendings=[])
    signals = detect_startup_signals([empty], cfg)
    assert all(s.kind != "stale_pending" for s in signals)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.signals'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/signals.py
"""Detectores deterministas: convierten ProjectState (+ contexto del turno) en Signals.

Reglas puras, sin estado y sin LLM. Las señales de ARRANQUE no necesitan
contexto conversacional; las CONTEXTUALES reciben el texto del turno y el RAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ProactivityConfig
from .project_state import ProjectState

_IMPORTANCE_RANK = {"low": 0, "normal": 1, "high": 2}


@dataclass(frozen=True)
class Signal:
    kind: str          # stale_pending | stale_project | open_loop | cross_project | ctx_pending
    project: str
    payload: dict
    base_priority: float
    evidence: list[str] = field(default_factory=list)


def _stale_pending(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        if st.staleness_days is None or st.staleness_days < cfg.stale_pending_days:
            continue
        if not st.open_pendings:
            continue
        out.append(
            Signal(
                kind="stale_pending",
                project=st.project,
                payload={"pending": st.open_pendings[0], "days": st.staleness_days},
                base_priority=0.6,
                evidence=[f"card:{st.project}"],
            )
        )
    return out


def detect_startup_signals(
    states: list[ProjectState], cfg: ProactivityConfig
) -> list[Signal]:
    """Señales que no necesitan contexto conversacional (briefing de arranque)."""
    signals: list[Signal] = []
    signals.extend(_stale_pending(states, cfg))
    return signals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/signals.py tests/test_proactivity_signals.py
git commit -m "feat(proactivity): Signal + detector stale_pending"
```

---

## Task 5: Detectores `stale_project` y `open_loop`

**Files:**
- Modify: `proactivity/signals.py`
- Test: `tests/test_proactivity_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_signals.py  (añadir)
def test_stale_project_fires_for_important_untouched_project():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(staleness_days=20, importance="high", open_pendings=[])
    signals = detect_startup_signals([st], cfg)
    assert any(s.kind == "stale_project" for s in signals)


def test_stale_project_ignores_low_importance():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(staleness_days=20, importance="low", open_pendings=[])
    signals = detect_startup_signals([st], cfg)
    assert all(s.kind != "stale_project" for s in signals)


def test_open_loop_fires_when_decisions_without_recent_progress():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(
        staleness_days=20,
        open_pendings=[],
        open_decisions=["2026-05-02 [high/high] usar WebSocket para el agente"],
    )
    signals = detect_startup_signals([st], cfg)
    assert any(s.kind == "open_loop" for s in signals)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: FAIL — no se emiten `stale_project` ni `open_loop`

- [ ] **Step 3: Write minimal implementation**

Añade los detectores y enchúfalos en `detect_startup_signals`:

```python
# proactivity/signals.py  (añadir antes de detect_startup_signals)
def _stale_project(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        if st.staleness_days is None or st.staleness_days < cfg.stale_project_days:
            continue
        if _IMPORTANCE_RANK.get(st.importance, 1) < _IMPORTANCE_RANK["normal"]:
            continue
        out.append(
            Signal(
                kind="stale_project",
                project=st.project,
                payload={"days": st.staleness_days, "importance": st.importance},
                base_priority=0.45,
                evidence=[f"card:{st.project}"],
            )
        )
    return out


def _open_loop(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        # decisión registrada pero el proyecto lleva tiempo sin tocarse:
        # bucle abierto (algo se decidió y no avanzó).
        if not st.open_decisions:
            continue
        if st.staleness_days is None or st.staleness_days < cfg.stale_project_days:
            continue
        out.append(
            Signal(
                kind="open_loop",
                project=st.project,
                payload={"decision": st.open_decisions[0], "days": st.staleness_days},
                base_priority=0.5,
                evidence=[f"card:{st.project}"],
            )
        )
    return out
```

Y dentro de `detect_startup_signals`, tras el `extend(_stale_pending(...))`:

```python
    signals.extend(_stale_project(states, cfg))
    signals.extend(_open_loop(states, cfg))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/signals.py tests/test_proactivity_signals.py
git commit -m "feat(proactivity): detectores stale_project y open_loop"
```

---

## Task 6: Detectores contextuales `cross_project` y `ctx_pending`

**Files:**
- Modify: `proactivity/signals.py`
- Test: `tests/test_proactivity_signals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_signals.py  (añadir)
from types import SimpleNamespace

from proactivity.signals import detect_contextual_signals


class FakeRAG:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search(self, query, top_k=3):
        self.queries.append((query, top_k))
        return self.results[:top_k]


def _rag(score, text, title="Nota", rel_path="Jarvis Memory/Interview_Copilot.md"):
    return SimpleNamespace(score=score, chunk=SimpleNamespace(title=title, rel_path=rel_path, text=text))


def test_ctx_pending_fires_when_active_project_has_pendings():
    cfg = ProactivityConfig()
    states = [_state(project="Polymath IDE")]
    rag = FakeRAG()
    signals = detect_contextual_signals(
        "sigamos con Polymath IDE el server", states, rag, cfg
    )
    assert any(s.kind == "ctx_pending" and s.project == "Polymath IDE" for s in signals)


def test_cross_project_fires_on_high_score_other_project():
    cfg = ProactivityConfig()
    states = [_state(project="Polymath IDE")]
    rag = FakeRAG(results=[_rag(0.82, "Implementamos FAISS RAG local", title="Interview_Copilot")])
    signals = detect_contextual_signals(
        "quiero búsqueda semántica con FAISS", states, rag, cfg
    )
    assert any(s.kind == "cross_project" for s in signals)
    cp = next(s for s in signals if s.kind == "cross_project")
    assert cp.evidence  # cita la nota de origen


def test_cross_project_ignores_low_score():
    cfg = ProactivityConfig()
    rag = FakeRAG(results=[_rag(0.10, "ruido")])
    signals = detect_contextual_signals("algo", [_state()], rag, cfg)
    assert all(s.kind != "cross_project" for s in signals)


def test_contextual_rag_failure_is_fail_safe():
    cfg = ProactivityConfig()

    class Boom:
        def search(self, *a, **k):
            raise RuntimeError("boom")

    # no debe propagar; devuelve lo que pueda (ctx_pending sigue disponible)
    signals = detect_contextual_signals("Polymath IDE", [_state()], Boom(), cfg)
    assert all(s.kind != "cross_project" for s in signals)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: FAIL con `ImportError: cannot import name 'detect_contextual_signals'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/signals.py  (añadir)
from memory import triage as triage_mod

CROSS_MIN_SCORE = 0.35


def _ctx_pending(active_project: str | None, states: list[ProjectState]) -> list[Signal]:
    if not active_project:
        return []
    by_name = {st.project: st for st in states}
    st = by_name.get(active_project)
    if st is None or not st.open_pendings:
        return []
    return [
        Signal(
            kind="ctx_pending",
            project=active_project,
            payload={"pending": st.open_pendings[0]},
            base_priority=0.7,
            evidence=[f"card:{active_project}"],
        )
    ]


def _cross_project(turn_text: str, active_project: str | None, rag) -> list[Signal]:
    try:
        results = rag.search(turn_text, top_k=3)
    except Exception:
        return []
    out: list[Signal] = []
    for r in results or []:
        if getattr(r, "score", 0.0) < CROSS_MIN_SCORE:
            continue
        title = getattr(r.chunk, "title", "") or ""
        rel = getattr(r.chunk, "rel_path", "") or ""
        # heurística: si la nota relevante NO es del proyecto activo, es una
        # conexión cross-proyecto (intuición).
        if active_project and active_project.lower() in (title + " " + rel).lower():
            continue
        snippet = " ".join((r.chunk.text or "").split())[:200]
        out.append(
            Signal(
                kind="cross_project",
                project=active_project or title,
                payload={"snippet": snippet, "source_title": title},
                base_priority=0.55,
                evidence=[rel or title],
            )
        )
        break  # una conexión cross por turno basta (anti-ruido)
    return out


def detect_contextual_signals(
    turn_text: str,
    states: list[ProjectState],
    rag,
    cfg: ProactivityConfig,
) -> list[Signal]:
    """Señales que dependen del texto del turno (detección en tiempo real)."""
    active = triage_mod.detect_project(turn_text or "")
    signals: list[Signal] = []
    signals.extend(_ctx_pending(active, states))
    signals.extend(_cross_project(turn_text or "", active, rag))
    return signals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_signals.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/signals.py tests/test_proactivity_signals.py
git commit -m "feat(proactivity): detectores contextuales cross_project y ctx_pending"
```

---

## Task 7: `Opportunity` + scoring + dedup + min_score

**Files:**
- Create: `proactivity/opportunity_queue.py`
- Test: `tests/test_proactivity_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_queue.py
from proactivity.config import ProactivityConfig
from proactivity.signals import Signal
from proactivity.opportunity_queue import Opportunity, OpportunityQueue, opportunity_id


def _signal(kind="stale_pending", project="Polymath IDE", prio=0.6, payload=None):
    return Signal(
        kind=kind,
        project=project,
        payload=payload or {"pending": "conectar el agente"},
        base_priority=prio,
        evidence=[f"card:{project}"],
    )


def test_opportunity_id_is_stable_and_distinguishes(tmp_path):
    a = opportunity_id(_signal())
    b = opportunity_id(_signal())
    c = opportunity_id(_signal(project="MTurk HITL Agent"))
    assert a == b
    assert a != c


def test_ingest_then_top_returns_highest_score(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([
        _signal(prio=0.4, payload={"pending": "menor"}),
        _signal(kind="ctx_pending", prio=0.9, payload={"pending": "mayor"}),
    ])
    top = q.top_opportunity()
    assert isinstance(top, Opportunity)
    assert top.signal.kind == "ctx_pending"
    assert "what" in top.suggestion_struct


def test_min_score_filters_weak_opportunities(tmp_path):
    cfg = ProactivityConfig(min_score=0.95)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([_signal(prio=0.1)])
    assert q.top_opportunity() is None


def test_dedup_same_id_not_offered_twice_in_session(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([_signal()])
    top = q.top_opportunity()
    assert top is not None
    q.mark_offered(top.id)
    assert q.top_opportunity() is None  # ya ofrecida en esta sesión
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_queue.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.opportunity_queue'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/opportunity_queue.py
"""Priorización y anti-spam de oportunidades proactivas (Fase 3).

Convierte Signals en Opportunities puntuadas, deduplica dentro de la sesión,
aplica cooldown entre sesiones y persiste el historial en JSON. El "aprendizaje"
de qué ignora Isaac es un contador determinista, sin ML.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .config import ProactivityConfig
from .signals import Signal

_IMPORTANCE_WEIGHT = {"low": 0.7, "normal": 1.0, "high": 1.3}


@dataclass(frozen=True)
class Opportunity:
    id: str
    signal: Signal
    score: float
    suggestion_struct: dict


def opportunity_id(signal: Signal) -> str:
    """Hash estable por (kind, project, payload-clave) para dedup/cooldown."""
    key = signal.payload.get("pending") or signal.payload.get("decision") or signal.payload.get("snippet") or ""
    raw = f"{signal.kind}|{signal.project}|{key}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


_WHAT_BY_KIND = {
    "stale_pending": "Retomar un pendiente que lleva días abierto",
    "stale_project": "Volver a un proyecto importante sin tocar",
    "open_loop": "Cerrar una decisión que no avanzó",
    "cross_project": "Reutilizar algo ya resuelto en otro proyecto",
    "ctx_pending": "Hay un pendiente abierto del proyecto que mencionaste",
}


def _suggestion_struct(signal: Signal) -> dict:
    return {
        "what": _WHAT_BY_KIND.get(signal.kind, "Oportunidad"),
        "project": signal.project,
        "why_now": signal.payload,
        "evidence": list(signal.evidence),
        "action_hint": signal.kind,
    }


def _score(signal: Signal) -> float:
    # score = base_priority × importance_weight(implícito en base) — aquí simple y
    # determinista; importance ya influyó en base_priority de algunos detectores.
    return round(signal.base_priority, 4)


class OpportunityQueue:
    def __init__(self, state_path: Path, *, config: ProactivityConfig) -> None:
        self.state_path = Path(state_path)
        self.config = config
        self._history = self._load()           # {id: {offered_at, dismissed_at, count}}
        self._offered_this_session: set[str] = set()
        self._candidates: list[Opportunity] = []

    # ---- persistencia ----
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}  # archivo ausente o corrupto: se reinicia sin romper

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._history, indent=2), encoding="utf-8")
        except Exception:
            pass  # fail-safe: no persistir no es un error fatal

    # ---- ingest / consulta ----
    def ingest(self, signals: list[Signal]) -> None:
        for sig in signals:
            score = _score(sig)
            if score < self.config.min_score:
                continue
            opp = Opportunity(
                id=opportunity_id(sig),
                signal=sig,
                score=score,
                suggestion_struct=_suggestion_struct(sig),
            )
            self._candidates.append(opp)

    def top_opportunity(self, *, now: datetime | None = None) -> Opportunity | None:
        ranked = sorted(self._candidates, key=lambda o: o.score, reverse=True)
        for opp in ranked:
            if opp.id in self._offered_this_session:
                continue
            return opp
        return None

    def mark_offered(self, opp_id: str) -> None:
        self._offered_this_session.add(opp_id)
        rec = self._history.setdefault(opp_id, {})
        rec["offered_at"] = datetime.now().isoformat()
        rec["count"] = int(rec.get("count", 0)) + 1
        self._save()

    def mark_dismissed(self, opp_id: str) -> None:
        rec = self._history.setdefault(opp_id, {})
        rec["dismissed_at"] = datetime.now().isoformat()
        self._save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_queue.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/opportunity_queue.py tests/test_proactivity_queue.py
git commit -m "feat(proactivity): Opportunity + scoring + dedup + min_score"
```

---

## Task 8: Cooldown, tope por sesión y persistencia robusta

**Files:**
- Modify: `proactivity/opportunity_queue.py`
- Test: `tests/test_proactivity_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_queue.py  (añadir)
from datetime import datetime, timedelta


def test_dismissed_in_cooldown_is_suppressed(tmp_path):
    cfg = ProactivityConfig(min_score=0.0, cooldown_days=7)
    path = tmp_path / "state.json"
    q1 = OpportunityQueue(path, config=cfg)
    q1.ingest([_signal()])
    opp = q1.top_opportunity()
    q1.mark_dismissed(opp.id)

    # nueva sesión (nueva instancia, mismo archivo): dentro del cooldown → suprimida
    q2 = OpportunityQueue(path, config=cfg)
    q2.ingest([_signal()])
    assert q2.top_opportunity() is None


def test_dismissed_after_cooldown_reappears(tmp_path):
    cfg = ProactivityConfig(min_score=0.0, cooldown_days=7)
    path = tmp_path / "state.json"
    q1 = OpportunityQueue(path, config=cfg)
    q1.ingest([_signal()])
    opp = q1.top_opportunity()
    q1.mark_dismissed(opp.id)
    # forzar dismissed_at viejo (más allá del cooldown)
    import json
    hist = json.loads(path.read_text(encoding="utf-8"))
    hist[opp.id]["dismissed_at"] = (datetime.now() - timedelta(days=30)).isoformat()
    path.write_text(json.dumps(hist), encoding="utf-8")

    q2 = OpportunityQueue(path, config=cfg)
    q2.ingest([_signal()])
    assert q2.top_opportunity() is not None


def test_max_per_session_caps_offers(tmp_path):
    cfg = ProactivityConfig(min_score=0.0, max_per_session=1)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([
        _signal(project="Polymath IDE"),
        _signal(project="MTurk HITL Agent"),
    ])
    first = q.top_opportunity()
    assert first is not None
    q.mark_offered(first.id)
    # alcanzado el tope de la sesión → no más ofertas aunque haya candidatos
    assert q.top_opportunity() is None


def test_corrupt_state_file_does_not_crash(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    cfg = ProactivityConfig(min_score=0.0)
    q = OpportunityQueue(path, config=cfg)  # no debe lanzar
    q.ingest([_signal()])
    assert q.top_opportunity() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_queue.py -v`
Expected: FAIL — `test_dismissed_in_cooldown_is_suppressed` y `test_max_per_session_caps_offers` fallan (aún no hay cooldown ni tope)

- [ ] **Step 3: Write minimal implementation**

Modifica `top_opportunity` para aplicar cooldown y tope por sesión:

```python
# proactivity/opportunity_queue.py  — reemplaza top_opportunity
    def _in_cooldown(self, opp_id: str, now: datetime) -> bool:
        rec = self._history.get(opp_id)
        if not rec:
            return False
        dismissed = rec.get("dismissed_at")
        if not dismissed:
            return False
        try:
            when = datetime.fromisoformat(dismissed)
        except (ValueError, TypeError):
            return False
        return (now - when) < timedelta(days=self.config.cooldown_days)

    def top_opportunity(self, *, now: datetime | None = None) -> Opportunity | None:
        now = now or datetime.now()
        if len(self._offered_this_session) >= self.config.max_per_session:
            return None
        ranked = sorted(self._candidates, key=lambda o: o.score, reverse=True)
        for opp in ranked:
            if opp.id in self._offered_this_session:
                continue
            if self._in_cooldown(opp.id, now):
                continue
            return opp
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_queue.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/opportunity_queue.py tests/test_proactivity_queue.py
git commit -m "feat(proactivity): cooldown, tope por sesión y persistencia robusta"
```

---

## Task 9: `render_briefing` — bloque estructurado de arranque

**Files:**
- Create: `proactivity/briefing.py`
- Test: `tests/test_proactivity_briefing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_briefing.py
from proactivity.config import ProactivityConfig
from proactivity.signals import Signal
from proactivity.opportunity_queue import Opportunity, opportunity_id


def _opp(kind, project, why):
    sig = Signal(kind=kind, project=project, payload={"pending": why}, base_priority=0.6, evidence=[f"card:{project}"])
    return Opportunity(
        id=opportunity_id(sig), signal=sig, score=0.6,
        suggestion_struct={"what": "x", "project": project, "why_now": {"pending": why}, "evidence": [f"card:{project}"], "action_hint": kind},
    )


def test_render_briefing_lists_top_k():
    from proactivity.briefing import render_briefing
    opps = [
        _opp("stale_pending", "Upwork Agent", "setup .env + RSS + Discord webhook"),
        _opp("stale_project", "MTurk HITL Agent", "smoke test pendiente"),
        _opp("cross_project", "Interview_Copilot", "FAISS reutilizable"),
        _opp("stale_pending", "Polymath IDE", "conectar agente"),
    ]
    block = render_briefing(opps, top_k=3)
    assert "BRIEFING PROACTIVO" in block
    assert "Upwork Agent" in block
    assert "MTurk HITL Agent" in block
    # respeta top_k=3: el cuarto no aparece
    assert "Polymath IDE" not in block


def test_render_briefing_empty_when_no_opportunities():
    from proactivity.briefing import render_briefing
    assert render_briefing([], top_k=3) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_briefing.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.briefing'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/briefing.py
"""Render del briefing proactivo de arranque (Fase 3).

Produce un bloque ESTRUCTURADO para el system_prompt. La narración la hace
Gemini (prompt-first): el bloque entrega datos, no una frase ya hecha.
"""

from __future__ import annotations

from .opportunity_queue import Opportunity

_BAR = "═" * 11


def _line(opp: Opportunity) -> str:
    s = opp.suggestion_struct
    why = s.get("why_now") or {}
    detail = why.get("pending") or why.get("decision") or why.get("snippet") or ""
    detail = " ".join(str(detail).split())
    if len(detail) > 140:
        detail = detail[:137].rstrip() + "..."
    tag = opp.signal.kind
    return f"- [{s.get('project')}] ({tag}) {detail}".rstrip()


def render_briefing(opportunities: list[Opportunity], *, top_k: int = 3) -> str:
    top = [o for o in opportunities][:max(0, top_k)]
    if not top:
        return ""
    lines = [f"{_BAR} BRIEFING PROACTIVO {_BAR}"]
    lines.extend(_line(o) for o in top)
    lines.append(f"{_BAR}{_BAR}")
    lines.append("(Menciónalo solo si encaja al abrir; no recites la lista. Una sugerencia, no un informe.)")
    return "\n".join(lines)
```

Nota: en el test, `render_briefing(opps, top_k=3)` se llama posicional; la firma usa
keyword-only (`*, top_k`). Ajusta la llamada del test a `render_briefing(opps, top_k=3)`
(ya es keyword) — válido. Si prefieres, cambia la firma a posicional; mantén una sola
convención.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_briefing.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/briefing.py tests/test_proactivity_briefing.py
git commit -m "feat(proactivity): render_briefing del bloque de arranque"
```

---

## Task 10: Verificación de regresión del motor

**Files:** ninguno (verificación)

- [ ] **Step 1: Smoke import del paquete completo**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "import proactivity, proactivity.config, proactivity.project_state, proactivity.signals, proactivity.opportunity_queue, proactivity.briefing; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Correr toda la suite**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest -q`
Expected: PASS — los tests previos (123) + los nuevos del motor de proactividad, todo verde.

- [ ] **Step 3: Si algo rompió, arreglar antes de continuar**

Si algún test previo falla, investigar con `superpowers:systematic-debugging`. No marcar la fase como completa con tests rojos.

- [ ] **Step 4: Commit final (si hubo arreglos)**

```bash
git add -A
git commit -m "test(proactivity): motor determinista completo, suite verde"
```

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec (sección "Motor de proactividad"):**
- `project_state.py` → `ProjectState` + `build_project_states` → Tasks 2, 3. ✓
- `signals.py` → `Signal` + 5 detectores (stale_pending, stale_project, open_loop, cross_project, ctx_pending) → Tasks 4, 5, 6. ✓
- `opportunity_queue.py` → scoring + dedup + cooldown + tope + persistencia + archivo corrupto → Tasks 7, 8. ✓
- `briefing.py` → top-K + vacío + formato estructurado → Task 9. ✓
- Config `.env` (7 vars) → Task 1 (lectura); documentar en `.env.example` es del **Plan B**. ✓
- Fail-safe por fuente → Tasks 3 (`_load_card`/`_session_files` try/except), 6 (`_cross_project` try/except), 7-8 (`_load`/`_save` try/except). ✓
- Determinismo (sin LLM) → todo el motor; tests sin reasoner. ✓

**Fuera de este plan (Plan B — runtime):** tool `jarvis_proactive_check` (decl+handler+dispatcher), campo `proactivity` en `ToolContext`, cableado en `jarvis.py` `build()`/`_on_turn_complete()`, instrucciones de ventana natural en `gemini/system_prompt.py`, `.env.example`, consolidation checkpoint y resolución de ambigüedad. Solo el caso de uso 3 (planificación autónoma) usa `context_assembler` (Fase 2, ya implementada).

**Placeholder scan:** sin TBD/TODO; cada step de código trae el código real.

**Consistencia de tipos:** `ProjectState(project, last_touched, staleness_days, open_pendings, open_decisions, current_state, importance, confidence)`; `Signal(kind, project, payload, base_priority, evidence)`; `Opportunity(id, signal, score, suggestion_struct)`; `OpportunityQueue(state_path, *, config)` con `ingest`/`top_opportunity`/`mark_offered`/`mark_dismissed`; `ProactivityConfig.from_env`. Nombres usados consistentemente entre tasks.

**Dependencia a confirmar en ejecución:** ninguna pendiente — las firmas del repo (cards, sesiones, RAG, triage) fueron verificadas contra el código real antes de escribir el plan. El único punto de fricción conocido: el frontmatter `project` de las sesiones está hardcodeado a jarvis, por eso `_last_touched_map` parsea la sección de cuerpo `## Proyectos tocados` (no el frontmatter).
```

