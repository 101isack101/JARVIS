# Detección de lagunas de conocimiento (KSI Fase 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Al cerrar sesión, JARVIS detecta lagunas de conocimiento (cards pobres, hechos obsoletos, contradicciones abiertas), el reasoner las convierte en preguntas naturales que se persisten en la card, y el motor de proactividad las pregunta de forma fluida en conversación — auto-retirándose en cuanto las respondes.

**Architecture:** Dos partes conectadas por la sección `## Preguntas abiertas` de cada Project Memory Card. Parte 1 (offline, dentro de `KnowledgeImprover._run_inner` de Fase 1): detección determinista → formulación batch presupuestada → persistencia aditiva con dedup y auto-retiro por `gap_id`. Parte 2 (en vivo, dentro del `ProactivityEngine` de Fase 3): `ProjectState` lee las preguntas abiertas y dos detectores nuevos las emiten como señales `knowledge_gap` que el motor expone en briefing y charla.

**Tech Stack:** Python 3.11, dataclasses frozen, pytest. Sin dependencias nuevas. Reusa Fase 1 (`memory/self_improvement/`) y Fase 3 (`proactivity/`). Tests: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-15-knowledge-gap-detection-design.md`

---

## File Structure

```
memory/self_improvement/
  gaps.py            # KnowledgeGap + detectores + formulate + persistencia   — Tasks 1,2,3
  improver.py        # (modificar) llamar al pipeline de gaps en _run_inner    — Task 4

proactivity/
  project_state.py   # (modificar) ProjectState.open_questions + poblarlo      — Task 5
  signals.py         # (modificar) _knowledge_gap + _ctx_knowledge_gap         — Task 6
  opportunity_queue.py # (modificar) opportunity_id por gap_id + _WHAT_BY_KIND — Task 6

tests/
  test_ksi_gaps.py                 # Tasks 1,2,3
  test_proactivity_knowledge_gap.py # Tasks 5,6

.env.example, CHANGELOG.md                                                      — Task 7
```

**Tipo central `KnowledgeGap`** (definido en Task 1, usado en todas):

```python
@dataclass(frozen=True)
class KnowledgeGap:
    gap_id: str       # sha1(f"{kind}|{project}|{key}")[:16] — estable
    kind: str         # "poor_card" | "stale_fact" | "open_contradiction"
    project: str
    key: str          # firma estable para el hash (project / event.id / "idA|idB")
    context: str = "" # texto legible para el reasoner
    question: str = "" # la formula el reasoner; "" hasta entonces
```

---

## Task 1: Detectores deterministas de laguna

**Files:**
- Create: `memory/self_improvement/gaps.py`
- Test: `tests/test_ksi_gaps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_gaps.py
from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.gaps import (
    KnowledgeGap,
    collect_gaps,
    detect_poor_cards,
    detect_stale_facts,
    gap_id,
)


class _Cfg:
    min_card_bullets = 4
    stale_confidence = 0.3
    decay_half_life_days = 45


class _State:
    def __init__(self, project, current_state, pendings, decisions, staleness_days):
        self.project = project
        self.current_state = current_state
        self.open_pendings = pendings
        self.open_decisions = decisions
        self.staleness_days = staleness_days


def _ev(text, project="JARVIS", conf=0.6, learned="2026-06-15", sup=None):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project,
                       learned_at=learned, confidence=conf, superseded_by=sup)


def test_gap_id_stable():
    a = gap_id("poor_card", "JARVIS", "JARVIS")
    b = gap_id("poor_card", "JARVIS", "JARVIS")
    assert a == b and len(a) == 16


def test_detect_poor_cards():
    rich = _State("Rich", ["a"], ["b", "c"], ["d", "e"], staleness_days=2)
    poor = _State("Poor", [], [], [], staleness_days=3)
    gaps = detect_poor_cards([rich, poor], _Cfg())
    assert [g.project for g in gaps] == ["Poor"]
    assert gaps[0].kind == "poor_card"


def test_poor_card_ignores_projects_without_activity():
    # staleness_days None = proyecto sin sesión reciente: no molestar
    ghost = _State("Ghost", [], [], [], staleness_days=None)
    assert detect_poor_cards([ghost], _Cfg()) == []


def test_detect_stale_facts():
    fresh = _ev("hecho fresco", conf=0.8, learned="2026-06-15")
    stale = _ev("hecho viejo", conf=0.8, learned="2026-01-01")  # muy decaído
    superseded = _ev("ya reemplazado", conf=0.1, learned="2026-01-01", sup="x")
    gaps = detect_stale_facts([fresh, stale, superseded], _Cfg(), today="2026-06-15")
    keys = [g.context for g in gaps]
    assert any("hecho viejo" in k for k in keys)
    assert not any("fresco" in k for k in keys)
    assert not any("reemplazado" in k for k in keys)


def test_collect_gaps_merges_all_kinds():
    poor = _State("Poor", [], [], [], staleness_days=3)
    stale = _ev("hecho viejo", conf=0.8, learned="2026-01-01", project="Poor")
    gaps = collect_gaps([poor], [stale], _Cfg(), today="2026-06-15")
    kinds = {g.kind for g in gaps}
    assert "poor_card" in kinds and "stale_fact" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'memory.self_improvement.gaps'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/gaps.py
"""Detección de lagunas de conocimiento (KSI Fase 2).

Tres detectores deterministas sobre el estado del vault + el reasoner para
formular preguntas naturales. Reusa confianza y contradicciones de Fase 1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import confidence as conf_mod
from .detectors import detect_contradictions


@dataclass(frozen=True)
class KnowledgeGap:
    gap_id: str
    kind: str
    project: str
    key: str
    context: str = ""
    question: str = ""


def gap_id(kind: str, project: str, key: str) -> str:
    raw = f"{kind}|{project}|{key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _make(kind: str, project: str, key: str, context: str) -> KnowledgeGap:
    return KnowledgeGap(
        gap_id=gap_id(kind, project, key),
        kind=kind, project=project, key=key, context=context,
    )


def detect_poor_cards(states, cfg) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for st in states:
        if getattr(st, "staleness_days", None) is None:
            continue  # proyecto sin actividad reciente: no molestar
        total = len(st.open_pendings) + len(st.open_decisions) + len(st.current_state)
        if not st.current_state or total < cfg.min_card_bullets:
            out.append(_make(
                "poor_card", st.project, st.project,
                f"El proyecto {st.project} tiene la Memory Card casi vacía.",
            ))
    return out


def detect_stale_facts(events, cfg, *, today=None) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for ev in events:
        if ev.superseded_by:
            continue
        decayed = conf_mod.decayed(
            ev.confidence, ev.learned_at,
            half_life_days=cfg.decay_half_life_days, today=today,
        )
        if decayed < cfg.stale_confidence:
            out.append(_make(
                "stale_fact", ev.project, ev.id,
                f"Hecho sin reconfirmar hace tiempo: {ev.text}",
            ))
    return out


def detect_open_contradictions(events) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for a, b in detect_contradictions(events):
        key = "|".join(sorted([a.id, b.id]))
        out.append(_make(
            "open_contradiction", a.project, key,
            f"Contradicción sin resolver: '{a.text}' vs '{b.text}'",
        ))
    return out


def collect_gaps(states, events, cfg, *, today=None) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    gaps.extend(detect_poor_cards(states, cfg))
    gaps.extend(detect_stale_facts(events, cfg, today=today))
    gaps.extend(detect_open_contradictions(events))
    return gaps
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/gaps.py tests/test_ksi_gaps.py
git commit -m "feat(ksi): detectores deterministas de lagunas (Fase 2 Task 1)"
```
(Termina el mensaje con: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: Formulación de preguntas (reasoner, presupuestado)

**Files:**
- Modify: `memory/self_improvement/gaps.py`
- Test: `tests/test_ksi_gaps.py`

`reasoner.ask(instructions, context_extra="", max_tokens=...)` devuelve objeto con `.text`.
Reusa `judge._extract_json` (Fase 1) para el self-heal del JSON.

- [ ] **Step 1: Write the failing test (append to tests/test_ksi_gaps.py)**

```python
from memory.self_improvement.gaps import formulate_questions


class _Resp:
    def __init__(self, text): self.text = text


class _Reasoner:
    def __init__(self, text): self._text = text; self.calls = 0
    def ask(self, instructions, context_extra="", max_tokens=600):
        self.calls += 1
        return _Resp(self._text)


def _gap(gid="g1"):
    return KnowledgeGap(gap_id=gid, kind="poor_card", project="JARVIS",
                        key="JARVIS", context="card vacía")


def test_formulate_assigns_questions_by_gap_id():
    gaps = [_gap("g1"), _gap("g2")]
    reasoner = _Reasoner('{"g1": "¿En qué estás con JARVIS?", "g2": "¿Y con esto?"}')
    out = formulate_questions(reasoner, gaps, token_budget=1000)
    by_id = {g.gap_id: g.question for g in out}
    assert by_id["g1"] == "¿En qué estás con JARVIS?"
    assert by_id["g2"] == "¿Y con esto?"


def test_formulate_skips_without_budget():
    reasoner = _Reasoner('{"g1": "x"}')
    out = formulate_questions(reasoner, [_gap("g1")], token_budget=0)
    assert out[0].question == ""
    assert reasoner.calls == 0


def test_formulate_bad_json_leaves_questions_empty():
    reasoner = _Reasoner("no json")
    out = formulate_questions(reasoner, [_gap("g1")], token_budget=1000)
    assert out[0].question == ""


def test_formulate_no_gaps_returns_empty():
    assert formulate_questions(_Reasoner("{}"), [], token_budget=1000) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -k formulate -v`
Expected: FAIL con `ImportError: cannot import name 'formulate_questions'`

- [ ] **Step 3: Add implementation to gaps.py**

Add to the imports at the top of `gaps.py`:
```python
from dataclasses import dataclass, replace

from .judge import _extract_json
```
(replace the existing `from dataclasses import dataclass` line with `from dataclasses import dataclass, replace`)

Add at the end of `gaps.py`:
```python
_FORMULATE_INSTRUCTIONS = (
    "Eres JARVIS. Te paso lagunas en tu conocimiento sobre los proyectos de Isaac. "
    "Por cada una, formula UNA pregunta breve, natural y en español (primera persona, "
    "directa, sin jerga técnica ni la palabra 'laguna'). Responde SOLO un objeto JSON "
    "que mapea gap_id -> pregunta. Ejemplo: {\"abc123\": \"¿Sigue vigente X?\"}."
)


def formulate_questions(reasoner, gaps: list[KnowledgeGap], *, token_budget: int, max_tokens: int = 600) -> list[KnowledgeGap]:
    if not gaps:
        return []
    if reasoner is None or token_budget <= 0:
        return list(gaps)
    payload = "\n".join(f"- {g.gap_id}: {g.context}" for g in gaps)
    try:
        resp = reasoner.ask(_FORMULATE_INSTRUCTIONS, context_extra="LAGUNAS:\n" + payload, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return list(gaps)
    if not isinstance(data, dict):
        return list(gaps)
    out: list[KnowledgeGap] = []
    for g in gaps:
        q = data.get(g.gap_id)
        out.append(replace(g, question=str(q).strip()) if isinstance(q, str) and q.strip() else g)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -k formulate -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/gaps.py tests/test_ksi_gaps.py
git commit -m "feat(ksi): formulación de preguntas presupuestada (Fase 2 Task 2)"
```
(Termina con el trailer Co-Authored-By.)

---

## Task 3: Persistencia — sección "Preguntas abiertas" (dedup + auto-retiro)

**Files:**
- Modify: `memory/self_improvement/gaps.py`
- Test: `tests/test_ksi_gaps.py`

Formato del bullet de pregunta:
`- {fecha} {pregunta} <!-- ksi-gap:{"gap_id":"...","kind":"...","status":"open"} -->`

Reusa `memory/triage.py`: `project_card_path(vault, project)`, `initial_project_card_body(project)`.
Reusa `memory/notes.py`: `read_note(vault, path)`, `write_note(vault, path, body, frontmatter)`.

- [ ] **Step 1: Write the failing test (append to tests/test_ksi_gaps.py)**

```python
from memory.self_improvement.gaps import (
    apply_questions,
    parse_questions_section,
    serialize_gap_bullet,
)
from memory.obsidian_vault import ObsidianVault


def _vault(tmp_path):
    # ObsidianVault apunta a un vault temporal; memory_path es la carpeta escribible.
    return ObsidianVault(vault_path=tmp_path)


def test_serialize_and_parse_roundtrip():
    line = serialize_gap_bullet("¿Sigue vigente X?", gap_id="abc123", kind="stale_fact",
                                status="open", today="2026-06-15")
    parsed = parse_questions_section("## Preguntas abiertas\n\n" + line + "\n")
    assert "abc123" in parsed
    assert parsed["abc123"]["status"] == "open"
    assert "¿Sigue vigente X?" in parsed["abc123"]["display"]


def test_apply_writes_and_dedups(tmp_path):
    vault = _vault(tmp_path)
    g = KnowledgeGap(gap_id="g1", kind="poor_card", project="JARVIS", key="JARVIS",
                     context="x", question="¿En qué estás con JARVIS?")
    apply_questions(vault, "JARVIS", [g], active_gap_ids={"g1"}, today="2026-06-15")
    apply_questions(vault, "JARVIS", [g], active_gap_ids={"g1"}, today="2026-06-15")  # idempotente
    from memory import triage as triage_mod
    body = triage_mod.project_card_path(vault, "JARVIS").read_text(encoding="utf-8")
    assert body.count("g1") == 1                      # no duplica
    assert "¿En qué estás con JARVIS?" in body


def test_apply_auto_retires_absent_gaps(tmp_path):
    vault = _vault(tmp_path)
    g = KnowledgeGap(gap_id="g1", kind="poor_card", project="JARVIS", key="JARVIS",
                     context="x", question="¿Pregunta vieja?")
    apply_questions(vault, "JARVIS", [g], active_gap_ids={"g1"}, today="2026-06-15")
    # corrida siguiente: g1 ya NO está entre los activos -> se marca resuelta
    apply_questions(vault, "JARVIS", [], active_gap_ids=set(), today="2026-06-16")
    from memory import triage as triage_mod
    body = triage_mod.project_card_path(vault, "JARVIS").read_text(encoding="utf-8")
    parsed = parse_questions_section(body)
    assert parsed["g1"]["status"] == "resolved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -k "apply or roundtrip" -v`
Expected: FAIL con `ImportError`

- [ ] **Step 3: Add implementation to gaps.py**

Add to the imports at the top of `gaps.py`:
```python
import json
import re
from datetime import date

from memory import notes as notes_mod
from memory import triage as triage_mod
```

Add at the end of `gaps.py`:
```python
QUESTIONS_HEADING = "Preguntas abiertas"
_GAP_TAG_RE = re.compile(r"<!--\s*ksi-gap:(?P<json>\{.*?\})\s*-->\s*$")


def serialize_gap_bullet(text: str, *, gap_id: str, kind: str, status: str, today: str) -> str:
    meta = {"gap_id": gap_id, "kind": kind, "status": status}
    tag = "<!-- ksi-gap:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->"
    return f"- {today} {text} {tag}"


def _serialize_existing(display: str, *, gap_id: str, kind: str, status: str) -> str:
    meta = {"gap_id": gap_id, "kind": kind, "status": status}
    tag = "<!-- ksi-gap:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->"
    return f"- {display} {tag}"


def parse_questions_section(body: str) -> dict:
    """Devuelve {gap_id: {"display", "kind", "status"}} de la sección de preguntas."""
    out: dict = {}
    in_section = False
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_section = line[3:].strip() == QUESTIONS_HEADING
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        m = _GAP_TAG_RE.search(stripped)
        if not m:
            continue
        try:
            meta = json.loads(m.group("json"))
        except json.JSONDecodeError:
            continue
        display = stripped[2:m.start()].strip()
        gid = str(meta.get("gap_id") or "")
        if gid:
            out[gid] = {"display": display, "kind": meta.get("kind", ""), "status": meta.get("status", "open")}
    return out


def _strip_section(body: str, heading: str) -> str:
    lines = (body or "").splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() == f"## {heading}":
            i += 1
            while i < n and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip()


def apply_questions(vault, project: str, gaps_with_questions: list[KnowledgeGap], active_gap_ids: set, *, today=None) -> None:
    today = today or date.today().isoformat()
    try:
        path = triage_mod.project_card_path(vault, project)
    except Exception:
        return
    if path.exists():
        note = notes_mod.read_note(vault, path)
        body, frontmatter = note.body, note.frontmatter
    else:
        body = triage_mod.initial_project_card_body(project)
        frontmatter = {"type": "project-memory-card", "project": project}

    existing = parse_questions_section(body)
    for g in gaps_with_questions:
        if g.question and g.gap_id not in existing:
            existing[g.gap_id] = {
                "display": f"{today} {g.question}", "kind": g.kind, "status": "open",
            }
    for gid, rec in existing.items():
        if rec["status"] == "open" and gid not in active_gap_ids:
            rec["status"] = "resolved"

    if not existing:
        return

    base = _strip_section(body, QUESTIONS_HEADING)
    ordered = sorted(existing.items(), key=lambda kv: (kv[1]["status"] != "open", kv[1]["display"]))
    section_lines = [f"## {QUESTIONS_HEADING}", ""]
    section_lines += [
        _serialize_existing(rec["display"], gap_id=gid, kind=rec["kind"], status=rec["status"])
        for gid, rec in ordered
    ]
    new_body = base.rstrip() + "\n\n" + "\n".join(section_lines).rstrip() + "\n"
    notes_mod.write_note(vault, path, new_body, frontmatter)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_gaps.py -v`
Expected: PASS (toda la suite de gaps verde)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/gaps.py tests/test_ksi_gaps.py
git commit -m "feat(ksi): persistencia de preguntas con dedup y auto-retiro (Fase 2 Task 3)"
```
(Termina con el trailer Co-Authored-By.)

---

## Task 4: Cablear el pipeline de gaps en KnowledgeImprover

**Files:**
- Modify: `memory/self_improvement/improver.py`
- Test: `tests/test_ksi_improver.py` (añadir 1 test)

`_run_inner` ya carga `events`. Necesita `states` (vía `build_project_states`, import perezoso
para no acoplar). Config gana 2 campos (se añaden en Task 7 al `.env`; aquí se usan con
defaults via getattr para no romper si faltan).

- [ ] **Step 1: Write the failing test (append to tests/test_ksi_improver.py)**

```python
def test_run_writes_open_questions_section(tmp_path):
    # Un evento "stale" (confianza baja, viejo) debe generar una pregunta en la card.
    from memory.self_improvement.config import KnowledgeImproverConfig
    old_event = _ev("hecho muy viejo")
    old_event = old_event.__class__(
        id=old_event.id, text=old_event.text, section=old_event.section,
        project=old_event.project, learned_at="2026-01-01", confidence=0.8,
    )

    class _Resp:
        text = '{"%s": "¿Sigue vigente esto?"}' % __import__("memory.self_improvement.gaps", fromlist=["gap_id"]).gap_id("stale_fact", "JARVIS", old_event.id)

    class _Reasoner:
        def ask(self, *a, **k): return _Resp()

    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(token_budget=1000),
        embed_fn=_embed, reasoner=_Reasoner(), event_loader=lambda _v: [old_event],
    )
    # vault real temporal para que apply_questions pueda escribir la card
    from memory.obsidian_vault import ObsidianVault
    imp.run(ObsidianVault(vault_path=tmp_path))
    from memory import triage as triage_mod
    card = triage_mod.project_card_path(ObsidianVault(vault_path=tmp_path), "JARVIS")
    assert card.exists()
    assert "Preguntas abiertas" in card.read_text(encoding="utf-8")
```

Note: `_ev` and `_embed` already exist at the top of `tests/test_ksi_improver.py` from Fase 1.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_improver.py -k open_questions -v`
Expected: FAIL (no se escribe la sección porque el pipeline de gaps no está cableado)

- [ ] **Step 3: Modify `memory/self_improvement/improver.py`**

Add to the imports block (after `from .proposer import to_signals`):
```python
from . import gaps as gaps_mod
```

Inside `_run_inner`, AFTER the F1 block that computes `signals` and ingests them into the
proactivity queue (i.e. after the `if signals and self.proactivity_engine is not None:` block),
and BEFORE the `memory_path = Path(vault.memory_path)` line, insert:

```python
        # Fase 2 — detección de lagunas + preguntas (aditivo, presupuestado).
        try:
            from proactivity.project_state import build_project_states
            states = build_project_states(vault)
        except Exception:
            states = []
        try:
            detected = gaps_mod.collect_gaps(states, events, self.config)
            formulated = gaps_mod.formulate_questions(self.reasoner, detected, token_budget=budget)
            by_project: dict[str, list] = {}
            for g in formulated:
                by_project.setdefault(g.project, []).append(g)
            for project, pgaps in by_project.items():
                active = {g.gap_id for g in pgaps}
                with_q = [g for g in pgaps if g.question]
                gaps_mod.apply_questions(vault, project, with_q, active)
        except Exception:
            pass
```

The detectors read `cfg.min_card_bullets`, `cfg.stale_confidence`, and `cfg.decay_half_life_days`.
`decay_half_life_days` already exists on `KnowledgeImproverConfig` (Fase 1). The other two are
added in Task 7. To avoid an ordering dependency, confirm the detectors use `getattr` with
defaults — update `gaps.py` `detect_poor_cards` and `detect_stale_facts` to read:
`getattr(cfg, "min_card_bullets", 4)` and `getattr(cfg, "stale_confidence", 0.3)` respectively.

Apply these two edits in `gaps.py`:
- In `detect_poor_cards`: replace `cfg.min_card_bullets` with `getattr(cfg, "min_card_bullets", 4)`.
- In `detect_stale_facts`: replace `cfg.stale_confidence` with `getattr(cfg, "stale_confidence", 0.3)`.

(Note: the Task 1 tests use a `_Cfg` class that defines these attributes directly, so the
`getattr` change keeps those tests green.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_improver.py tests/test_ksi_gaps.py -v`
Expected: PASS (incluye el nuevo test + todos los de Fase 1 sin regresión)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/improver.py memory/self_improvement/gaps.py tests/test_ksi_improver.py
git commit -m "feat(ksi): cablear detección de lagunas en el improver (Fase 2 Task 4)"
```
(Termina con el trailer Co-Authored-By.)

---

## Task 5: ProjectState gana `open_questions`

**Files:**
- Modify: `proactivity/project_state.py`
- Test: `tests/test_proactivity_knowledge_gap.py`

`open_questions` es `list[dict]` con `{"text", "gap_id"}`, excluyendo las `resolved`.
Reusa `parse_questions_section` de `gaps.py` para leer la sección.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_knowledge_gap.py
from proactivity.project_state import ProjectState, _open_questions_from_body


def test_open_questions_excludes_resolved():
    body = (
        "## Preguntas abiertas\n\n"
        "- 2026-06-15 ¿Pregunta viva? <!-- ksi-gap:{\"gap_id\":\"g1\",\"kind\":\"poor_card\",\"status\":\"open\"} -->\n"
        "- 2026-06-15 ¿Ya resuelta? <!-- ksi-gap:{\"gap_id\":\"g2\",\"kind\":\"poor_card\",\"status\":\"resolved\"} -->\n"
    )
    qs = _open_questions_from_body(body)
    assert len(qs) == 1
    assert qs[0]["gap_id"] == "g1"
    assert "¿Pregunta viva?" in qs[0]["text"]


def test_project_state_has_open_questions_field():
    st = ProjectState(
        project="JARVIS", last_touched=None, staleness_days=None,
        open_pendings=[], open_decisions=[], current_state=[],
        importance="normal", confidence="medium",
    )
    assert st.open_questions == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_knowledge_gap.py -v`
Expected: FAIL con `ImportError` / `TypeError` (campo inexistente)

- [ ] **Step 3: Modify `proactivity/project_state.py`**

Add `field` to the dataclass import (top of file):
```python
from dataclasses import dataclass, field
```

Add the new field to `ProjectState` (after `confidence: str`):
```python
    open_questions: list[dict] = field(default_factory=list)
```

Add this helper near the other parsing helpers (after `section_bullets`):
```python
def _open_questions_from_body(body: str) -> list[dict]:
    """Preguntas abiertas (no resueltas) de la card, como [{"text","gap_id"}]."""
    from memory.self_improvement.gaps import parse_questions_section
    out: list[dict] = []
    for gid, rec in parse_questions_section(body).items():
        if rec.get("status") == "open":
            out.append({"text": rec.get("display", ""), "gap_id": gid})
    return out
```

In `build_project_states`, where the `ProjectState(...)` is constructed, populate the new
field. Change the construction so that when `note is not None` it reads the questions, else
empty. Compute it just before building the state:
```python
        open_questions = _open_questions_from_body(note.body or "") if note is not None else []
```
and add `open_questions=open_questions,` to the `ProjectState(...)` kwargs.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_knowledge_gap.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add proactivity/project_state.py tests/test_proactivity_knowledge_gap.py
git commit -m "feat(proactividad): ProjectState.open_questions desde la card (Fase 2 Task 5)"
```
(Termina con el trailer Co-Authored-By.)

---

## Task 6: Detectores de surfacing + dedup por gap_id

**Files:**
- Modify: `proactivity/signals.py`
- Modify: `proactivity/opportunity_queue.py`
- Test: `tests/test_proactivity_knowledge_gap.py`

- [ ] **Step 1: Write the failing test (append to tests/test_proactivity_knowledge_gap.py)**

```python
from proactivity.signals import _knowledge_gap, _ctx_knowledge_gap
from proactivity.opportunity_queue import _WHAT_BY_KIND, _suggestion_struct, opportunity_id


def _state(project, questions):
    return ProjectState(
        project=project, last_touched=None, staleness_days=None,
        open_pendings=[], open_decisions=[], current_state=[],
        importance="normal", confidence="medium",
        open_questions=[{"text": q, "gap_id": gid} for q, gid in questions],
    )


def test_knowledge_gap_emits_one_signal_per_question():
    st = _state("JARVIS", [("¿Q1?", "g1"), ("¿Q2?", "g2")])
    sigs = _knowledge_gap([st], cfg=None)
    assert len(sigs) == 2
    assert all(s.kind == "knowledge_gap" for s in sigs)
    assert {s.payload["gap_id"] for s in sigs} == {"g1", "g2"}
    assert sigs[0].payload["snippet"] == "¿Q1?"


def test_ctx_knowledge_gap_prioritizes_active_project():
    a = _state("A", [("¿qA?", "ga")])
    b = _state("B", [("¿qB?", "gb")])
    sigs = _ctx_knowledge_gap("B", [a, b])
    assert len(sigs) == 1
    assert sigs[0].project == "B"
    assert sigs[0].base_priority >= 0.7


def test_opportunity_id_keys_on_gap_id():
    from proactivity.signals import Signal
    s1 = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "texto A", "gap_id": "g1"}, base_priority=0.5)
    s2 = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "texto REFRASEADO", "gap_id": "g1"}, base_priority=0.5)
    assert opportunity_id(s1) == opportunity_id(s2)  # mismo gap, distinto texto -> mismo id


def test_what_by_kind_has_knowledge_gap():
    assert "knowledge_gap" in _WHAT_BY_KIND
    from proactivity.signals import Signal
    sig = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "¿Q?", "gap_id": "g1"}, base_priority=0.5)
    assert _suggestion_struct(sig)["what"] == _WHAT_BY_KIND["knowledge_gap"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_knowledge_gap.py -k "knowledge_gap or opportunity_id or what_by_kind" -v`
Expected: FAIL con `ImportError` / `KeyError`

- [ ] **Step 3a: Modify `proactivity/signals.py`**

Add the startup detector (near `_open_loop`, before `detect_startup_signals`):
```python
def _knowledge_gap(states, cfg) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        for q in getattr(st, "open_questions", []):
            out.append(
                Signal(
                    kind="knowledge_gap",
                    project=st.project,
                    payload={"snippet": q["text"], "gap_id": q["gap_id"]},
                    base_priority=0.5,
                    evidence=[f"card:{st.project}"],
                )
            )
    return out
```

Add `_knowledge_gap` to `detect_startup_signals` (inside that function, after the existing extends):
```python
    signals.extend(_knowledge_gap(states, cfg))
```

Add the contextual detector (near `_ctx_pending`):
```python
def _ctx_knowledge_gap(active_project: str | None, states) -> list[Signal]:
    if not active_project:
        return []
    by_name = {st.project: st for st in states}
    st = by_name.get(active_project)
    if st is None:
        return []
    out: list[Signal] = []
    for q in getattr(st, "open_questions", []):
        out.append(
            Signal(
                kind="knowledge_gap",
                project=active_project,
                payload={"snippet": q["text"], "gap_id": q["gap_id"]},
                base_priority=0.72,
                evidence=[f"card:{active_project}"],
            )
        )
    return out
```

Add `_ctx_knowledge_gap` to `detect_contextual_signals`. Find that function (it composes the
contextual detectors) and add, inside it:
```python
    signals.extend(_ctx_knowledge_gap(active_project, states))
```
(Use the parameter names already present in `detect_contextual_signals` — it receives the
active project and `states`. If the active project is derived under a different local name,
use that name.)

- [ ] **Step 3b: Modify `proactivity/opportunity_queue.py`**

In `opportunity_id(signal)`, make `gap_id` the highest-priority key. Change the `key` line:
```python
    key = (
        signal.payload.get("gap_id")
        or signal.payload.get("pending")
        or signal.payload.get("decision")
        or signal.payload.get("snippet")
        or ""
    )
```

Add to `_WHAT_BY_KIND` (after the `memory_supersede` entry from Fase 1):
```python
    "knowledge_gap": "Preguntar algo que falta saber del proyecto",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_knowledge_gap.py -v`
Then regression: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/ -k "proactiv or ksi or gap" -q`
Expected: PASS, sin regresión en proactividad ni KSI.

- [ ] **Step 5: Commit**

```bash
git add proactivity/signals.py proactivity/opportunity_queue.py tests/test_proactivity_knowledge_gap.py
git commit -m "feat(proactividad): señales knowledge_gap + dedup por gap_id (Fase 2 Task 6)"
```
(Termina con el trailer Co-Authored-By.)

---

## Task 7: Config + .env.example + CHANGELOG

**Files:**
- Modify: `memory/self_improvement/config.py`
- Modify: `.env.example`
- Modify: `CHANGELOG.md`
- Test: `tests/test_ksi_config.py` (añadir asserts)

- [ ] **Step 1: Add failing asserts (append to tests/test_ksi_config.py)**

```python
def test_gap_config_defaults_and_env():
    cfg = KnowledgeImproverConfig()
    assert cfg.min_card_bullets >= 1
    assert 0.0 < cfg.stale_confidence < 1.0
    cfg2 = KnowledgeImproverConfig.from_env({
        "JARVIS_KSI_MIN_CARD_BULLETS": "6",
        "JARVIS_KSI_STALE_CONFIDENCE": "0.25",
    })
    assert cfg2.min_card_bullets == 6
    assert cfg2.stale_confidence == 0.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py -k gap_config -v`
Expected: FAIL con `AttributeError: ... has no attribute 'min_card_bullets'`

- [ ] **Step 3: Modify `memory/self_improvement/config.py`**

Add two fields to the dataclass (after `min_cluster_size`):
```python
    min_card_bullets: int = 4           # umbral de "card pobre"
    stale_confidence: float = 0.3       # confianza decaída bajo la cual un hecho es "obsoleto"
```

Add to the `from_env` return (inside the `cls(...)` call):
```python
            min_card_bullets=_int("JARVIS_KSI_MIN_CARD_BULLETS", d.min_card_bullets),
            stale_confidence=_float("JARVIS_KSI_STALE_CONFIDENCE", d.stale_confidence),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py -v`
Expected: PASS

- [ ] **Step 5: Update `.env.example`**

Under the existing `# --- Auto-mejora recursiva de conocimiento (KSI, Fase 1) ---` block,
add after `JARVIS_KSI_MIN_CLUSTER_SIZE=2`:
```bash
# Fase 2 — detección de lagunas:
JARVIS_KSI_MIN_CARD_BULLETS=4
JARVIS_KSI_STALE_CONFIDENCE=0.3
```

- [ ] **Step 6: Update `CHANGELOG.md`**

Under `## [Unreleased]` → `### Added`:
```markdown
- Detección de lagunas de conocimiento (KSI Fase 2): al cerrar sesión, JARVIS detecta cards
  pobres, hechos obsoletos y contradicciones abiertas, formula preguntas naturales (reasoner
  presupuestado) y las persiste en la sección "Preguntas abiertas" de cada card. El motor de
  proactividad las pregunta de forma fluida en conversación y briefing, con anti-spam (cooldown
  /dedup por gap_id) y auto-retiro determinista al responderlas. Módulo `memory/self_improvement/gaps.py`.
```

- [ ] **Step 7: Verify full suite + compile**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m py_compile jarvis.py`
Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/ -q`
Expected: solo el `test_version_is_1_02` stale falla (pre-existente, ajeno); todo lo demás verde.

- [ ] **Step 8: Commit**

```bash
git add memory/self_improvement/config.py .env.example CHANGELOG.md tests/test_ksi_config.py
git commit -m "feat(ksi): config de lagunas + .env + CHANGELOG (Fase 2 Task 7)"
```
(Termina con el trailer Co-Authored-By.)

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec:**
- §4 Parte 1 (gaps.py: KnowledgeGap, 3 detectores, gap_id) → Task 1. ✅
- §4 formulate_questions (batch, presupuestado, self-heal) → Task 2. ✅
- §4 persistencia (`## Preguntas abiertas`, dedup, auto-retiro, ksi-gap tag) → Task 3. ✅
- §4 integración en `_run_inner` (states vía build_project_states, budget compartido) → Task 4. ✅
- §5 ProjectState.open_questions (list[dict], excluye resueltas) → Task 5. ✅
- §5 `_knowledge_gap` + `_ctx_knowledge_gap` + composers → Task 6. ✅
- §5 dedup por gap_id en opportunity_id + `_WHAT_BY_KIND` → Task 6. ✅
- §6 fluidez/anti-spam (cooldown/dedup/tope reusados; prioridad contextual 0.72) → Task 6. ✅
- §7 fail-safe (todo dentro de try/except del improver y la fachada) → Tasks 4,6. ✅
- §8 testing → cada task con tests. ✅
- §9 config + .env + CHANGELOG → Task 7. ✅
- §3 "cero cambios en jarvis.py" → ningún task toca jarvis.py (solo py_compile de verificación). ✅

**Placeholder scan:** sin TODO/TBD; cada paso con código tiene bloque completo. ✅

**Consistencia de tipos:** `KnowledgeGap` (Task 1) usado en Tasks 2,3,4 con los mismos campos.
`gap_id(kind, project, key)` firma consistente. `parse_questions_section` (Task 3) reusado en
Task 5 (`_open_questions_from_body`). `open_questions: list[dict]` con claves `text`/`gap_id`
consistente entre Tasks 5 y 6. `Signal(kind="knowledge_gap", payload={"snippet","gap_id"})`
idéntico en detectores y tests. `opportunity_id` keyea `gap_id` (Task 6) — coincide con el
payload que emiten los detectores. ✅

**Nota de robustez:** los detectores leen `min_card_bullets`/`stale_confidence` vía `getattr`
con default (Task 4), de modo que Task 4 funciona aunque se ejecute antes que Task 7; los
campos formales se añaden en Task 7. Sin dependencia de orden frágil.
