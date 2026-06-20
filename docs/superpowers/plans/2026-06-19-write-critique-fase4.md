# KSI Fase 4 — Auto-crítica en escritura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `jarvis_remember` refine de forma autónoma y fail-safe los `content` vagos antes de persistirlos, reusando el reasoner ya existente.

**Architecture:** Módulo nuevo `memory/self_improvement/write_critique.py` con `detect_vague` (determinista, bilingüe ES/EN), `refine` (reasoner presupuestado + JSON self-heal) y una fachada `critique` fail-safe. Un único seam síncrono en `jarvis_remember`, tras el guard de triage y antes de escribir, refina `content` una vez (fluye a los 3 sitios que lo usan: nota nueva, append y bullet de card). Gated por `JARVIS_KSI_WRITE_CRITIQUE` (default OFF). Stateless.

**Tech Stack:** Python 3.11, pytest, dataclasses, `re`, `json`. Reasoner = Claude Sonnet 4.6 vía `ctx.reasoner.ask(...)`.

**Runner (Git Bash en Windows):** `PYTHONUTF8=1 /h/Python311/python.exe -m pytest`

---

### Task 1: Módulo `write_critique.py` — `CritiqueResult` + `detect_vague`

**Files:**
- Create: `memory/self_improvement/write_critique.py`
- Test: `tests/test_ksi_write_critique.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ksi_write_critique.py
from memory.self_improvement.write_critique import CritiqueResult, detect_vague


def test_critique_result_is_frozen_dataclass():
    r = CritiqueResult(text="hola", doubt=True)
    assert r.text == "hola"
    assert r.doubt is True


def test_detect_vague_flags_vague_spanish():
    assert detect_vague("Isaac quiere algo más sencillo, no estoy seguro de qué") is True


def test_detect_vague_flags_vague_english():
    assert detect_vague("we should add some stuff, not sure exactly what") is True


def test_detect_vague_ignores_precise_with_numbers():
    assert detect_vague("Decidimos usar Sonnet 4.6 por costo y latencia") is False


def test_detect_vague_ignores_precise_with_identifiers():
    assert detect_vague("El bug estaba en memory/tools.py por el import de numpy") is False


def test_detect_vague_precise_text_without_filler_is_false():
    assert detect_vague("La build v1.1.0 se mergeó a main el commit 84a15c6") is False


def test_detect_vague_empty_is_false():
    assert detect_vague("") is False
    assert detect_vague(None) is False  # type: ignore[arg-type]


def test_detect_vague_filler_but_concrete_acronym_is_false():
    # "varios" es muletilla, pero "AEC" (acrónimo) y "31dB" dan concreción.
    assert detect_vague("varios fixes al AEC subieron el ERLE a 31dB") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_write_critique.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.self_improvement.write_critique'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/self_improvement/write_critique.py
"""Auto-crítica en escritura (KSI Fase 4).

Detecta de forma determinista un `content` vago al guardarlo y, solo en ese caso,
pide al reasoner que lo reescriba preciso. Fail-safe total: ante cualquier fallo
devuelve el texto original. Stateless: no persiste nada.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Muletillas de imprecisión, bilingüe ES/EN. Matching por palabra, case-insensitive.
_VAGUE_TERMS = (
    # Español
    "algo", "varios", "varias", "creo", "más o menos", "mas o menos", "etc",
    "no estoy seguro", "como que", "tal vez", "quizá", "quizas", "supongo",
    "cosas", "alguna cosa",
    # Inglés
    "some", "a few", "several", "i think", "kind of", "sort of", "maybe",
    "i guess", "stuff", "things", "not sure", "somehow",
)

# Señales de concreción: si aparecen, el texto NO se considera vago aunque tenga
# muletillas. Dígitos, rutas, file.ext, acrónimos en MAYÚSCULAS, identificadores
# camelCase/PascalCase. Evita marcar como "concreta" una mayúscula de inicio de
# frase normal.
_CONCRETE_RE = re.compile(
    r"\d"                       # cualquier dígito
    r"|[/\\]"                   # separadores de ruta
    r"|\w+\.\w+"                # file.ext / modulo.attr
    r"|\b[A-Z]{2,}\b"           # acrónimos: API, RAG, AEC
    r"|\b\w*[a-z]\w*[A-Z]\w*"   # camelCase / PascalCase
)


@dataclass(frozen=True)
class CritiqueResult:
    text: str
    doubt: bool


def detect_vague(text: str) -> bool:
    """True si el texto tiene muletilla de imprecisión Y carece de concreción."""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    has_vague = any(re.search(rf"\b{re.escape(term)}\b", low) for term in _VAGUE_TERMS)
    if not has_vague:
        return False
    if _CONCRETE_RE.search(t):
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_write_critique.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/write_critique.py tests/test_ksi_write_critique.py
git commit -m "feat(ksi): detect_vague + CritiqueResult para Fase 4 (write-critique)"
```

---

### Task 2: `refine` + fachada `critique`

**Files:**
- Modify: `memory/self_improvement/write_critique.py`
- Test: `tests/test_ksi_write_critique.py`

- [ ] **Step 1: Write the failing test**

```python
# Añadir a tests/test_ksi_write_critique.py
from memory.self_improvement.write_critique import critique, refine


class FakeReasoner:
    """Devuelve una respuesta con `.text` fija y cuenta las llamadas."""

    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, *, context_extra="", max_tokens=300):
        self.calls += 1
        return type("Resp", (), {"text": self._text})()


class BoomReasoner:
    def ask(self, *a, **k):
        raise RuntimeError("reasoner caído")


def test_refine_rewrites_and_reads_doubt():
    r = FakeReasoner('{"text": "Isaac prefiere notas granulares por proyecto", "doubt": false}')
    out = refine(r, "Isaac quiere algo más granular")
    assert out.text == "Isaac prefiere notas granulares por proyecto"
    assert out.doubt is False
    assert r.calls == 1


def test_refine_sets_doubt_true():
    r = FakeReasoner('{"text": "Isaac mencionó un cambio sin especificar cuál", "doubt": true}')
    out = refine(r, "hay que cambiar algo")
    assert out.doubt is True


def test_refine_self_heals_json_with_prose():
    r = FakeReasoner('Claro:\n{"text": "texto preciso", "doubt": false}\n¿algo más?')
    out = refine(r, "algo vago")
    assert out.text == "texto preciso"


def test_refine_corrupt_json_returns_original():
    r = FakeReasoner("no es json en absoluto")
    out = refine(r, "texto original vago")
    assert out.text == "texto original vago"
    assert out.doubt is False


def test_refine_empty_refined_returns_original():
    r = FakeReasoner('{"text": "   ", "doubt": false}')
    out = refine(r, "texto original")
    assert out.text == "texto original"


def test_refine_none_reasoner_returns_original():
    out = refine(None, "texto")
    assert out.text == "texto"


def test_critique_disabled_returns_original_without_calling_reasoner():
    r = FakeReasoner('{"text": "no debería usarse", "doubt": false}')
    out = critique(r, "esto es algo vago", enabled=False)
    assert out.text == "esto es algo vago"
    assert r.calls == 0


def test_critique_not_vague_skips_reasoner():
    r = FakeReasoner('{"text": "no debería usarse", "doubt": false}')
    out = critique(r, "Mergeado a main en el commit 84a15c6", enabled=True)
    assert out.text == "Mergeado a main en el commit 84a15c6"
    assert r.calls == 0


def test_critique_vague_refines():
    r = FakeReasoner('{"text": "texto preciso final", "doubt": false}')
    out = critique(r, "Isaac quiere algo más simple, no estoy seguro", enabled=True)
    assert out.text == "texto preciso final"
    assert r.calls == 1


def test_critique_reasoner_exception_returns_original():
    out = critique(BoomReasoner(), "Isaac quiere algo, no estoy seguro", enabled=True)
    assert out.text == "Isaac quiere algo, no estoy seguro"
    assert out.doubt is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_write_critique.py -k "refine or critique" -v`
Expected: FAIL with `ImportError: cannot import name 'critique'`

- [ ] **Step 3: Write minimal implementation**

Añadir a `memory/self_improvement/write_critique.py`:

```python
_INSTRUCTIONS = (
    "Eres el bibliotecario de JARVIS. Te paso una memoria que un detector marcó "
    "como VAGA o imprecisa. Reescríbela para que sea precisa y concreta, "
    "conservando SOLO la información presente. PROHIBIDO inventar datos, nombres, "
    "números o fechas que no estén en el texto. Si no puedes concretarla por falta "
    "de información objetiva, devuélvela lo más clara posible y marca doubt=true. "
    'Responde SOLO un objeto JSON: {"text": "<memoria reescrita>", "doubt": true|false}.'
)


def _extract_json(text: str) -> dict | None:
    """Primer objeto JSON balanceado dentro del texto (self-heal básico).

    Espeja memory/self_improvement/judge.py para mantener el módulo aislado.
    """
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


def refine(reasoner, text: str, *, max_tokens: int = 300) -> CritiqueResult:
    """Pide al reasoner reescribir `text`. SOLO se llama sobre texto vago."""
    if reasoner is None:
        return CritiqueResult(text=text, doubt=False)
    try:
        resp = reasoner.ask(_INSTRUCTIONS, context_extra="MEMORIA:\n" + text, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return CritiqueResult(text=text, doubt=False)
    if not isinstance(data, dict) or "text" not in data:
        return CritiqueResult(text=text, doubt=False)
    refined = str(data.get("text") or "").strip()
    if not refined:
        return CritiqueResult(text=text, doubt=False)
    return CritiqueResult(text=refined, doubt=bool(data.get("doubt")))


def critique(reasoner, text: str, *, enabled: bool, max_tokens: int = 300) -> CritiqueResult:
    """Fachada fail-safe — único punto de entrada para jarvis_remember."""
    try:
        if not enabled:
            return CritiqueResult(text=text, doubt=False)
        if not detect_vague(text):
            return CritiqueResult(text=text, doubt=False)
        return refine(reasoner, text, max_tokens=max_tokens)
    except Exception:
        return CritiqueResult(text=text, doubt=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_write_critique.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/write_critique.py tests/test_ksi_write_critique.py
git commit -m "feat(ksi): refine + fachada critique fail-safe (Fase 4)"
```

---

### Task 3: Config `write_critique_enabled` + `from_env`

**Files:**
- Modify: `memory/self_improvement/config.py:30` (tras `usage_decay_days`) y bloque `from_env` (tras línea 68)
- Test: `tests/test_ksi_config.py`

- [ ] **Step 1: Write the failing test**

```python
# Añadir a tests/test_ksi_config.py
def test_write_critique_config_defaults_off_and_env():
    cfg = KnowledgeImproverConfig()
    assert cfg.write_critique_enabled is False
    cfg2 = KnowledgeImproverConfig.from_env({"JARVIS_KSI_WRITE_CRITIQUE": "true"})
    assert cfg2.write_critique_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py::test_write_critique_config_defaults_off_and_env -v`
Expected: FAIL with `AttributeError: 'KnowledgeImproverConfig' object has no attribute 'write_critique_enabled'`

- [ ] **Step 3: Write minimal implementation**

En `config.py`, añadir el campo tras la línea 30 (`usage_decay_days`):

```python
    write_critique_enabled: bool = False  # auto-crítica en escritura (gate JARVIS_KSI_WRITE_CRITIQUE)
```

En `from_env`, añadir como último argumento de `return cls(...)` (tras `usage_decay_days=...`):

```python
            write_critique_enabled=_bool("JARVIS_KSI_WRITE_CRITIQUE", d.write_critique_enabled),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memory/self_improvement/config.py tests/test_ksi_config.py
git commit -m "feat(ksi): config write_critique_enabled (gate JARVIS_KSI_WRITE_CRITIQUE)"
```

---

### Task 4: Seam en `jarvis_remember` + campo en `ToolContext`

**Files:**
- Modify: `memory/tools.py` (import nuevo; `ToolContext` tras línea 78; seam tras línea 1087)
- Test: `tests/test_memory_remember.py`

- [ ] **Step 1: Write the failing test**

```python
# Añadir a tests/test_memory_remember.py
class FakeReasoner:
    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, *, context_extra="", max_tokens=300):
        self.calls += 1
        return type("Resp", (), {"text": self._text})()


def test_remember_refines_vague_content_when_enabled(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "Isaac prefiere notas granulares por proyecto", "doubt": false}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner, write_critique_enabled=True)

    jarvis_remember(
        ctx,
        title="Preferencia notas",
        content="Isaac quiere algo más granular, no estoy seguro de cómo",
        tags=["preference"],
    )

    note = read_note(vault, vault.memory_path / "Preferencia notas.md")
    assert "Isaac prefiere notas granulares por proyecto" in note.body
    assert "algo más granular" not in note.body
    assert reasoner.calls == 1


def test_remember_doubt_appends_marker(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "Isaac mencionó un cambio pendiente sin detallar", "doubt": true}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner, write_critique_enabled=True)

    jarvis_remember(
        ctx,
        title="Cambio pendiente",
        content="hay que cambiar algo, no estoy seguro qué",
        tags=["todo"],
    )

    text = (vault.memory_path / "Cambio pendiente.md").read_text(encoding="utf-8")
    assert "<!-- ksi-doubt:vague -->" in text


def test_remember_disabled_leaves_content_untouched(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "NO DEBERIA USARSE", "doubt": false}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner)  # flag default False

    jarvis_remember(
        ctx,
        title="Preferencia notas",
        content="Isaac quiere algo más granular, no estoy seguro de cómo",
        tags=["preference"],
    )

    note = read_note(vault, vault.memory_path / "Preferencia notas.md")
    assert "Isaac quiere algo más granular" in note.body
    assert "NO DEBERIA USARSE" not in note.body
    assert reasoner.calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_memory_remember.py -k "refines_vague or doubt_appends or disabled_leaves" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'write_critique_enabled'`

- [ ] **Step 3: Write minimal implementation**

(a) En `memory/tools.py`, junto a los imports `from . import ...` del módulo, añadir:

```python
from .self_improvement import write_critique
```

(b) En `ToolContext`, tras la línea 78 (`retrieval_curator: Any | None = None`), añadir:

```python
    # Auto-crítica en escritura (KSI Fase 4): refina content vago antes de guardar.
    write_critique_enabled: bool = False
```

(c) En `jarvis_remember`, insertar el seam INMEDIATAMENTE después del bloque
`if not triage.should_save: return {...}` (tras la línea 1087) y antes de
`path = ctx.vault.memory_file(title)`:

```python
    # KSI Fase 4: refina content vago una vez; fluye a nota nueva, append y card.
    _crit = write_critique.critique(ctx.reasoner, content, enabled=ctx.write_critique_enabled)
    content = _crit.text
    if _crit.doubt and "<!-- ksi-doubt:vague -->" not in content:
        content = content.rstrip() + "\n\n<!-- ksi-doubt:vague -->\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_memory_remember.py -v`
Expected: PASS (los 3 nuevos + los 5 existentes siguen verdes)

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py tests/test_memory_remember.py
git commit -m "feat(ksi): seam de auto-crítica en jarvis_remember (Fase 4)"
```

---

### Task 5: Cableado en `jarvis.py` (gate desde config)

**Files:**
- Modify: `jarvis.py:548-566` (construcción de `ToolContext`)

- [ ] **Step 1: Añadir el flag a la construcción de ToolContext**

En la llamada `self.tool_ctx = ToolContext(...)` (línea 548), añadir como último
argumento (tras `retrieval_curator=self.retrieval_curator,`):

```python
            write_critique_enabled=KnowledgeImproverConfig.from_env().write_critique_enabled,
```

- [ ] **Step 2: Verificar import**

Confirmar que `KnowledgeImproverConfig` ya está importado (jarvis.py:66
`from memory.self_improvement.config import KnowledgeImproverConfig`). No hace
falta nada más.

- [ ] **Step 3: Smoke de compilación e import**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "import ast,py_compile; py_compile.compile('jarvis.py', doraise=True); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Suite KSI completa + tools**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ksi_write_critique.py tests/test_ksi_config.py tests/test_memory_remember.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis.py
git commit -m "feat(ksi): cablear write_critique_enabled en ToolContext (Fase 4)"
```

---

### Task 6: Documentación (`.env.example` + `CHANGELOG.md`)

**Files:**
- Modify: `.env.example` (tras la línea `JARVIS_KSI_USAGE_DECAY_DAYS=45`, ~línea 295)
- Modify: `CHANGELOG.md` (nueva entrada al inicio de la sección de cambios)

- [ ] **Step 1: Añadir bloque a `.env.example`**

Tras `JARVIS_KSI_USAGE_DECAY_DAYS=45`:

```bash

# --- KSI Fase 4: auto-crítica en escritura (write-time self-critique) ---
# Cuando un content de jarvis_remember es vago/impreciso, el reasoner lo reescribe
# preciso ANTES de guardarlo (nota + card). Aditivo, fail-safe, síncrono. Si no
# puede concretar, anexa el marcador <!-- ksi-doubt:vague -->. Default OFF.
JARVIS_KSI_WRITE_CRITIQUE=false
```

- [ ] **Step 2: Añadir entrada a `CHANGELOG.md`**

El archivo ya tiene `## Unreleased` → `### Added` con bullets `- `. Insertar este
bullet como PRIMER ítem bajo ese `### Added` existente (antes del bullet de KSI
Fase 3), sin crear una sección nueva:

```markdown
- KSI Fase 4 - auto-critica en escritura: nuevo modulo `memory/self_improvement/write_critique.py`.
  `jarvis_remember` refina de forma autonoma los `content` vagos antes de persistirlos
  (deteccion determinista bilingue ES/EN + reasoner presupuestado con JSON self-heal).
  Anexa el marcador `<!-- ksi-doubt:vague -->` cuando queda duda. Stateless y fail-safe.
  Gated por `JARVIS_KSI_WRITE_CRITIQUE` (default OFF).
```

- [ ] **Step 3: Verificar que el CHANGELOG no quedó malformado**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "print(open('CHANGELOG.md', encoding='utf-8').read()[:400])"`
Expected: la nueva entrada visible y bien formada.

- [ ] **Step 4: Commit**

```bash
git add .env.example CHANGELOG.md
git commit -m "docs(ksi): documentar Fase 4 en .env.example y CHANGELOG"
```

---

## Cierre

- [ ] **Suite completa** (excepto el fallo stale conocido):

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest -q`
Expected: todo verde salvo `test_version_is_1_02` (stale preexistente, VERSION ya 1.03 — NO tocar).

- [ ] **Merge FF a main** (espejo de F1–F3) vía superpowers:finishing-a-development-branch.

---

## Notas de implementación

- **Aislamiento previo de WIP:** el working tree de Isaac tiene cambios sin commitear
  ajenos a esta feature (README.md, gemini/session.py, gemini/system_prompt.py,
  openai_code/reasoner.py, tests/test_gpt55_code_reasoner.py,
  docs/PLAN_dashboard_autopilot.md). **NO** stagear ni tocar esos archivos. Stagear
  SIEMPRE solo los archivos nombrados en cada Task (nunca `git add -A` / `git add .`).
- **Commits** terminan con `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **`content` se usa en 3 sitios** dentro de `jarvis_remember` (nota nueva línea
  ~1127, append a nota existente línea ~1118, bullet de card línea ~1137). El seam
  refina `content` una vez ANTES de los tres, así que no hay que tocar cada sitio.
- **Sin estado nuevo:** F4 no crea archivos en `data/` ni en el vault más allá de lo
  que ya escribe `jarvis_remember`.
