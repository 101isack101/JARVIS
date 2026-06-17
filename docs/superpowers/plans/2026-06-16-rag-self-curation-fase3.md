# RAG Auto-curado (KSI Fase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar el lazo de utilidad del RAG: medir qué chunks usa de verdad el reasoner y re-rankear las recuperaciones futuras de forma autónoma y no destructiva.

**Architecture:** Nuevo módulo `memory/self_improvement/retrieval_curation.py` con `RetrievalCurator`, que persiste stats de uso por `chunk_key` content-addressed en `data/rag_usage.json`. Engancha en dos seams calientes (`build_project_context` para rerank+note, `ask_claude_deep`/`_async` para atribución) y hace housekeeping al cierre KSI. Fail-safe en cada seam: nunca degrada la respuesta de voz.

**Tech Stack:** Python 3.11, numpy, sentence-transformers MiniLM (reusa el del RAG), pytest. TDD, sin tocar en rojo el path de voz.

---

## File Structure

- **Create** `memory/self_improvement/retrieval_curation.py` — `RetrievalCurator` (chunk_key, quality_factor, rerank, note_retrieval, attribute_usage, housekeeping, persistencia atómica). Única pieza nueva.
- **Modify** `memory/self_improvement/config.py` — 6 campos nuevos en `KnowledgeImproverConfig` + `from_env`.
- **Modify** `memory/context_assembler.py` — `build_project_context(..., curator=None)`: rerank + note_retrieval tras el search.
- **Modify** `memory/tools.py` — `ToolContext.retrieval_curator`; `_augmented_context` pasa el curator; `ask_claude_deep`/`ask_claude_deep_async` atribuyen uso tras la respuesta.
- **Modify** `memory/self_improvement/improver.py` — `KnowledgeImprover` recibe `retrieval_curator` + `chunk_keys_provider`; housekeeping en `_run_inner`.
- **Modify** `jarvis.py` — instancia el curator (gated por `JARVIS_RAG_CURATION`), lo pasa a `ToolContext` y a `KnowledgeImprover`.
- **Modify** `.env.example`, `CHANGELOG.md` — documentación.
- **Create** `tests/test_retrieval_curation.py` — unidad del curator.
- **Modify** `tests/test_ask_claude_deep_context.py` — wiring de atribución.

**Test runner (Git Bash, Windows):** `PYTHONUTF8=1 /h/Python311/python.exe -m pytest`

---

## Task 1: Config — campos de curación

**Files:**
- Modify: `memory/self_improvement/config.py`
- Test: `tests/test_retrieval_curation.py` (nuevo)

- [ ] **Step 1: Write the failing test**

Create `tests/test_retrieval_curation.py`:

```python
from memory.self_improvement.config import KnowledgeImproverConfig


def test_curation_defaults():
    c = KnowledgeImproverConfig()
    assert c.rag_curation_enabled is False
    assert c.use_threshold == 0.55
    assert c.cold_start_min == 5
    assert c.factor_floor == 0.6
    assert c.factor_ceil == 1.4
    assert c.usage_decay_days == 45


def test_curation_from_env_reads_gate():
    c = KnowledgeImproverConfig.from_env({
        "JARVIS_RAG_CURATION": "true",
        "JARVIS_KSI_USE_THRESHOLD": "0.7",
        "JARVIS_KSI_COLD_START_MIN": "3",
    })
    assert c.rag_curation_enabled is True
    assert c.use_threshold == 0.7
    assert c.cold_start_min == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -v`
Expected: FAIL (`AttributeError: ... 'rag_curation_enabled'`).

- [ ] **Step 3: Add the fields**

In `memory/self_improvement/config.py`, add to the dataclass (after `stale_confidence`):

```python
    rag_curation_enabled: bool = False  # re-ranking del RAG por uso real (gate JARVIS_RAG_CURATION)
    use_threshold: float = 0.55         # coseno minimo respuesta<->chunk para contar "usado"
    cold_start_min: int = 5             # recuperaciones minimas antes de salir de factor neutral
    factor_floor: float = 0.6           # multiplicador minimo del score en rerank
    factor_ceil: float = 1.4            # multiplicador maximo del score en rerank
    usage_decay_days: int = 45          # vida media para decaer cuentas en housekeeping
```

And in `from_env`, add to the `return cls(...)` call (before the closing paren):

```python
            rag_curation_enabled=_bool("JARVIS_RAG_CURATION", d.rag_curation_enabled),
            use_threshold=_float("JARVIS_KSI_USE_THRESHOLD", d.use_threshold),
            cold_start_min=_int("JARVIS_KSI_COLD_START_MIN", d.cold_start_min),
            factor_floor=_float("JARVIS_KSI_FACTOR_FLOOR", d.factor_floor),
            factor_ceil=_float("JARVIS_KSI_FACTOR_CEIL", d.factor_ceil),
            usage_decay_days=_int("JARVIS_KSI_USAGE_DECAY_DAYS", d.usage_decay_days),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/config.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): config de curacion de RAG (Fase 3 Task 1)"
```

---

## Task 2: chunk_key estable + quality_factor

**Files:**
- Modify: `memory/self_improvement/retrieval_curation.py`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
from memory.self_improvement.retrieval_curation import RetrievalCurator


def _curator(tmp_path, **over):
    cfg = KnowledgeImproverConfig(**over)
    return RetrievalCurator(
        config=cfg,
        embed_fn=lambda texts: __import__("numpy").zeros((len(texts), 2), dtype="float32"),
        state_path=tmp_path / "rag_usage.json",
    )


def test_chunk_key_stable_across_text_and_path():
    k1 = RetrievalCurator.chunk_key("a/b.md", "Hola   mundo")
    k2 = RetrievalCurator.chunk_key("a/b.md", "Hola mundo")   # whitespace colapsa
    k3 = RetrievalCurator.chunk_key("a/b.md", "Otro texto")
    k4 = RetrievalCurator.chunk_key("a/c.md", "Hola mundo")   # otra ruta
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4
    assert len(k1) == 16


def test_quality_factor_cold_start_is_neutral(tmp_path):
    cur = _curator(tmp_path, cold_start_min=5)
    cur._chunks["x"] = {"retrieved": 4, "used": 0, "last_used": None, "last_touch": None}
    assert cur.quality_factor("x") == 1.0          # < cold_start_min
    assert cur.quality_factor("missing") == 1.0     # desconocido


def test_quality_factor_linear(tmp_path):
    cur = _curator(tmp_path, cold_start_min=5, factor_floor=0.6, factor_ceil=1.4)
    cases = {0.0: 0.6, 0.25: 0.8, 0.5: 1.0, 0.75: 1.2, 1.0: 1.4}
    for i, (rate, expected) in enumerate(cases.items()):
        retrieved = 8
        cur._chunks[f"k{i}"] = {
            "retrieved": retrieved, "used": rate * retrieved,
            "last_used": None, "last_touch": None,
        }
        assert abs(cur.quality_factor(f"k{i}") - expected) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -v`
Expected: FAIL (`ModuleNotFoundError` / class not defined).

- [ ] **Step 3: Create the module skeleton + chunk_key + quality_factor**

Create `memory/self_improvement/retrieval_curation.py`:

```python
"""Curacion de recuperaciones del RAG por uso real (KSI Fase 3).

El curator mide que chunks usa de verdad el reasoner y re-rankea las
recuperaciones futuras. Es un *decorador* de recuperacion: la fuente de verdad
sigue siendo el indice FAISS + el vault; rag_usage.json es cache desechable.
Fail-safe en cada metodo: nunca puede degradar la respuesta de voz.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import numpy as np

_WS_RE = re.compile(r"\s+")


class RetrievalCurator:
    def __init__(self, *, config, embed_fn, state_path: Path | str = "data/rag_usage.json") -> None:
        self.config = config
        self.embed_fn = embed_fn
        self.state_path = Path(state_path)
        self._chunks: dict[str, dict] = {}
        self._pending: dict[str, list] = {}
        self._load()

    # ---- clave estable ----
    @staticmethod
    def chunk_key(rel_path: str, text: str) -> str:
        norm = _WS_RE.sub(" ", (text or "")).strip()
        raw = f"{rel_path}|{norm}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # ---- senal de calidad ----
    def quality_factor(self, key: str) -> float:
        st = self._chunks.get(key)
        if st is None or st.get("retrieved", 0) < self.config.cold_start_min:
            return 1.0
        retrieved = st["retrieved"]
        rate = st.get("used", 0) / retrieved if retrieved else 0.0
        floor, ceil = self.config.factor_floor, self.config.factor_ceil
        factor = floor + rate * (ceil - floor)
        return max(floor, min(ceil, factor))

    # ---- persistencia atomica ----
    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._chunks = dict(data.get("chunks", {}))
            self._pending = dict(data.get("pending", {}))
        except Exception:
            self._chunks, self._pending = {}, {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"chunks": self._chunks, "pending": self._pending}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.state_path)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/retrieval_curation.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): chunk_key estable + quality_factor lineal (Fase 3 Task 2)"
```

---

## Task 3: rerank + note_retrieval (fail-safe + persistencia)

**Files:**
- Modify: `memory/self_improvement/retrieval_curation.py`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
from dataclasses import dataclass


@dataclass
class _Chunk:
    rel_path: str
    text: str
    title: str = ""


@dataclass
class _Result:
    chunk: _Chunk
    score: float


def test_rerank_reorders_by_factor(tmp_path):
    cur = _curator(tmp_path, cold_start_min=2, factor_floor=0.6, factor_ceil=1.4)
    a = _Result(_Chunk("p.md", "alpha"), 0.50)   # mala: factor 0.6 -> 0.30
    b = _Result(_Chunk("p.md", "beta"), 0.45)    # buena: factor 1.4 -> 0.63
    cur._chunks[RetrievalCurator.chunk_key("p.md", "alpha")] = {"retrieved": 10, "used": 0, "last_used": None, "last_touch": None}
    cur._chunks[RetrievalCurator.chunk_key("p.md", "beta")] = {"retrieved": 10, "used": 10, "last_used": None, "last_touch": None}
    out = cur.rerank([a, b])
    assert [r.chunk.text for r in out] == ["beta", "alpha"]
    assert abs(out[0].score - 0.63) < 1e-9


def test_rerank_neutral_when_no_stats(tmp_path):
    cur = _curator(tmp_path)
    a = _Result(_Chunk("p.md", "alpha"), 0.50)
    b = _Result(_Chunk("p.md", "beta"), 0.40)
    out = cur.rerank([a, b])
    assert [r.chunk.text for r in out] == ["alpha", "beta"]   # orden intacto, scores neutrales


def test_rerank_is_fail_safe(tmp_path):
    cur = _curator(tmp_path)
    bad = object()  # sin .chunk / .score
    out = cur.rerank([bad])
    assert out == [bad]   # devuelve la lista intacta, no propaga


def test_note_retrieval_increments_and_persists(tmp_path):
    cur = _curator(tmp_path)
    r = _Result(_Chunk("p.md", "alpha"), 0.5)
    cur.note_retrieval("que es alpha?", [r])
    key = RetrievalCurator.chunk_key("p.md", "alpha")
    assert cur._chunks[key]["retrieved"] == 1
    # pending guarda [key, text] bajo el hash del prompt
    reloaded = RetrievalCurator(config=cur.config, embed_fn=cur.embed_fn, state_path=cur.state_path)
    assert reloaded._chunks[key]["retrieved"] == 1
    assert any(key == k for pend in reloaded._pending.values() for k, _ in pend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k "rerank or note_retrieval" -v`
Expected: FAIL (`AttributeError: 'RetrievalCurator' object has no attribute 'rerank'`).

- [ ] **Step 3: Implement rerank + note_retrieval + helpers**

In `memory/self_improvement/retrieval_curation.py`, add inside the class (after `quality_factor`):

```python
    @staticmethod
    def _prompt_hash(prompt: str) -> str:
        return hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:16]

    def _stat(self, key: str) -> dict:
        return self._chunks.setdefault(
            key, {"retrieved": 0, "used": 0, "last_used": None, "last_touch": None}
        )

    def rerank(self, results: list) -> list:
        try:
            adjusted = []
            for r in results:
                key = self.chunk_key(r.chunk.rel_path, r.chunk.text)
                r.score = r.score * self.quality_factor(key)
                adjusted.append(r)
            adjusted.sort(key=lambda r: r.score, reverse=True)
            return adjusted
        except Exception:
            return results

    def note_retrieval(self, prompt: str, results: list, *, today=None) -> None:
        try:
            today = today or date.today().isoformat()
            pend: list = []
            for r in results:
                key = self.chunk_key(r.chunk.rel_path, r.chunk.text)
                st = self._stat(key)
                st["retrieved"] += 1
                st["last_touch"] = today
                pend.append([key, r.chunk.text])
            if pend:
                self._pending[self._prompt_hash(prompt)] = pend
                self._save()
        except Exception:
            pass
```

Note: `rerank` mutates `r.score` (both `SearchResult` and the test `_Result` are mutable dataclasses) so the adjusted score flows into the `MIN_RAG_SCORE` filter downstream.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k "rerank or note_retrieval" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/retrieval_curation.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): rerank no destructivo + note_retrieval (Fase 3 Task 3)"
```

---

## Task 4: attribute_usage (coseno respuesta<->chunk, fail-safe)

**Files:**
- Modify: `memory/self_improvement/retrieval_curation.py`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
import numpy as np


def _lookup_embed(mapping):
    """embed_fn fake: devuelve el vector mapeado por texto (ya normalizado)."""
    def _fn(texts):
        return np.array([mapping[t] for t in texts], dtype="float32")
    return _fn


def test_attribute_usage_counts_only_used(tmp_path):
    cfg = KnowledgeImproverConfig(use_threshold=0.55)
    mapping = {
        "respuesta sobre alpha": [1.0, 0.0],
        "alpha":                 [1.0, 0.0],   # coseno 1.0 -> usado
        "beta":                  [0.0, 1.0],   # coseno 0.0 -> no usado
    }
    cur = RetrievalCurator(config=cfg, embed_fn=_lookup_embed(mapping), state_path=tmp_path / "u.json")
    ra = _Result(_Chunk("p.md", "alpha"), 0.5)
    rb = _Result(_Chunk("p.md", "beta"), 0.5)
    cur.note_retrieval("q", [ra, rb])
    cur.attribute_usage("q", "respuesta sobre alpha")
    ka = RetrievalCurator.chunk_key("p.md", "alpha")
    kb = RetrievalCurator.chunk_key("p.md", "beta")
    assert cur._chunks[ka]["used"] == 1
    assert cur._chunks[kb]["used"] == 0
    assert cur._pending == {}   # se limpio el pending del prompt


def test_attribute_usage_is_fail_safe(tmp_path):
    def _boom(texts):
        raise RuntimeError("embed caido")
    cfg = KnowledgeImproverConfig()
    cur = RetrievalCurator(config=cfg, embed_fn=_boom, state_path=tmp_path / "u.json")
    cur._chunks["k"] = {"retrieved": 1, "used": 0, "last_used": None, "last_touch": None}
    cur._pending = {RetrievalCurator._prompt_hash("q"): [["k", "alpha"]]}
    cur.attribute_usage("q", "cualquier respuesta")   # no debe propagar
    assert cur._chunks["k"]["used"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k attribute_usage -v`
Expected: FAIL (`AttributeError: ... 'attribute_usage'`).

- [ ] **Step 3: Implement attribute_usage**

In `memory/self_improvement/retrieval_curation.py`, add inside the class (after `note_retrieval`):

```python
    def attribute_usage(self, prompt: str, response_text: str, *, today=None) -> None:
        try:
            phash = self._prompt_hash(prompt)
            pend = self._pending.get(phash)
            if not pend or not (response_text or "").strip():
                return
            today = today or date.today().isoformat()
            texts = [response_text] + [text for _, text in pend]
            emb = np.asarray(self.embed_fn(texts), dtype="float32")
            resp_vec = emb[0]
            resp_norm = float(np.linalg.norm(resp_vec)) or 1.0
            for i, (key, _text) in enumerate(pend):
                vec = emb[i + 1]
                denom = (float(np.linalg.norm(vec)) or 1.0) * resp_norm
                cos = float(np.dot(resp_vec, vec)) / denom
                if cos >= self.config.use_threshold:
                    st = self._stat(key)
                    st["used"] += 1
                    st["last_used"] = today
                    st["last_touch"] = today
            self._pending.pop(phash, None)
            self._save()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k attribute_usage -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/retrieval_curation.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): atribucion de uso por coseno respuesta-chunk (Fase 3 Task 4)"
```

---

## Task 5: housekeeping (decay + purga de huerfanos)

**Files:**
- Modify: `memory/self_improvement/retrieval_curation.py`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
def test_housekeeping_decays_and_purges(tmp_path):
    cfg = KnowledgeImproverConfig(usage_decay_days=45)
    cur = RetrievalCurator(config=cfg, embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
                           state_path=tmp_path / "u.json")
    # chunk stale: tocado hace 45 dias -> factor 0.5
    cur._chunks["stale"] = {"retrieved": 12, "used": 8, "last_used": "2026-05-01", "last_touch": "2026-05-01"}
    # chunk huerfano: no esta en valid_keys -> se elimina
    cur._chunks["orphan"] = {"retrieved": 5, "used": 2, "last_used": None, "last_touch": "2026-06-15"}
    cur.housekeeping(valid_keys={"stale"}, today="2026-06-15")
    assert "orphan" not in cur._chunks
    assert abs(cur._chunks["stale"]["retrieved"] - 6.0) < 1e-6
    assert abs(cur._chunks["stale"]["used"] - 4.0) < 1e-6
    assert cur._chunks["stale"]["last_touch"] == "2026-06-15"


def test_housekeeping_drops_negligible(tmp_path):
    cfg = KnowledgeImproverConfig(usage_decay_days=10)
    cur = RetrievalCurator(config=cfg, embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
                           state_path=tmp_path / "u.json")
    cur._chunks["tiny"] = {"retrieved": 1, "used": 0, "last_used": None, "last_touch": "2026-01-01"}
    cur.housekeeping(valid_keys=None, today="2026-06-15")   # >> 10 dias -> retrieved ~ 0
    assert "tiny" not in cur._chunks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k housekeeping -v`
Expected: FAIL (`AttributeError: ... 'housekeeping'`).

- [ ] **Step 3: Implement housekeeping**

In `memory/self_improvement/retrieval_curation.py`, change the date import at the top (replace `from datetime import date`):

```python
from datetime import date, datetime
```

Then add inside the class (after `attribute_usage`):

```python
    @staticmethod
    def _age_days(iso: str | None, today: str) -> int:
        if not iso:
            return 0
        try:
            d0 = datetime.fromisoformat(iso).date()
            d1 = datetime.fromisoformat(today).date()
            return max(0, (d1 - d0).days)
        except Exception:
            return 0

    def housekeeping(self, *, valid_keys=None, today=None) -> None:
        try:
            today = today or date.today().isoformat()
            half = max(1, self.config.usage_decay_days)
            for key in list(self._chunks.keys()):
                if valid_keys is not None and key not in valid_keys:
                    del self._chunks[key]
                    continue
                st = self._chunks[key]
                age = self._age_days(st.get("last_touch"), today)
                if age > 0:
                    f = 0.5 ** (age / half)
                    st["retrieved"] = st.get("retrieved", 0) * f
                    st["used"] = st.get("used", 0) * f
                    st["last_touch"] = today
                if st.get("retrieved", 0) < 0.5:
                    del self._chunks[key]
            # pendings huerfanos (turnos sin respuesta) caducan en housekeeping
            self._pending = {}
            self._save()
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -v`
Expected: PASS (all curator tests).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/retrieval_curation.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): housekeeping con decay + purga de huerfanos (Fase 3 Task 5)"
```

---

## Task 6: Wire build_project_context (rerank + note)

**Files:**
- Modify: `memory/context_assembler.py:82-117`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
from memory.context_assembler import build_project_context


class _FakeRAG:
    def search(self, query, top_k=3):
        return [
            _Result(_Chunk("p.md", "buena", title="T"), 0.30),
            _Result(_Chunk("p.md", "mala", title="T"), 0.30),
        ]


def test_build_context_invokes_curator(tmp_path, monkeypatch):
    import memory.context_assembler as ca
    monkeypatch.setattr(ca.triage_mod, "detect_project", lambda p: "ProjX")
    monkeypatch.setattr(ca, "_load_card_body", lambda v, p: "")
    cur = _curator(tmp_path, cold_start_min=2)
    cur._chunks[RetrievalCurator.chunk_key("p.md", "buena")] = {"retrieved": 10, "used": 10, "last_used": None, "last_touch": None}
    cur._chunks[RetrievalCurator.chunk_key("p.md", "mala")] = {"retrieved": 10, "used": 0, "last_used": None, "last_touch": None}
    res = build_project_context(vault=None, rag=_FakeRAG(), prompt="algo de ProjX", curator=cur)
    # note_retrieval corrio: el chunk "buena" tiene retrieved incrementado
    assert cur._chunks[RetrievalCurator.chunk_key("p.md", "buena")]["retrieved"] == 11
    # "mala" cayo bajo MIN_RAG_SCORE (0.30*0.6=0.18 < 0.25); "buena" sobrevive
    assert "buena" in res.text and "mala" not in res.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k build_context -v`
Expected: FAIL (`TypeError: build_project_context() got an unexpected keyword argument 'curator'`).

- [ ] **Step 3: Add the curator param + seam**

In `memory/context_assembler.py`, change the signature of `build_project_context` (lines 82-89) to add `curator=None`:

```python
def build_project_context(
    vault: ObsidianVault,
    rag: VaultRAG,
    prompt: str,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    semantic_memory=None,
    curator=None,
) -> ContextResult:
```

Then replace the RAG retrieval block (currently lines 110-115):

```python
    try:
        searcher = semantic_memory or rag
        rag_results = searcher.search(prompt, top_k=RAG_TOP_K)
    except Exception:
        rag_results = []
```

with:

```python
    try:
        searcher = semantic_memory or rag
        rag_results = searcher.search(prompt, top_k=RAG_TOP_K)
        if curator is not None:
            rag_results = curator.rerank(rag_results)
            curator.note_retrieval(prompt, rag_results)
    except Exception:
        rag_results = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k build_context -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): rerank+note del curator en build_project_context (Fase 3 Task 6)"
```

---

## Task 7: Wire ToolContext + ask_claude_deep (atribucion)

**Files:**
- Modify: `memory/tools.py:51-76` (ToolContext), `:1258-1263` (`_augmented_context`), `:1266-1278` (`ask_claude_deep`), `:1326-1340` (`ask_claude_deep_async`)
- Test: `tests/test_ask_claude_deep_context.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ask_claude_deep_context.py`:

```python
def test_ask_claude_deep_attributes_usage():
    from memory.tools import ToolContext, ask_claude_deep

    class _R:
        text = "respuesta usando alpha"
        latency_ms = 1.0
        cost_usd = 0.0
        input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0

    class _Reasoner:
        model = "claude-x"
        def ask(self, prompt, context_extra=None, max_tokens=0):
            return _R()

    calls = {}

    class _Curator:
        def attribute_usage(self, prompt, text):
            calls["args"] = (prompt, text)

    ctx = ToolContext(vault=None, rag=None, reasoner=_Reasoner(), retrieval_curator=_Curator())
    # _augmented_context es fail-safe con vault=None: devuelve context_extra tal cual
    out = ask_claude_deep(ctx, "pregunta alpha", context_extra=None, max_tokens=200)
    assert out["ok"] is True
    assert calls["args"] == ("pregunta alpha", "respuesta usando alpha")


def test_ask_claude_deep_attribution_is_fail_safe():
    from memory.tools import ToolContext, ask_claude_deep

    class _R:
        text = "x"
        latency_ms = 1.0
        cost_usd = 0.0
        input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0

    class _Reasoner:
        model = "claude-x"
        def ask(self, prompt, context_extra=None, max_tokens=0):
            return _R()

    class _BoomCurator:
        def attribute_usage(self, prompt, text):
            raise RuntimeError("boom")

    ctx = ToolContext(vault=None, rag=None, reasoner=_Reasoner(), retrieval_curator=_BoomCurator())
    out = ask_claude_deep(ctx, "q", context_extra=None)   # no propaga
    assert out["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ask_claude_deep_context.py -k attribut -v`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'retrieval_curator'`).

- [ ] **Step 3a: Add the ToolContext field**

In `memory/tools.py`, in the `ToolContext` dataclass (after the `proactivity` field, line ~76):

```python
    # Curador de recuperaciones del RAG (KSI Fase 3). None si esta deshabilitado.
    retrieval_curator: Any | None = None
```

- [ ] **Step 3b: Pass the curator through `_augmented_context`**

Replace `_augmented_context` (lines 1258-1263):

```python
def _augmented_context(ctx: ToolContext, prompt: str, context_extra: str | None) -> str | None:
    try:
        auto = build_project_context(
            ctx.vault, ctx.rag, prompt,
            semantic_memory=ctx.semantic_memory,
            curator=ctx.retrieval_curator,
        )
    except Exception:
        return context_extra  # fail-safe: nunca rompe el razonamiento
    return _merge_context(context_extra, auto.text)
```

- [ ] **Step 3c: Add a fail-safe attribution helper**

Add right after `_augmented_context`:

```python
def _attribute_usage(ctx: ToolContext, prompt: str, response_text: str) -> None:
    if ctx.retrieval_curator is None:
        return
    try:
        ctx.retrieval_curator.attribute_usage(prompt, response_text)
    except Exception:
        pass  # jamas degrada la respuesta
```

- [ ] **Step 3d: Call it after the response in both Claude functions**

In `ask_claude_deep` (line ~1277), replace:

```python
    r = ctx.reasoner.ask(prompt, context_extra=merged, max_tokens=max_tokens)
    return _format_claude_response(ctx.reasoner.model, r)
```

with:

```python
    r = ctx.reasoner.ask(prompt, context_extra=merged, max_tokens=max_tokens)
    _attribute_usage(ctx, prompt, getattr(r, "text", "") or "")
    return _format_claude_response(ctx.reasoner.model, r)
```

In `ask_claude_deep_async` (line ~1339), replace:

```python
    r = await ctx.reasoner.ask_async(prompt, context_extra=merged, max_tokens=max_tokens)
    return _format_claude_response(ctx.reasoner.model, r)
```

with:

```python
    r = await ctx.reasoner.ask_async(prompt, context_extra=merged, max_tokens=max_tokens)
    _attribute_usage(ctx, prompt, getattr(r, "text", "") or "")
    return _format_claude_response(ctx.reasoner.model, r)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ask_claude_deep_context.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py tests/test_ask_claude_deep_context.py
git commit -m "feat(ksi): curator en ToolContext + atribucion tras ask_claude_deep (Fase 3 Task 7)"
```

---

## Task 8: Housekeeping en el cierre KSI + instanciacion en jarvis.py

**Files:**
- Modify: `memory/self_improvement/improver.py:26-48` (init), `:101-116` (housekeeping en `_run_inner`)
- Modify: `jarvis.py:490-543`
- Test: `tests/test_retrieval_curation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_retrieval_curation.py`:

```python
from memory.self_improvement.improver import KnowledgeImprover


def test_improver_runs_curator_housekeeping(tmp_path):
    cfg = KnowledgeImproverConfig(enabled=True)
    cur = RetrievalCurator(config=cfg, embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
                           state_path=tmp_path / "u.json")
    cur._chunks["orphan"] = {"retrieved": 5, "used": 1, "last_used": None, "last_touch": "2026-06-15"}
    cur._chunks["keep"] = {"retrieved": 5, "used": 1, "last_used": None, "last_touch": "2026-06-15"}

    imp = KnowledgeImprover(
        config=cfg,
        embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
        event_loader=lambda v: [],   # sin eventos: _run_inner sale temprano
        retrieval_curator=cur,
        chunk_keys_provider=lambda: {"keep"},
    )
    imp.run_curator_housekeeping(today="2026-06-15")
    assert "orphan" not in cur._chunks
    assert "keep" in cur._chunks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py -k improver_runs -v`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'retrieval_curator'`).

- [ ] **Step 3a: Extend KnowledgeImprover**

In `memory/self_improvement/improver.py`, update `__init__` (lines 27-40) to accept the two new params:

```python
    def __init__(
        self,
        *,
        config: KnowledgeImproverConfig,
        embed_fn,
        reasoner=None,
        proactivity_engine=None,
        event_loader: EventLoader | None = None,
        retrieval_curator=None,
        chunk_keys_provider=None,
    ) -> None:
        self.config = config
        self.embed_fn = embed_fn
        self.reasoner = reasoner
        self.proactivity_engine = proactivity_engine
        self._event_loader = event_loader or _default_event_loader
        self.retrieval_curator = retrieval_curator
        self._chunk_keys_provider = chunk_keys_provider
```

Add a dedicated fail-safe method (after `run`, before `_run_inner`):

```python
    def run_curator_housekeeping(self, *, today=None) -> None:
        if self.retrieval_curator is None:
            return
        try:
            valid = self._chunk_keys_provider() if self._chunk_keys_provider else None
            self.retrieval_curator.housekeeping(valid_keys=valid, today=today)
        except Exception:
            pass
```

Call it from `_run_inner`, right before the metrics block (after the Fase 2 gaps `try/except`, currently line 99). Insert:

```python
        # Fase 3 - housekeeping del curador de RAG (decay + purga de huerfanos).
        self.run_curator_housekeeping()
```

- [ ] **Step 3b: Wire instantiation in jarvis.py**

In `jarvis.py`, just before the KSI block (line ~490), add the curator construction:

```python
        # Curador de recuperaciones del RAG (KSI Fase 3): gated por JARVIS_RAG_CURATION.
        self.retrieval_curator = None
        try:
            _ksi_cfg = KnowledgeImproverConfig.from_env()
            if _ksi_cfg.rag_curation_enabled:
                from memory.self_improvement.retrieval_curation import RetrievalCurator
                self.retrieval_curator = RetrievalCurator(
                    config=_ksi_cfg,
                    embed_fn=lambda texts: self.rag._ensure_model().encode(
                        list(texts), normalize_embeddings=True
                    ),
                    state_path=Path("data") / "rag_usage.json",
                )
        except Exception as exc:
            self.retrieval_curator = None
            log.warning(f"[WARN] curador de RAG no pudo inicializarse: {exc}")
```

In the `KnowledgeImprover(...)` construction (lines 492-499), add the two params:

```python
            self.knowledge_improver = KnowledgeImprover(
                config=KnowledgeImproverConfig.from_env(),
                embed_fn=lambda texts: self.rag._ensure_model().encode(
                    list(texts), normalize_embeddings=True
                ),
                reasoner=self.reasoner,
                proactivity_engine=self.proactivity,
                retrieval_curator=self.retrieval_curator,
                chunk_keys_provider=lambda: {
                    RetrievalCurator.chunk_key(c.rel_path, c.text)
                    for c in self.rag.chunks if c.text
                },
            )
```

Add the import near the top of `jarvis.py` (with the other `memory.self_improvement` imports) so `chunk_keys_provider` can reference it:

```python
from memory.self_improvement.retrieval_curation import RetrievalCurator
```

In the `ToolContext(...)` construction (lines 526-543), add the field:

```python
            retrieval_curator=self.retrieval_curator,
```

- [ ] **Step 4: Run tests**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_retrieval_curation.py tests/test_ask_claude_deep_context.py -v`
Expected: PASS.

Then byte-compile `jarvis.py` (no test harness lo importa directamente):
Run: `PYTHONUTF8=1 /h/Python311/python.exe -m py_compile jarvis.py`
Expected: sin salida (exito).

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/improver.py jarvis.py tests/test_retrieval_curation.py
git commit -m "feat(ksi): housekeeping en cierre KSI + wiring del curador en jarvis (Fase 3 Task 8)"
```

---

## Task 9: Documentacion (.env.example + CHANGELOG)

**Files:**
- Modify: `.env.example`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document the env vars**

In `.env.example`, in the KSI section (junto a las `JARVIS_KSI_*` existentes), add:

```bash
# --- KSI Fase 3: RAG auto-curado (re-ranking por uso real) ---
# Activa el curador: mide que chunks usa el reasoner y re-rankea recuperaciones.
# Autonomo y no destructivo (solo reordena en memoria; nunca toca el indice FAISS).
JARVIS_RAG_CURATION=false
# Coseno minimo respuesta<->chunk para contar un chunk como "usado" (0..1).
JARVIS_KSI_USE_THRESHOLD=0.55
# Recuperaciones minimas antes de salir del factor neutral 1.0 (arranque en frio).
JARVIS_KSI_COLD_START_MIN=5
# Rango del multiplicador de score en el rerank.
JARVIS_KSI_FACTOR_FLOOR=0.6
JARVIS_KSI_FACTOR_CEIL=1.4
# Vida media (dias) para decaer cuentas de uso en el housekeeping de cierre.
JARVIS_KSI_USAGE_DECAY_DAYS=45
```

- [ ] **Step 2: Update CHANGELOG**

In `CHANGELOG.md`, under the unreleased/most-recent section, add:

```markdown
### Added
- KSI Fase 3 - RAG auto-curado: el curador (`RetrievalCurator`) mide el uso real
  de cada chunk por el reasoner (atribucion por coseno respuesta<->chunk) y
  re-rankea las recuperaciones futuras con un `quality_factor` lineal acotado a
  [0.6, 1.4]. Autonomo y no destructivo (estado desechable en `data/rag_usage.json`,
  nunca toca el indice FAISS). Housekeeping de decay + purga al cierre de sesion.
  Gated por `JARVIS_RAG_CURATION`. Fail-safe en cada seam: jamas degrada la respuesta.
```

- [ ] **Step 3: Verify nothing broke**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/ -k "ksi or gap or curation or claude_deep" -v`
Expected: PASS (curacion + KSI + gaps + ask_claude_deep). `test_version_is_1_02` puede fallar (stale preexistente, ajeno a esto - NO tocar).

- [ ] **Step 4: Commit**

```bash
git add .env.example CHANGELOG.md
git commit -m "docs(ksi): documenta RAG auto-curado Fase 3 (env + changelog)"
```

---

## Self-Review

**1. Spec coverage:**
- chunk_key estable -> Task 2 ok
- quality_factor lineal + cold-start + clamp -> Task 2 ok
- estado `rag_usage.json` atomico -> Task 2 (`_save` tmp+replace) ok
- rerank no destructivo antes de MIN_RAG_SCORE -> Task 3 + Task 6 ok
- note_retrieval (`retrieved++`, pending) -> Task 3 ok
- attribute_usage (batch coseno, `used++`, limpia pending) -> Task 4 ok
- housekeeping (decay + purga huerfanos) -> Task 5 + Task 8 ok
- fail-safe en todos los seams -> Tasks 3/4/5 (try/except interno) + Task 7 (`_attribute_usage`) ok
- wiring ToolContext + build_project_context + ask_claude_deep/_async -> Tasks 6/7 ok
- housekeeping en cierre KSI, cero cambios de trigger en jarvis -> Task 8 ok
- config `from_env` + 6 campos -> Task 1 ok
- docs env + changelog -> Task 9 ok

**2. Placeholder scan:** sin TBD/TODO; cada step con codigo completo y comando + salida esperada.

**3. Type consistency:** `chunk_key(rel_path, text)` estatico usado igual en Tasks 2/3/4/6/8; `_chunks[key]` siempre `{retrieved, used, last_used, last_touch}`; `quality_factor(key)`, `rerank(results)`, `note_retrieval(prompt, results)`, `attribute_usage(prompt, response_text)`, `housekeeping(valid_keys=, today=)` - firmas consistentes entre modulo, wiring y tests. `KnowledgeImprover.run_curator_housekeeping(today=)` coincide entre Task 8 init/metodo y el test.

**Nota de coupling:** el curador comparte el `embed_fn` normalizado del RAG (`normalize_embeddings=True`), asi que el coseno en `attribute_usage` opera sobre vectores ya normalizados; aun asi se normaliza defensivamente. El curador solo se instancia con `JARVIS_RAG_CURATION=true` (default off), igual que el patron gated de proactividad.
