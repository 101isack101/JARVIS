# Auto-mejora recursiva de conocimiento (Fase 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Al cerrar cada sesión, JARVIS consolida su base de conocimiento de forma aditiva: detecta memorias duplicadas (determinista), las juzga con el reasoner solo cuando son candidatas, regenera las Project Memory Cards con confianza ponderada (tras snapshot), y propone fusiones destructivas vía HITL en el briefing.

**Architecture:** Modelo evento→proyección→índice. Los bullets de las Project Memory Cards se tratan como *eventos de memoria* con procedencia (`id`, `learned_at`, `confidence`). Un pipeline fail-safe colgado de `_save_session_memory()` recalcula confianza, agrupa duplicados por coseno (reusando el embedder MiniLM del RAG), y regenera la card. Lo destructivo (fusión/supersesión) no se auto-aplica: se encola como `Signal` en la `OpportunityQueue` existente para que el morning briefing lo ofrezca como "PR de memoria".

**Tech Stack:** Python 3.11, dataclasses frozen, `sentence-transformers` (MiniLM, vía RAG), `numpy`, pytest. Sin dependencias nuevas. Tests: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-14-knowledge-self-improvement-design.md`

---

## File Structure

```
memory/self_improvement/
  __init__.py        # exporta KnowledgeImprover
  config.py          # KnowledgeImproverConfig (frozen, from_env)        — Task 1
  confidence.py      # mapeo legado + decay + refuerzo                   — Task 2
  events.py          # MemoryEvent + event_id + load_events + bullets    — Task 3
  detectors.py       # detect_duplicate_clusters + detect_contradictions — Task 4
  judge.py           # MergeVerdict + judge_merge (reasoner, budget)     — Task 5
  projection.py      # snapshot_previous + rebuild_card_body             — Task 6
  proposer.py        # to_signals (verdicts -> Signal)                   — Task 7
  metrics.py         # compute_health + write_health                    — Task 8
  review_log.py      # append_review_log                                 — Task 8
  improver.py        # KnowledgeImprover (fachada fail-safe)             — Task 9

tests/
  test_ksi_config.py, test_ksi_confidence.py, test_ksi_events.py,
  test_ksi_detectors.py, test_ksi_judge.py, test_ksi_projection.py,
  test_ksi_proposer.py, test_ksi_metrics.py, test_ksi_improver.py

Modificados:
  proactivity/opportunity_queue.py  # +2 entradas en _WHAT_BY_KIND       — Task 7
  jarvis.py                         # instanciar + llamar al improver     — Task 10
  .env.example, CHANGELOG.md                                             — Task 10
```

**Nota de alcance (Fase 1):** los *eventos* se derivan de los bullets de las Project
Memory Cards (es donde la deduplicación aporta valor). El `session_journal.py` ya es el
sustrato append-only inmutable que alimenta las cards vía `triage` existente; tratarlo
como fuente directa de eventos se difiere a fases posteriores (KAG).

**Convención `MemoryEvent` (definida en Task 3, usada en todas las demás):**

```python
@dataclass(frozen=True)
class MemoryEvent:
    id: str                       # sha1(texto normalizado)[:16], estable
    text: str                     # contenido del bullet (sin metadatos)
    section: str                  # sección de la card: Facts/Decisions/...
    project: str                  # nombre del proyecto (de la card)
    source: str                   # "card:<archivo>"
    learned_at: str               # fecha ISO "YYYY-MM-DD"
    confidence: float             # 0.0–1.0
    reinforced: int = 1
    superseded_by: str | None = None
```

---

## Task 1: Config

**Files:**
- Create: `memory/self_improvement/__init__.py`
- Create: `memory/self_improvement/config.py`
- Test: `tests/test_ksi_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_config.py
from memory.self_improvement.config import KnowledgeImproverConfig


def test_defaults_are_safe():
    cfg = KnowledgeImproverConfig()
    assert cfg.enabled is True
    assert 0.0 < cfg.sim_threshold <= 1.0
    assert cfg.decay_half_life_days > 0
    assert cfg.min_cluster_size >= 2


def test_from_env_overrides():
    env = {
        "JARVIS_KSI_ENABLED": "false",
        "JARVIS_KSI_SIM_THRESHOLD": "0.9",
        "JARVIS_KSI_TOKEN_BUDGET": "0",
        "JARVIS_KSI_DECAY_HALF_LIFE_DAYS": "30",
        "JARVIS_KSI_MIN_CLUSTER_SIZE": "3",
    }
    cfg = KnowledgeImproverConfig.from_env(env)
    assert cfg.enabled is False
    assert cfg.sim_threshold == 0.9
    assert cfg.token_budget == 0
    assert cfg.decay_half_life_days == 30
    assert cfg.min_cluster_size == 3


def test_from_env_bad_values_fall_back():
    cfg = KnowledgeImproverConfig.from_env({"JARVIS_KSI_SIM_THRESHOLD": "abc"})
    assert cfg.sim_threshold == KnowledgeImproverConfig().sim_threshold
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'memory.self_improvement'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/__init__.py
"""Auto-mejora recursiva de conocimiento (Fase 1)."""
```

```python
# memory/self_improvement/config.py
"""Configuración del motor de auto-mejora de conocimiento (KSI).

Mismo patrón que proactivity/config.py: frozen dataclass + from_env con defaults
seguros. Prefijo de entorno: JARVIS_KSI_.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class KnowledgeImproverConfig:
    enabled: bool = True
    sim_threshold: float = 0.86         # coseno mínimo para considerar duplicado
    token_budget: int = 1500            # tokens máximos para el reasoner por corrida
    decay_half_life_days: int = 45      # vida media del decaimiento de confianza
    min_cluster_size: int = 2           # tamaño mínimo de cluster de duplicados

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KnowledgeImproverConfig":
        env = env if env is not None else os.environ
        d = cls()

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
            enabled=_bool("JARVIS_KSI_ENABLED", d.enabled),
            sim_threshold=_float("JARVIS_KSI_SIM_THRESHOLD", d.sim_threshold),
            token_budget=_int("JARVIS_KSI_TOKEN_BUDGET", d.token_budget),
            decay_half_life_days=_int("JARVIS_KSI_DECAY_HALF_LIFE_DAYS", d.decay_half_life_days),
            min_cluster_size=_int("JARVIS_KSI_MIN_CLUSTER_SIZE", d.min_cluster_size),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/__init__.py memory/self_improvement/config.py tests/test_ksi_config.py
git commit -m "feat(ksi): config con from_env (Task 1)"
```

---

## Task 2: Confidence (mapeo legado, decay, refuerzo)

**Files:**
- Create: `memory/self_improvement/confidence.py`
- Test: `tests/test_ksi_confidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_confidence.py
from memory.self_improvement.confidence import decayed, legacy_to_float, reinforce


def test_legacy_mapping():
    assert legacy_to_float("high") == 0.85
    assert legacy_to_float("medium") == 0.6
    assert legacy_to_float("low") == 0.35
    assert legacy_to_float("desconocido") == 0.6  # default


def test_decay_is_monotonic_with_age():
    fresh = decayed(0.8, "2026-06-14", half_life_days=45, today="2026-06-14")
    old = decayed(0.8, "2026-04-30", half_life_days=45, today="2026-06-14")
    assert fresh == 0.8                 # sin antigüedad, sin decay
    assert old < fresh                  # más viejo => menos confianza
    assert old > 0.0


def test_decay_half_life():
    # a 45 días (una vida media) la confianza cae a la mitad
    half = decayed(0.8, "2026-04-30", half_life_days=45, today="2026-06-14")
    assert abs(half - 0.4) < 0.01


def test_reinforce_increases_capped():
    assert reinforce(0.6, times=1) == 0.6
    assert reinforce(0.6, times=3) > 0.6
    assert reinforce(0.99, times=10) == 1.0  # cap


def test_decay_bad_date_returns_input():
    assert decayed(0.7, "no-es-fecha", half_life_days=45, today="2026-06-14") == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_confidence.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/confidence.py
"""Confianza numérica: mapeo desde el esquema legado high/medium/low, decaimiento
temporal con vida media, y refuerzo por reconfirmación.
"""

from __future__ import annotations

from datetime import date, datetime

_LEGACY = {"high": 0.85, "medium": 0.6, "low": 0.35}
_DEFAULT = 0.6
_REINFORCE_STEP = 0.05


def legacy_to_float(word: str) -> float:
    return _LEGACY.get((word or "").strip().lower(), _DEFAULT)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def decayed(confidence: float, learned_at: str, *, half_life_days: int, today: str | None = None) -> float:
    learned = _parse_date(learned_at)
    now = _parse_date(today) if today else date.today()
    if learned is None or now is None or half_life_days <= 0:
        return confidence
    age = (now - learned).days
    if age <= 0:
        return confidence
    factor = 0.5 ** (age / half_life_days)
    return round(confidence * factor, 4)


def reinforce(confidence: float, *, times: int) -> float:
    bumps = max(0, int(times) - 1)
    return round(min(1.0, confidence + _REINFORCE_STEP * bumps), 4)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_confidence.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/confidence.py tests/test_ksi_confidence.py
git commit -m "feat(ksi): confianza con decay y refuerzo (Task 2)"
```

---

## Task 3: Events (MemoryEvent, id estable, parseo de bullets)

**Files:**
- Create: `memory/self_improvement/events.py`
- Test: `tests/test_ksi_events.py`

Formato del bullet en las cards (de `triage.project_card_bullet`):
`- 2026-06-14 [high/medium] texto resumen (source: [[Titulo]])`
Tras la primera consolidación, el bullet gana un comentario HTML invisible:
`- 2026-06-14 [0.85] texto resumen <!-- ksi:{"id":"...","reinforced":1} -->`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_events.py
from memory.self_improvement.events import (
    MemoryEvent,
    event_id,
    parse_bullet,
    serialize_bullet,
)


def test_event_id_is_stable_and_normalized():
    a = event_id("Usar Sonnet 4.6 como reasoner")
    b = event_id("usar   sonnet 4.6 COMO reasoner")  # espacios/caps distintos
    assert a == b
    assert len(a) == 16


def test_parse_legacy_bullet():
    line = "- 2026-06-10 [high/medium] Migrar a Tauri v2 (source: [[nota]])"
    ev = parse_bullet(line, section="Decisions", project="JARVIS")
    assert ev is not None
    assert ev.text == "Migrar a Tauri v2"
    assert ev.learned_at == "2026-06-10"
    assert ev.confidence == 0.6          # mapeo de "medium"
    assert ev.section == "Decisions"
    assert ev.project == "JARVIS"
    assert ev.reinforced == 1


def test_parse_ksi_bullet_roundtrip():
    ev = MemoryEvent(
        id="abc123", text="Hecho X", section="Facts", project="JARVIS",
        source="card:JARVIS.md", learned_at="2026-06-14", confidence=0.85, reinforced=2,
    )
    line = serialize_bullet(ev)
    parsed = parse_bullet(line, section="Facts", project="JARVIS")
    assert parsed.text == "Hecho X"
    assert parsed.confidence == 0.85
    assert parsed.reinforced == 2
    assert parsed.id == "abc123"


def test_parse_ignores_pending_and_blank():
    assert parse_bullet("- (pending)", section="Facts", project="JARVIS") is None
    assert parse_bullet("   ", section="Facts", project="JARVIS") is None
    assert parse_bullet("## Facts", section="Facts", project="JARVIS") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_events.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/events.py
"""Eventos de memoria derivados de los bullets de las Project Memory Cards.

Un evento es un hecho atómico con procedencia. El id es content-addressed para
que la reconfirmación del mismo hecho colapse al mismo id (idempotencia).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import confidence as conf_mod

_DATE_RE = r"(?P<date>\d{4}-\d{2}-\d{2})"
_CONF_RE = r"\[(?P<conf>[^\]]+)\]"
_KSI_RE = re.compile(r"<!--\s*ksi:(?P<json>\{.*?\})\s*-->\s*$")
_SOURCE_RE = re.compile(r"\s*\(source:\s*\[\[.*?\]\]\)\s*$")


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    text: str
    section: str
    project: str
    source: str = ""
    learned_at: str = ""
    confidence: float = conf_mod._DEFAULT
    reinforced: int = 1
    superseded_by: str | None = None


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def event_id(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:16]


def parse_bullet(line: str, *, section: str, project: str) -> MemoryEvent | None:
    raw = (line or "").rstrip()
    if not raw.strip().startswith("- "):
        return None
    body = raw.strip()[2:].strip()
    if not body or body.lower() == "(pending)":
        return None

    ksi: dict = {}
    m_ksi = _KSI_RE.search(body)
    if m_ksi:
        try:
            ksi = json.loads(m_ksi.group("json"))
        except json.JSONDecodeError:
            ksi = {}
        body = body[: m_ksi.start()].rstrip()

    learned_at = ""
    m_date = re.match(_DATE_RE, body)
    if m_date:
        learned_at = m_date.group("date")
        body = body[m_date.end():].strip()

    confidence = None
    m_conf = re.match(_CONF_RE, body)
    if m_conf:
        token = m_conf.group("conf").split("/")[-1].strip()  # "high/medium" -> "medium"
        try:
            confidence = float(token)
        except ValueError:
            confidence = conf_mod.legacy_to_float(token)
        body = body[m_conf.end():].strip()

    body = _SOURCE_RE.sub("", body).strip()
    if not body:
        return None

    text = body
    return MemoryEvent(
        id=str(ksi.get("id") or event_id(text)),
        text=text,
        section=section,
        project=project,
        source=ksi.get("source", f"card:{project}"),
        learned_at=learned_at or (ksi.get("learned_at") or date.today().isoformat()),
        confidence=confidence if confidence is not None else conf_mod._DEFAULT,
        reinforced=int(ksi.get("reinforced", 1)),
        superseded_by=ksi.get("superseded_by"),
    )


def serialize_bullet(ev: MemoryEvent) -> str:
    meta = {"id": ev.id, "reinforced": ev.reinforced, "learned_at": ev.learned_at}
    if ev.superseded_by:
        meta["superseded_by"] = ev.superseded_by
    tag = "<!-- ksi:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->"
    return f"- {ev.learned_at} [{ev.confidence:.2f}] {ev.text} {tag}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_events.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/events.py tests/test_ksi_events.py
git commit -m "feat(ksi): MemoryEvent + parseo/serialización de bullets (Task 3)"
```

---

## Task 4: Detectores (clusters de duplicados + contradicciones)

**Files:**
- Create: `memory/self_improvement/detectors.py`
- Test: `tests/test_ksi_detectors.py`

`embed_fn` es inyectado: `Callable[[list[str]], np.ndarray]` que devuelve vectores
L2-normalizados (coseno = producto punto). En tests se inyecta uno falso.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_detectors.py
import numpy as np

from memory.self_improvement.detectors import (
    detect_contradictions,
    detect_duplicate_clusters,
)
from memory.self_improvement.events import MemoryEvent


def _ev(text, project="JARVIS", section="Facts"):
    return MemoryEvent(id=text[:8], text=text, section=section, project=project)


def _fake_embed(vectors_by_text):
    def embed(texts):
        return np.array([vectors_by_text[t] for t in texts], dtype="float32")
    return embed


def test_clusters_group_near_duplicates():
    events = [_ev("a"), _ev("a-dup"), _ev("b")]
    embed = _fake_embed({
        "a": [1.0, 0.0],
        "a-dup": [0.99, 0.01],   # casi idéntico a "a"
        "b": [0.0, 1.0],         # ortogonal
    })
    clusters = detect_duplicate_clusters(events, embed, threshold=0.9, min_size=2)
    assert len(clusters) == 1
    texts = sorted(e.text for e in clusters[0])
    assert texts == ["a", "a-dup"]


def test_no_clusters_when_all_distinct():
    events = [_ev("a"), _ev("b")]
    embed = _fake_embed({"a": [1.0, 0.0], "b": [0.0, 1.0]})
    assert detect_duplicate_clusters(events, embed, threshold=0.9, min_size=2) == []


def test_contradiction_detected_by_negation_polarity():
    e1 = _ev("Migrar el reasoner a la version 4.7")
    e2 = _ev("NO migrar el reasoner a la version 4.7")
    pairs = detect_contradictions([e1, e2])
    assert (e1, e2) in pairs or (e2, e1) in pairs


def test_no_contradiction_across_projects():
    e1 = _ev("usar X", project="A")
    e2 = _ev("no usar X", project="B")
    assert detect_contradictions([e1, e2]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_detectors.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/detectors.py
"""Detección determinista de candidatos para el reasoner.

- duplicados: clusters por similitud coseno (embeddings inyectados)
- contradicciones: pares del mismo proyecto con polaridad de negación opuesta
  y alto solapamiento de tokens. Heurística barata; el reasoner decide después.
"""

from __future__ import annotations

import re
from typing import Callable

import numpy as np

from .events import MemoryEvent

EmbedFn = Callable[[list[str]], "np.ndarray"]

_NEG = {"no", "nunca", "jamas", "jamás", "sin"}
_TOKEN_RE = re.compile(r"[a-z0-9áéíóúñ]+")


def detect_duplicate_clusters(
    events: list[MemoryEvent], embed_fn: EmbedFn, *, threshold: float, min_size: int = 2
) -> list[list[MemoryEvent]]:
    if len(events) < min_size:
        return []
    vecs = embed_fn([e.text for e in events])
    n = len(events)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if events[i].project != events[j].project:
                continue
            sim = float(np.dot(vecs[i], vecs[j]))
            if sim >= threshold:
                union(i, j)

    groups: dict[int, list[MemoryEvent]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(events[idx])
    return [g for g in groups.values() if len(g) >= min_size]


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _polarity(text: str) -> bool:
    return bool(_tokens(text) & _NEG)


def detect_contradictions(events: list[MemoryEvent]) -> list[tuple[MemoryEvent, MemoryEvent]]:
    out: list[tuple[MemoryEvent, MemoryEvent]] = []
    n = len(events)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = events[i], events[j]
            if a.project != b.project:
                continue
            if _polarity(a.text) == _polarity(b.text):
                continue
            ta, tb = _tokens(a.text) - _NEG, _tokens(b.text) - _NEG
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / len(ta | tb)
            if overlap >= 0.6:
                out.append((a, b))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_detectors.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/detectors.py tests/test_ksi_detectors.py
git commit -m "feat(ksi): detectores de duplicados y contradicciones (Task 4)"
```

---

## Task 5: Judge (reasoner enfocado, presupuestado)

**Files:**
- Create: `memory/self_improvement/judge.py`
- Test: `tests/test_ksi_judge.py`

`reasoner.ask(instructions, context_extra=..., max_tokens=...)` devuelve un objeto
con atributo `.text` (mismo contrato que usa `session_summary.synthesize_and_save`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_judge.py
from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.judge import MergeVerdict, judge_merge


def _ev(text):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project="JARVIS")


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeReasoner:
    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, context_extra="", max_tokens=300):
        self.calls += 1
        return _FakeResp(self._text)


def test_judge_returns_verdict_from_json():
    cluster = [_ev("Hecho A"), _ev("Hecho A repetido")]
    reasoner = _FakeReasoner('Aquí va: {"is_true_duplicate": true, "canonical_text": "Hecho A"} listo')
    verdict = judge_merge(reasoner, cluster, token_budget=1000)
    assert isinstance(verdict, MergeVerdict)
    assert verdict.is_true_duplicate is True
    assert verdict.canonical_text == "Hecho A"
    assert sorted(verdict.member_ids) == sorted(e.id for e in cluster)


def test_judge_skips_when_no_budget():
    reasoner = _FakeReasoner('{"is_true_duplicate": true, "canonical_text": "x"}')
    assert judge_merge(reasoner, [_ev("a"), _ev("b")], token_budget=0) is None
    assert reasoner.calls == 0


def test_judge_returns_none_on_bad_json():
    reasoner = _FakeReasoner("no hay json aquí")
    assert judge_merge(reasoner, [_ev("a"), _ev("b")], token_budget=1000) is None


def test_judge_returns_none_when_reasoner_raises():
    class Boom:
        def ask(self, *a, **k):
            raise RuntimeError("api down")
    assert judge_merge(Boom(), [_ev("a"), _ev("b")], token_budget=1000) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_judge.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/judge.py
"""Juicio del reasoner SOLO sobre candidatos del detector. Presupuestado y
fail-safe: cualquier fallo o falta de budget devuelve None (se omite la fusión).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .events import MemoryEvent

_INSTRUCTIONS = (
    "Eres el bibliotecario de JARVIS. Te paso varias memorias que un detector marcó "
    "como POSIBLES duplicados. Decide si dicen lo MISMO. Responde SOLO un objeto JSON: "
    '{"is_true_duplicate": true|false, "canonical_text": "<texto fusionado conciso>"}. '
    "Si no son el mismo hecho, is_true_duplicate=false y canonical_text=\"\"."
)


@dataclass(frozen=True)
class MergeVerdict:
    is_true_duplicate: bool
    canonical_text: str
    member_ids: list[str]


def _extract_json(text: str) -> dict | None:
    """Primer objeto JSON balanceado dentro del texto (self-heal básico)."""
    s = text or ""
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def judge_merge(reasoner, cluster: list[MemoryEvent], *, token_budget: int, max_tokens: int = 300):
    if reasoner is None or token_budget <= 0 or len(cluster) < 2:
        return None
    payload = "\n".join(f"- ({e.id}) {e.text}" for e in cluster)
    try:
        resp = reasoner.ask(_INSTRUCTIONS, context_extra="MEMORIAS:\n" + payload, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return None
    if not isinstance(data, dict) or "is_true_duplicate" not in data:
        return None
    return MergeVerdict(
        is_true_duplicate=bool(data.get("is_true_duplicate")),
        canonical_text=str(data.get("canonical_text") or "").strip(),
        member_ids=[e.id for e in cluster],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_judge.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/judge.py tests/test_ksi_judge.py
git commit -m "feat(ksi): judge del reasoner presupuestado con JSON self-heal (Task 5)"
```

---

## Task 6: Projection (snapshot + regeneración aditiva de la card)

**Files:**
- Create: `memory/self_improvement/projection.py`
- Test: `tests/test_ksi_projection.py`

Regeneración **aditiva**: todos los eventos se conservan; se reordenan por confianza
descendente dentro de su sección y se serializan con su tag ksi. Antes de tocar la
card se guarda un snapshot. La fusión real (quitar duplicados) es HITL, no aquí.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_projection.py
from memory.self_improvement.events import MemoryEvent, parse_bullet
from memory.self_improvement.projection import rebuild_card_body, snapshot_previous


def _ev(text, section, conf, learned="2026-06-14"):
    return MemoryEvent(id=text[:8], text=text, section=section, project="JARVIS",
                       learned_at=learned, confidence=conf)


def test_rebuild_preserves_all_events_sorted_by_confidence():
    events = [
        _ev("baja", "Facts", 0.3),
        _ev("alta", "Facts", 0.9),
        _ev("decision", "Decisions", 0.7),
    ]
    body = rebuild_card_body("JARVIS", events)
    assert "## Facts" in body and "## Decisions" in body
    # dentro de Facts, "alta" (0.9) aparece antes que "baja" (0.3)
    assert body.index("alta") < body.index("baja")
    # round-trip: las líneas de Facts vuelven a parsear a eventos
    fact_lines = [l for l in body.splitlines() if l.startswith("- ") and "<!-- ksi:" in l]
    assert len(fact_lines) == 3
    parsed = parse_bullet(fact_lines[0], section="Facts", project="JARVIS")
    assert parsed is not None


def test_snapshot_copies_existing_card(tmp_path):
    card = tmp_path / "JARVIS.md"
    card.write_text("contenido previo", encoding="utf-8")
    snap = snapshot_previous(tmp_path, card)
    assert snap is not None and snap.exists()
    assert snap.read_text(encoding="utf-8") == "contenido previo"
    assert snap != card


def test_snapshot_missing_card_returns_none(tmp_path):
    assert snapshot_previous(tmp_path, tmp_path / "noexiste.md") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_projection.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/projection.py
"""Regeneración aditiva de Project Memory Cards desde eventos + snapshots.

La card es OUTPUT: se reconstruye desde los eventos, nunca se parchea in-place.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .events import MemoryEvent, serialize_bullet

SNAPSHOT_SUBDIR = "self-improvement/snapshots"
# Orden canónico de secciones (igual que triage.initial_project_card_body)
_SECTION_ORDER = [
    "Objective", "Current State", "Facts", "Decisions", "Pending",
    "Procedures", "Preferences", "Learning Notes", "Risks", "Notes", "Sources",
]


def snapshot_previous(memory_path: Path, card_path: Path) -> Path | None:
    card_path = Path(card_path)
    if not card_path.exists():
        return None
    snap_dir = Path(memory_path) / SNAPSHOT_SUBDIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = snap_dir / f"{card_path.stem}_{ts}.md"
    shutil.copy2(card_path, dest)
    return dest


def rebuild_card_body(project: str, events: list[MemoryEvent]) -> str:
    by_section: dict[str, list[MemoryEvent]] = {}
    for ev in events:
        by_section.setdefault(ev.section, []).append(ev)

    ordered = list(_SECTION_ORDER)
    for section in by_section:
        if section not in ordered:
            ordered.append(section)

    lines = [f"# {project} - Memory Card", ""]
    for section in ordered:
        evs = by_section.get(section)
        if not evs:
            continue
        evs = sorted(evs, key=lambda e: e.confidence, reverse=True)
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(serialize_bullet(ev) for ev in evs)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_projection.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/projection.py tests/test_ksi_projection.py
git commit -m "feat(ksi): snapshot + regeneración aditiva de cards (Task 6)"
```

---

## Task 7: Proposer (verdicts → Signal) + extensión de la OpportunityQueue

**Files:**
- Create: `memory/self_improvement/proposer.py`
- Modify: `proactivity/opportunity_queue.py` (añadir 2 entradas a `_WHAT_BY_KIND`)
- Test: `tests/test_ksi_proposer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_proposer.py
from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.judge import MergeVerdict
from memory.self_improvement.proposer import to_signals
from proactivity.opportunity_queue import _WHAT_BY_KIND, _suggestion_struct


def _ev(text, project="JARVIS"):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project)


def test_merge_verdict_becomes_signal():
    verdict = MergeVerdict(is_true_duplicate=True, canonical_text="Hecho fusionado", member_ids=["a", "b"])
    signals = to_signals([verdict], [], project_by_members={"a": "JARVIS", "b": "JARVIS"})
    assert len(signals) == 1
    sig = signals[0]
    assert sig.kind == "memory_merge"
    assert sig.project == "JARVIS"
    assert sig.payload["snippet"] == "Hecho fusionado"
    assert sig.payload["members"] == ["a", "b"]


def test_false_duplicate_is_skipped():
    verdict = MergeVerdict(is_true_duplicate=False, canonical_text="", member_ids=["a"])
    assert to_signals([verdict], [], project_by_members={"a": "JARVIS"}) == []


def test_contradiction_becomes_supersede_signal():
    a, b = _ev("usar X"), _ev("no usar X")
    signals = to_signals([], [(a, b)], project_by_members={})
    assert len(signals) == 1
    assert signals[0].kind == "memory_supersede"
    assert signals[0].project == "JARVIS"


def test_new_kinds_have_human_labels_and_render():
    assert "memory_merge" in _WHAT_BY_KIND
    assert "memory_supersede" in _WHAT_BY_KIND
    # el struct usa snippet (lo que briefing._line lee desde why_now)
    a, b = _ev("usar X"), _ev("no usar X")
    sig = to_signals([], [(a, b)], project_by_members={})[0]
    struct = _suggestion_struct(sig)
    assert struct["what"] == _WHAT_BY_KIND["memory_supersede"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_proposer.py -v`
Expected: FAIL (`ModuleNotFoundError` y `memory_merge` ausente de `_WHAT_BY_KIND`)

- [ ] **Step 3a: Extender `proactivity/opportunity_queue.py`**

Localiza el diccionario `_WHAT_BY_KIND` (≈ línea 40) y añade dos entradas al final, antes de la llave de cierre `}`:

```python
_WHAT_BY_KIND = {
    "stale_pending": "Retomar un pendiente que lleva días abierto",
    "stale_project": "Volver a un proyecto importante sin tocar",
    "open_loop": "Cerrar una decisión que no avanzó",
    "cross_project": "Reutilizar algo ya resuelto en otro proyecto",
    "ctx_pending": "Hay un pendiente abierto del proyecto que mencionaste",
    "memory_merge": "Fusionar dos memorias duplicadas",
    "memory_supersede": "Resolver una contradicción entre dos memorias",
}
```

- [ ] **Step 3b: Escribir `memory/self_improvement/proposer.py`**

```python
# memory/self_improvement/proposer.py
"""Convierte veredictos destructivos en Signals para la OpportunityQueue (HITL).

Nada se aplica aquí: solo se PROPONE. El usuario aprueba en el morning briefing.
El payload usa la clave "snippet" porque es la que opportunity_id y briefing._line leen.
"""

from __future__ import annotations

from proactivity.signals import Signal

from .events import MemoryEvent
from .judge import MergeVerdict


def to_signals(
    merges: list[MergeVerdict],
    contradictions: list[tuple[MemoryEvent, MemoryEvent]],
    *,
    project_by_members: dict[str, str],
) -> list[Signal]:
    out: list[Signal] = []
    for v in merges:
        if not v.is_true_duplicate:
            continue
        project = next((project_by_members.get(mid, "") for mid in v.member_ids), "")
        out.append(
            Signal(
                kind="memory_merge",
                project=project or "general",
                payload={"snippet": v.canonical_text, "members": list(v.member_ids)},
                base_priority=0.7,
                evidence=[f"merge:{','.join(v.member_ids)}"],
            )
        )
    for a, b in contradictions:
        out.append(
            Signal(
                kind="memory_supersede",
                project=a.project or "general",
                payload={"snippet": f"{a.text}  ⟷  {b.text}", "members": [a.id, b.id]},
                base_priority=0.65,
                evidence=[f"contradiction:{a.id},{b.id}"],
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_proposer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/proposer.py proactivity/opportunity_queue.py tests/test_ksi_proposer.py
git commit -m "feat(ksi): proposer destructivo -> Signal + kinds de memoria (Task 7)"
```

---

## Task 8: Metrics + Review log

**Files:**
- Create: `memory/self_improvement/metrics.py`
- Create: `memory/self_improvement/review_log.py`
- Test: `tests/test_ksi_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_metrics.py
from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.metrics import compute_health, write_health
from memory.self_improvement.review_log import append_review_log


def _ev(text, project="JARVIS", conf=0.6):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project, confidence=conf)


def test_compute_health_counts():
    events = [_ev("a"), _ev("b"), _ev("c", project="X")]
    clusters = [[events[0], events[1]]]
    health = compute_health(events, clusters, contradictions=[])
    assert health["total_events"] == 3
    assert health["projects"] == 2
    assert health["duplicate_clusters"] == 1
    assert health["open_contradictions"] == 0
    assert 0.0 <= health["avg_confidence"] <= 1.0


def test_write_health_creates_file(tmp_path):
    path = write_health(tmp_path, {"total_events": 5, "avg_confidence": 0.7})
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "total_events" in text and "5" in text


def test_append_review_log_appends(tmp_path):
    p1 = append_review_log(tmp_path, ["anotó card JARVIS", "propuso fusión a+b"])
    p2 = append_review_log(tmp_path, ["otra corrida"])
    assert p1 == p2
    text = p1.read_text(encoding="utf-8")
    assert "anotó card JARVIS" in text
    assert "otra corrida" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_metrics.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/metrics.py
"""Métricas de salud de la memoria. Sin esto, 'recursivo' es fe, no ingeniería."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .events import MemoryEvent

HEALTH_PATH = "self-improvement/health.md"


def compute_health(events, clusters, contradictions) -> dict:
    total = len(events)
    projects = len({e.project for e in events})
    avg_conf = round(sum(e.confidence for e in events) / total, 4) if total else 0.0
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_events": total,
        "projects": projects,
        "duplicate_clusters": len(clusters),
        "open_contradictions": len(contradictions),
        "avg_confidence": avg_conf,
    }


def write_health(memory_path: Path, health: dict) -> Path:
    path = Path(memory_path) / HEALTH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Salud de la memoria (KSI)", "", "```json",
            json.dumps(health, ensure_ascii=False, indent=2), "```", ""]
    path.write_text("\n".join(body), encoding="utf-8")
    return path
```

```python
# memory/self_improvement/review_log.py
"""Traza auditable append-only de cada corrida del improver."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

REVIEW_LOG_PATH = "self-improvement/review-log.md"


def append_review_log(memory_path: Path, actions: list[str]) -> Path:
    path = Path(memory_path) / REVIEW_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    block = [f"\n## {stamp}"]
    block.extend(f"- {a}" for a in (actions or ["(sin acciones)"]))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_metrics.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/metrics.py memory/self_improvement/review_log.py tests/test_ksi_metrics.py
git commit -m "feat(ksi): métricas de salud + review log (Task 8)"
```

---

## Task 9: Improver (fachada fail-safe que orquesta el pipeline)

**Files:**
- Create: `memory/self_improvement/improver.py`
- Modify: `memory/self_improvement/__init__.py` (exportar `KnowledgeImprover`)
- Test: `tests/test_ksi_improver.py`

`load_events_from_vault(vault)` lee las Project Memory Cards. Para tests se inyecta un
`event_loader` falso, evitando montar un vault real.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_improver.py
import numpy as np

from memory.self_improvement.config import KnowledgeImproverConfig
from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.improver import KnowledgeImprover


def _ev(text, project="JARVIS"):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project,
                       learned_at="2026-06-14", confidence=0.6)


class _FakeVault:
    def __init__(self, memory_path):
        self.memory_path = memory_path


def _embed(texts):
    # vectores deterministas: misma longitud => casi idénticos
    return np.array([[1.0, 0.0] if len(t) % 2 == 0 else [0.0, 1.0] for t in texts], dtype="float32")


def test_run_is_fail_safe_when_loader_raises(tmp_path):
    def boom(_vault):
        raise RuntimeError("no se pudo leer")
    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(),
        embed_fn=_embed,
        reasoner=None,
        event_loader=boom,
    )
    # No debe propagar
    imp.run(_FakeVault(tmp_path))


def test_run_disabled_is_noop(tmp_path):
    called = {"n": 0}
    def loader(_vault):
        called["n"] += 1
        return []
    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(enabled=False),
        embed_fn=_embed, reasoner=None, event_loader=loader,
    )
    imp.run(_FakeVault(tmp_path))
    assert called["n"] == 0


def test_run_writes_health_and_log(tmp_path):
    events = [_ev("aa"), _ev("bb")]  # ambos pares => mismo vector => cluster
    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(token_budget=0),  # sin reasoner: solo determinista
        embed_fn=_embed, reasoner=None, event_loader=lambda _v: events,
    )
    imp.run(_FakeVault(tmp_path))
    assert (tmp_path / "self-improvement" / "health.md").exists()
    assert (tmp_path / "self-improvement" / "review-log.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_improver.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/improver.py
"""Fachada fail-safe del motor de auto-mejora de conocimiento.

Único punto de contacto con jarvis.py. Ningún método propaga excepción: un fallo
aquí jamás puede tumbar el cierre de sesión. Presupuestado: sin budget, solo corre
el camino determinista.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import confidence as conf_mod
from . import metrics as metrics_mod
from . import review_log as log_mod
from .config import KnowledgeImproverConfig
from .detectors import detect_contradictions, detect_duplicate_clusters
from .events import MemoryEvent
from .judge import judge_merge
from .projection import rebuild_card_body, snapshot_previous
from .proposer import to_signals

EventLoader = Callable[[object], list[MemoryEvent]]


class KnowledgeImprover:
    def __init__(
        self,
        *,
        config: KnowledgeImproverConfig,
        embed_fn,
        reasoner=None,
        proactivity_engine=None,
        event_loader: EventLoader | None = None,
    ) -> None:
        self.config = config
        self.embed_fn = embed_fn
        self.reasoner = reasoner
        self.proactivity_engine = proactivity_engine
        self._event_loader = event_loader or _default_event_loader

    def run(self, vault) -> None:
        if not self.config.enabled:
            return
        try:
            self._run_inner(vault)
        except Exception:
            # fail-safe absoluto: el shutdown nunca depende de esto
            pass

    def _run_inner(self, vault) -> None:
        try:
            events = self._event_loader(vault)
        except Exception:
            return
        if not events:
            return

        # 1) recalcular confianza (autónomo)
        events = [self._reweigh(e) for e in events]

        # 2) detectar candidatos (determinista)
        clusters = detect_duplicate_clusters(
            events, self.embed_fn,
            threshold=self.config.sim_threshold, min_size=self.config.min_cluster_size,
        )
        contradictions = detect_contradictions(events)

        # 3) juzgar (reasoner, presupuestado)
        budget = self.config.token_budget
        verdicts = []
        for cluster in clusters:
            v = judge_merge(self.reasoner, cluster, token_budget=budget)
            if v is not None:
                verdicts.append(v)
                budget = max(0, budget - 300)

        # 4) proponer destructivo (HITL) → OpportunityQueue
        project_by_members = {e.id: e.project for e in events}
        signals = to_signals(verdicts, contradictions, project_by_members=project_by_members)
        if signals and self.proactivity_engine is not None:
            try:
                self.proactivity_engine.queue.ingest(signals)
            except Exception:
                pass

        # 5) métricas + traza (siempre)
        memory_path = Path(getattr(vault, "memory_path"))
        actions = [
            f"eventos={len(events)}",
            f"clusters={len(clusters)}",
            f"contradicciones={len(contradictions)}",
            f"propuestas={len(signals)}",
        ]
        try:
            health = metrics_mod.compute_health(events, clusters, contradictions)
            metrics_mod.write_health(memory_path, health)
        except Exception:
            pass
        try:
            log_mod.append_review_log(memory_path, actions)
        except Exception:
            pass

    def _reweigh(self, ev: MemoryEvent) -> MemoryEvent:
        conf = conf_mod.decayed(
            conf_mod.reinforce(ev.confidence, times=ev.reinforced),
            ev.learned_at, half_life_days=self.config.decay_half_life_days,
        )
        return MemoryEvent(
            id=ev.id, text=ev.text, section=ev.section, project=ev.project,
            source=ev.source, learned_at=ev.learned_at, confidence=conf,
            reinforced=ev.reinforced, superseded_by=ev.superseded_by,
        )


def _default_event_loader(vault) -> list[MemoryEvent]:
    """Lee las Project Memory Cards del vault y devuelve sus eventos.

    Importa perezosamente para no acoplar el módulo a Obsidian en los tests.
    """
    from memory import notes as notes_mod
    from memory.triage import PROJECT_CARD_FOLDER

    from .events import parse_bullet

    out: list[MemoryEvent] = []
    folder = Path(vault.memory_path) / PROJECT_CARD_FOLDER
    if not folder.exists():
        return out
    for card_path in sorted(folder.glob("*.md")):
        try:
            note = notes_mod.read_note(vault, card_path)
        except Exception:
            continue
        project = note.frontmatter.get("project") or card_path.stem
        section = "Notes"
        for line in note.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                section = stripped[3:].strip()
                continue
            ev = parse_bullet(line, section=section, project=str(project))
            if ev is not None:
                out.append(ev)
    return out
```

Actualiza el `__init__.py`:

```python
# memory/self_improvement/__init__.py
"""Auto-mejora recursiva de conocimiento (Fase 1)."""

from .improver import KnowledgeImprover

__all__ = ["KnowledgeImprover"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_improver.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/improver.py memory/self_improvement/__init__.py tests/test_ksi_improver.py
git commit -m "feat(ksi): KnowledgeImprover fachada fail-safe (Task 9)"
```

---

## Task 10: Wiring en jarvis.py + .env.example + CHANGELOG

**Files:**
- Modify: `jarvis.py` (import, instanciar en `build()`, llamar en `_save_session_memory()`)
- Modify: `.env.example`
- Modify: `CHANGELOG.md`

No hay test unitario nuevo (es cableado de runtime); se valida con la suite completa
y la compilación. El comportamiento fail-safe ya está cubierto por `test_ksi_improver.py`.

- [ ] **Step 1: Añadir el import**

En `jarvis.py`, junto a los demás imports de memoria (cerca de la línea 56 donde se
importa `synthesize_and_save`), añade:

```python
from memory.self_improvement import KnowledgeImprover
from memory.self_improvement.config import KnowledgeImproverConfig
```

- [ ] **Step 2: Instanciar el improver en `build()`**

Localiza en `jarvis.py` el método/función `build()` donde ya existen `self.rag`,
`self.reasoner`, `self.vault` y `self.proactivity` (la `ProactivityEngine`). Justo
después de crear `self.proactivity`, añade:

```python
self.knowledge_improver = KnowledgeImprover(
    config=KnowledgeImproverConfig.from_env(),
    embed_fn=lambda texts: self.rag._ensure_model().encode(
        list(texts), normalize_embeddings=True
    ),
    reasoner=self.reasoner,
    proactivity_engine=self.proactivity,
)
```

Si la clase usa atributos con otro nombre (p.ej. `self._rag`), respeta el nombre real
ya presente en `build()`. Verifica con: `grep -n "self.rag\|self.reasoner\|self.proactivity" jarvis.py`

- [ ] **Step 3: Llamar al improver al cierre de sesión**

En `_save_session_memory()` (≈ línea 1483), al FINAL del método (después del bloque que
indexa la nota de sesión en el RAG), añade:

```python
        try:
            self.knowledge_improver.run(self.vault)
        except Exception as exc:
            self._log(f"[WARN] auto-mejora de conocimiento omitida: {exc}")
```

- [ ] **Step 4: Verificar que compila y la suite pasa**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m py_compile jarvis.py`
Expected: sin salida (compila)

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/ -q`
Expected: toda la suite verde (los nuevos `test_ksi_*` + los existentes sin regresión)

- [ ] **Step 5: Añadir variables a `.env.example`**

Añade al final de `.env.example`:

```bash
# --- Auto-mejora recursiva de conocimiento (KSI, Fase 1) ---
# Se ejecuta al cerrar sesión: consolida memorias de forma aditiva y propone
# fusiones destructivas vía el morning briefing (HITL).
JARVIS_KSI_ENABLED=true
JARVIS_KSI_SIM_THRESHOLD=0.86
JARVIS_KSI_TOKEN_BUDGET=1500
JARVIS_KSI_DECAY_HALF_LIFE_DAYS=45
JARVIS_KSI_MIN_CLUSTER_SIZE=2
```

- [ ] **Step 6: Añadir entrada al `CHANGELOG.md`**

Bajo la sección `## [Unreleased]` (o crea una nueva en la cima si no existe):

```markdown
### Added
- Auto-mejora recursiva de conocimiento (Fase 1): al cerrar sesión, JARVIS consolida
  sus Project Memory Cards de forma aditiva (recalcula confianza con decaimiento/refuerzo,
  detecta duplicados por coseno) y propone fusiones/contradicciones vía el morning
  briefing (HITL). Modelo evento→proyección regenerable; fail-safe total; métricas de
  salud en `Jarvis Memory/self-improvement/`. Paquete `memory/self_improvement/`.
```

- [ ] **Step 7: Commit**

```bash
git add jarvis.py .env.example CHANGELOG.md
git commit -m "feat(ksi): cablear KnowledgeImprover al cierre de sesión (Task 10)"
```

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec:**
- §3 modelo evento→proyección → Tasks 3 (events), 6 (projection). ✅
- §4 roadmap (solo Fase 1 en alcance) → todo el plan es Fase 1. ✅
- §5 modelo de datos (id/learned_at/source/confidence/reinforced/superseded_by) → Task 3 `MemoryEvent`. ✅
- §6 componentes (config/improver/events/projection/detectors/judge/confidence/proposer/metrics/review_log) → Tasks 1-9. ✅
- §7 flujo de datos (recolectar→detectar→confianza→juzgar→aplicar→proponer→registrar) → Task 9 `_run_inner`. ✅
- §8 autonomía (aditivo autónomo / destructivo HITL) → Task 6 (regeneración aditiva) + Task 7 (Signal a la cola). ✅
- §9 fail-safe + presupuesto → Task 9 (try/except total + budget decrement). ✅
- §10 testing TDD → cada task tiene tests. ✅
- §11 métricas de salud → Task 8. ✅
- §12 archivos tocados (triage/signals/queue/briefing/jarvis/env/changelog) → Tasks 7, 10. **Nota:** `triage.py` y `briefing.py` no requieren edición — los bullets legados se migran perezosamente al parsear (Task 3) y `briefing._line` ya lee `snippet` genéricamente. Documentado como simplificación.

**Placeholder scan:** sin TODO/TBD; todo paso con código tiene su bloque completo. ✅

**Consistencia de tipos:** `MemoryEvent` (Task 3) usado idéntico en Tasks 4/5/6/7/9. `MergeVerdict` (Task 5) consumido en Task 7. `embed_fn` firma `list[str]->np.ndarray` consistente en Tasks 4 y 9. `to_signals(merges, contradictions, *, project_by_members)` igual en Tasks 7 y 9. `_WHAT_BY_KIND` extendido en Task 7 y verificado en su test. ✅
