# Briefing Matutino Hablado — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que JARVIS narre en voz, en cada arranque, un briefing matutino con pendientes del vault, estado de repos git, titulares de IA del día y (Fase 2) la agenda del calendario.

**Architecture:** Tres recolectores fail-safe aislados (`git_repos`, `ai_news`, `google_calendar`) feedean a un orquestador puro (`morning_brief`) que renderiza un prompt; `jarvis.py` lo dispara una vez por proceso en `_on_connected` vía `session.send_text`, forzando una respuesta hablada de Gemini. Cada recolector nunca propaga excepciones.

**Tech Stack:** Python 3.11, pytest, subprocess+git CLI, google-api-python-client (Fase 2). Convenciones del repo: dataclasses `frozen` con `from_env`, `from __future__ import annotations`, tests en `tests/test_*.py`.

**Comando de tests:** desde `JARVIS/`: `python -m pytest tests/<archivo> -v`

---

## File Structure

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `proactivity/git_repos.py` | crear | `RepoStatus` + `scan_repo_status()` — estado git por repo |
| `proactivity/ai_news.py` | crear | `NewsDigest` + `latest_ai_news()` — titulares de la nota IA más reciente |
| `proactivity/morning_brief.py` | crear | `MorningBriefConfig` + `BriefData` + `collect_morning_brief()` + `render_brief_prompt()` |
| `integrations/__init__.py` | crear | paquete (Fase 2) |
| `integrations/google_calendar.py` | crear (Fase 2) | `CalEvent` + `today_events()` |
| `jarvis.py` | modificar | flag `_briefing_sent` + disparo en `_on_connected` |
| `tests/test_git_repos.py` | crear | tests de `git_repos` |
| `tests/test_ai_news.py` | crear | tests de `ai_news` |
| `tests/test_morning_brief.py` | crear | tests de `morning_brief` |
| `tests/test_google_calendar.py` | crear (Fase 2) | tests de calendario |
| `.env.example`, `CHANGELOG.md` | modificar | documentar config |

Dependencias entre tareas: **Task 1 y Task 2 son independientes** (paralelizables). Task 3 depende de los tipos de 1+2. Task 4 depende de 3. Task 5 (Fase 2) es independiente. Task 6 depende de 5+3.

---

## FASE 1 — Briefing local (vault + git + noticias)

### Task 1: `git_repos.py` — estado de repos

**Files:**
- Create: `proactivity/git_repos.py`
- Test: `tests/test_git_repos.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_git_repos.py
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proactivity.git_repos import RepoStatus, scan_repo_status


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_clean_repo_is_excluded(tmp_path: Path):
    _make_repo(tmp_path, "clean")
    result = scan_repo_status(tmp_path)
    assert result == []


def test_dirty_repo_is_reported(tmp_path: Path):
    repo = _make_repo(tmp_path, "dirty")
    (repo / "new.txt").write_text("y", encoding="utf-8")
    result = scan_repo_status(tmp_path)
    assert len(result) == 1
    assert result[0].name == "dirty"
    assert result[0].dirty >= 1
    assert isinstance(result[0], RepoStatus)


def test_non_git_dir_ignored(tmp_path: Path):
    (tmp_path / "not_a_repo").mkdir()
    assert scan_repo_status(tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path):
    assert scan_repo_status(tmp_path / "nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_git_repos.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.git_repos'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/git_repos.py
"""Escaneo fail-safe del estado git de los repos bajo una raíz.

Devuelve solo repos con algo que reportar (cambios sin commitear o sin push).
Ningún método propaga excepciones: el briefing nunca debe romper el arranque.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoStatus:
    name: str
    dirty: int   # nº de líneas de `git status --porcelain`
    ahead: int   # commits sin push (0 si no hay upstream)
    branch: str


def _git(repo: Path, *args: str, timeout_s: float) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout_s,
    )
    return out.stdout if out.returncode == 0 else ""


def _status_for(repo: Path, timeout_s: float) -> RepoStatus | None:
    try:
        porcelain = _git(repo, "status", "--porcelain", timeout_s=timeout_s)
        dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD",
                      timeout_s=timeout_s).strip() or "?"
        ahead_raw = _git(repo, "rev-list", "--count", "@{u}..HEAD",
                         timeout_s=timeout_s).strip()
        ahead = int(ahead_raw) if ahead_raw.isdigit() else 0
        if dirty == 0 and ahead == 0:
            return None
        return RepoStatus(name=repo.name, dirty=dirty, ahead=ahead, branch=branch)
    except Exception:
        return None


def scan_repo_status(root: Path, *, max_repos: int = 40,
                     per_repo_timeout_s: float = 3.0) -> list[RepoStatus]:
    try:
        root = Path(root)
        if not root.is_dir():
            return []
        results: list[RepoStatus] = []
        for child in sorted(root.iterdir()):
            if len(results) >= max_repos:
                break
            if not (child / ".git").exists():
                continue
            st = _status_for(child, per_repo_timeout_s)
            if st is not None:
                results.append(st)
        return results
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_git_repos.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add proactivity/git_repos.py tests/test_git_repos.py
git commit -m "feat(proactivity): git_repos scanner para el briefing matutino"
```

---

### Task 2: `ai_news.py` — titulares de la nota IA más reciente

**Files:**
- Create: `proactivity/ai_news.py`
- Test: `tests/test_ai_news.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_news.py
from __future__ import annotations

from datetime import date
from pathlib import Path

from proactivity.ai_news import NewsDigest, latest_ai_news

_NOTE = """﻿---
tags:
  - noticias-ia
date: 2026-06-09
---

# Noticias de IA

## 1. PRIMER TITULAR IMPORTANTE
> Fuente: X
texto

## 2. SEGUNDO TITULAR
> Fuente: Y
texto

## 3. TERCER TITULAR
texto

## 4. CUARTO TITULAR
texto
"""


def test_returns_top_n_headlines(tmp_path: Path):
    (tmp_path / "2026-06-09.md").write_text(_NOTE, encoding="utf-8")
    digest = latest_ai_news(tmp_path, max_items=3,
                            today=date(2026, 6, 9))
    assert isinstance(digest, NewsDigest)
    assert digest.headlines == [
        "PRIMER TITULAR IMPORTANTE",
        "SEGUNDO TITULAR",
        "TERCER TITULAR",
    ]
    assert digest.date == "2026-06-09"
    assert digest.age_days == 0


def test_picks_most_recent_note(tmp_path: Path):
    (tmp_path / "2026-06-01.md").write_text(_NOTE, encoding="utf-8")
    (tmp_path / "2026-06-09.md").write_text(_NOTE, encoding="utf-8")
    digest = latest_ai_news(tmp_path, today=date(2026, 6, 12))
    assert digest.date == "2026-06-09"
    assert digest.age_days == 3


def test_empty_folder_returns_none(tmp_path: Path):
    assert latest_ai_news(tmp_path) is None


def test_missing_folder_returns_none(tmp_path: Path):
    assert latest_ai_news(tmp_path / "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_news.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.ai_news'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/ai_news.py
"""Lectura fail-safe de la nota de Noticias IA más reciente.

Reutiliza el artefacto (.md) que produce el AI News Agent en Obsidian.
Acoplamiento por formato (`## N. Título`), no por código. Nunca lanza.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_HEADLINE = re.compile(r"^##\s*\d+\.\s*(.+?)\s*$", re.MULTILINE)
_DATE_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class NewsDigest:
    date: str
    age_days: int
    headlines: list[str]


def _note_date(path: Path) -> date | None:
    m = _DATE_NAME.search(path.stem)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def latest_ai_news(news_folder: Path, *, max_items: int = 3,
                   max_age_days: int = 3,
                   today: date | None = None) -> NewsDigest | None:
    try:
        folder = Path(news_folder)
        if not folder.is_dir():
            return None
        dated = [(d, p) for p in folder.glob("*.md")
                 if (d := _note_date(p)) is not None]
        if not dated:
            return None
        note_date, note_path = max(dated, key=lambda t: t[0])
        text = note_path.read_text(encoding="utf-8-sig")
        headlines = _HEADLINE.findall(text)[:max(0, max_items)]
        if not headlines:
            return None
        ref = today or datetime.now().date()
        age = (ref - note_date).days
        return NewsDigest(date=note_date.isoformat(),
                          age_days=age, headlines=headlines)
    except Exception:
        return None
```

Nota: `max_age_days` se acepta para uso del render (decidir si aclarar
antigüedad); `latest_ai_news` siempre devuelve la más reciente con su `age_days`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_news.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add proactivity/ai_news.py tests/test_ai_news.py
git commit -m "feat(proactivity): ai_news lee la nota IA mas reciente del vault"
```

---

### Task 3: `morning_brief.py` — orquestador + render

**Files:**
- Create: `proactivity/morning_brief.py`
- Test: `tests/test_morning_brief.py`

Depende de los tipos `RepoStatus` (Task 1) y `NewsDigest` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_morning_brief.py
from __future__ import annotations

from pathlib import Path

from proactivity.ai_news import NewsDigest
from proactivity.git_repos import RepoStatus
from proactivity.morning_brief import (
    BriefData,
    MorningBriefConfig,
    collect_morning_brief,
    render_brief_prompt,
)


def test_config_from_env_defaults():
    cfg = MorningBriefConfig.from_env({})
    assert cfg.enabled is True
    assert cfg.news_items == 3


def test_config_from_env_overrides():
    cfg = MorningBriefConfig.from_env({
        "JARVIS_MORNING_BRIEF": "false",
        "JARVIS_BRIEF_NEWS_ITEMS": "5",
    })
    assert cfg.enabled is False
    assert cfg.news_items == 5


def test_collect_is_fail_safe(monkeypatch, tmp_path):
    # git scanner que explota -> no debe propagar
    import proactivity.morning_brief as mb

    def boom(*a, **k):
        raise RuntimeError("git down")

    monkeypatch.setattr(mb, "scan_repo_status", boom)
    monkeypatch.setattr(mb, "latest_ai_news", lambda *a, **k: None)
    cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path)
    data = collect_morning_brief(vault_block="", cfg=cfg)
    assert isinstance(data, BriefData)
    assert data.repos == []
    assert data.news is None


def test_render_all_empty_is_short_greeting():
    data = BriefData(vault_block="", repos=[], news=None, events=[])
    prompt = render_brief_prompt(data)
    assert "Buenos días Isaac" in prompt


def test_render_includes_sections():
    data = BriefData(
        vault_block="═ BRIEFING ═\n- [proj] pendiente X",
        repos=[RepoStatus(name="JARVIS", dirty=3, ahead=1, branch="main")],
        news=NewsDigest(date="2026-06-09", age_days=0,
                        headlines=["TITULAR UNO", "TITULAR DOS"]),
        events=[],
    )
    prompt = render_brief_prompt(data)
    assert "JARVIS" in prompt
    assert "TITULAR UNO" in prompt
    assert "pendiente X" in prompt
    assert "[ARRANQUE]" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_morning_brief.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.morning_brief'`

- [ ] **Step 3: Write minimal implementation**

```python
# proactivity/morning_brief.py
"""Orquestador del briefing matutino (prompt-first).

Junta las fuentes (vault, git, noticias, calendario) en datos estructurados y
renderiza el prompt de arranque que se envía a Gemini con send_text. No conoce
Gemini ni voz: recibe fuentes, devuelve texto. Fail-safe absoluto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .ai_news import NewsDigest, latest_ai_news
from .git_repos import RepoStatus, scan_repo_status

_TRUE = {"1", "true", "yes", "on"}

_DEFAULT_REPOS_ROOT = r"C:\Users\Isaac\Desktop\PROYECTOS"
_DEFAULT_NEWS_DIR = r"H:\Obsidian ClaudeCode\Obsidian Claude Code\Noticias IA"


@dataclass(frozen=True)
class MorningBriefConfig:
    enabled: bool = True
    repos_root: Path = field(default_factory=lambda: Path(_DEFAULT_REPOS_ROOT))
    news_dir: Path = field(default_factory=lambda: Path(_DEFAULT_NEWS_DIR))
    news_items: int = 3
    news_max_age_days: int = 3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MorningBriefConfig":
        env = env if env is not None else os.environ
        d = cls()

        def _bool(key: str, default: bool) -> bool:
            raw = env.get(key)
            return default if raw is None else raw.strip().lower() in _TRUE

        def _int(key: str, default: int) -> int:
            try:
                return int(str(env.get(key, default)).strip())
            except (ValueError, TypeError):
                return default

        return cls(
            enabled=_bool("JARVIS_MORNING_BRIEF", d.enabled),
            repos_root=Path(env.get("JARVIS_BRIEF_REPOS_ROOT", str(d.repos_root))),
            news_dir=Path(env.get("JARVIS_BRIEF_NEWS_DIR", str(d.news_dir))),
            news_items=_int("JARVIS_BRIEF_NEWS_ITEMS", d.news_items),
            news_max_age_days=_int("JARVIS_BRIEF_NEWS_MAX_AGE", d.news_max_age_days),
        )


# CalEvent es de Fase 2; en Fase 1 events siempre es [].
@dataclass(frozen=True)
class BriefData:
    vault_block: str
    repos: list[RepoStatus]
    news: NewsDigest | None
    events: list = field(default_factory=list)


def collect_morning_brief(*, vault_block: str,
                          cfg: MorningBriefConfig) -> BriefData:
    """Llama a cada fuente envuelta en try/except. Nunca lanza."""
    try:
        repos = scan_repo_status(cfg.repos_root)
    except Exception:
        repos = []
    try:
        news = latest_ai_news(cfg.news_dir, max_items=cfg.news_items,
                              max_age_days=cfg.news_max_age_days)
    except Exception:
        news = None
    return BriefData(vault_block=(vault_block or "").strip(),
                     repos=repos, news=news, events=[])


def _repos_line(repos: list[RepoStatus]) -> str:
    parts = []
    for r in repos:
        bits = []
        if r.dirty:
            bits.append(f"{r.dirty} sin commitear")
        if r.ahead:
            bits.append(f"{r.ahead} sin push")
        parts.append(f"{r.name} ({', '.join(bits)})")
    return "; ".join(parts)


def _news_block(news: NewsDigest, max_age_days: int) -> str:
    lines = [f"  · {h}" for h in news.headlines]
    if news.age_days > max_age_days:
        prefix = f"Noticias de IA (de hace {news.age_days} días, {news.date}):"
    else:
        prefix = "Noticias de IA del día:"
    return prefix + "\n" + "\n".join(lines)


def render_brief_prompt(data: BriefData,
                        max_age_days: int = 3) -> str:
    """Prompt de arranque: datos + instrucción de tono (prompt-first)."""
    sections: list[str] = []
    if data.events:
        ev = "; ".join(getattr(e, "summary", str(e)) for e in data.events)
        sections.append(f"Agenda de hoy: {ev}")
    if data.vault_block:
        sections.append(f"Pendientes del vault:\n{data.vault_block}")
    if data.repos:
        sections.append(f"Repos con cambios: {_repos_line(data.repos)}")
    if data.news:
        sections.append(_news_block(data.news, max_age_days))

    if not sections:
        return ("[ARRANQUE] Saluda brevemente: di solo «Buenos días Isaac, "
                "JARVIS a tu servicio». Sin más.")

    datos = "\n\n".join(sections)
    return (
        "[ARRANQUE] Es el inicio de sesión. Saluda a Isaac por su nombre y dale "
        "su briefing matutino con estos datos. Háblalo fluido y natural, una "
        "frase por tema, en tono cercano; NO recites listas literales ni leas "
        "URLs. Cierra invitándolo a empezar el día.\n\n"
        f"=== DATOS DEL BRIEFING ===\n{datos}\n=== FIN DATOS ==="
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_morning_brief.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add proactivity/morning_brief.py tests/test_morning_brief.py
git commit -m "feat(proactivity): morning_brief orquestador + render del prompt"
```

---

### Task 4: Wiring en `jarvis.py` + config + docs

**Files:**
- Modify: `jarvis.py` (`__init__` ~451-465 y `_on_connected` ~1039-1056)
- Modify: `.env.example`, `CHANGELOG.md`

- [ ] **Step 1: Inicializar el flag y la config en `__init__`**

En `jarvis.py`, en el bloque de Fase 3 — Proactividad (justo después de
`self.proactivity = None` en ~452), añadir el import al principio del archivo
(junto a los otros `from proactivity...`):

```python
from proactivity.morning_brief import (
    MorningBriefConfig,
    collect_morning_brief,
    render_brief_prompt,
)
```

Y en `__init__`, tras inicializar `self.proactivity`:

```python
        # Briefing matutino hablado: se dispara una vez por proceso en el
        # primer connect (no en reconexiones). Flag de instancia = idempotencia.
        self._briefing_sent = False
        self._morning_cfg = MorningBriefConfig.from_env()
        self._briefing_block_cache = briefing_block  # vault block ya calculado
```

- [ ] **Step 2: Disparar el briefing en `_on_connected`**

En `jarvis.py`, al final de `_on_connected` (después del bloque LIBRE, ~1056),
añadir:

```python
        # Briefing matutino hablado: solo el primer connect del proceso.
        if not self._briefing_sent and self._morning_cfg.enabled:
            self._briefing_sent = True  # marcar antes para no reintentar en fallo
            try:
                if self._gemini_budget_available("[BRIEF] morning"):
                    data = collect_morning_brief(
                        vault_block=self._briefing_block_cache,
                        cfg=self._morning_cfg,
                    )
                    prompt = render_brief_prompt(
                        data, max_age_days=self._morning_cfg.news_max_age_days)
                    self._log("[BRIEF] enviando briefing matutino hablado")
                    self.session.send_text(prompt)
            except Exception as exc:
                self._log(f"[WARN] briefing matutino falló: {exc}")
```

- [ ] **Step 3: Verificar que el módulo carga sin romper imports**

Run: `python -c "import jarvis"`
Expected: sin tracebacks (puede imprimir logs/warnings de entorno, pero no
`ImportError`/`SyntaxError`).

- [ ] **Step 4: Smoke test del render end-to-end (sin red)**

Run:
```bash
python -c "from proactivity.morning_brief import MorningBriefConfig, collect_morning_brief, render_brief_prompt; cfg=MorningBriefConfig.from_env(); d=collect_morning_brief(vault_block='- [demo] algo', cfg=cfg); print(render_brief_prompt(d)[:400])"
```
Expected: imprime un prompt `[ARRANQUE]...` con las secciones que existan en tu
máquina (repos/noticias reales si están disponibles).

- [ ] **Step 5: Documentar config en `.env.example`**

Añadir al final de `.env.example`:

```bash
# --- Briefing matutino hablado ---
JARVIS_MORNING_BRIEF=true
JARVIS_BRIEF_REPOS_ROOT=C:\Users\Isaac\Desktop\PROYECTOS
JARVIS_BRIEF_NEWS_DIR=H:\Obsidian ClaudeCode\Obsidian Claude Code\Noticias IA
JARVIS_BRIEF_NEWS_ITEMS=3
JARVIS_BRIEF_NEWS_MAX_AGE=3
# Fase 2 (calendario): JARVIS_BRIEF_CALENDAR=true y GOOGLE_CALENDAR_CREDENTIALS=ruta\client_secret.json
```

- [ ] **Step 6: Documentar en `CHANGELOG.md`**

Bajo `## Unreleased`, añadir un bullet:

```markdown
- Briefing matutino hablado: al arrancar, JARVIS narra en voz un resumen con
  pendientes del vault, estado de los repos git y titulares de IA del día
  (reutiliza las notas del AI News Agent). Idempotente por proceso (no se repite
  en reconexiones), fail-safe y desactivable con `JARVIS_MORNING_BRIEF=false`.
```

- [ ] **Step 7: Correr toda la suite de proactividad**

Run: `python -m pytest tests/test_git_repos.py tests/test_ai_news.py tests/test_morning_brief.py tests/test_proactivity_engine.py -v`
Expected: todos PASS (no romper la proactividad existente).

- [ ] **Step 8: Commit**

```bash
git add jarvis.py .env.example CHANGELOG.md
git commit -m "feat(jarvis): disparar briefing matutino hablado en el primer connect"
```

---

## FASE 2 — Calendario (Google Calendar API)

### Task 5: `integrations/google_calendar.py`

**Files:**
- Create: `integrations/__init__.py` (vacío)
- Create: `integrations/google_calendar.py`
- Test: `tests/test_google_calendar.py`
- Modify: `requirements.txt`, `.gitignore`

- [ ] **Step 1: Añadir dependencia y proteger el token**

En `requirements.txt` añadir:
```
google-api-python-client
google-auth-oauthlib
```
En `.gitignore` añadir:
```
data/google_token.json
```

- [ ] **Step 2: Write the failing test (cliente mockeado, sin red)**

```python
# tests/test_google_calendar.py
from __future__ import annotations

from pathlib import Path

from integrations.google_calendar import CalEvent, events_from_api_items


def test_parse_timed_event():
    items = [{"summary": "Standup",
              "start": {"dateTime": "2026-06-13T09:30:00-06:00"}}]
    out = events_from_api_items(items)
    assert out == [CalEvent(start="09:30", summary="Standup", all_day=False)]


def test_parse_all_day_event():
    items = [{"summary": "Feriado", "start": {"date": "2026-06-13"}}]
    out = events_from_api_items(items)
    assert out[0].all_day is True
    assert out[0].summary == "Feriado"


def test_no_token_returns_empty(tmp_path: Path):
    from integrations.google_calendar import today_events
    assert today_events(credentials_path=tmp_path / "nope.json",
                        token_path=tmp_path / "tok.json") == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_google_calendar.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'integrations.google_calendar'`

- [ ] **Step 4: Write minimal implementation**

```python
# integrations/__init__.py
```
(archivo vacío)

```python
# integrations/google_calendar.py
"""Acceso fail-safe a Google Calendar para el briefing matutino.

OAuth read-only. El token se cachea fuera de git. Sin credenciales/token o sin
red -> devuelve []. La lógica de parseo se aísla en events_from_api_items para
poder testearla sin red.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass(frozen=True)
class CalEvent:
    start: str       # "HH:MM" o "" si all_day
    summary: str
    all_day: bool


def events_from_api_items(items: list[dict]) -> list[CalEvent]:
    out: list[CalEvent] = []
    for it in items:
        start = it.get("start", {}) or {}
        summary = (it.get("summary") or "(sin título)").strip()
        if "date" in start and "dateTime" not in start:
            out.append(CalEvent(start="", summary=summary, all_day=True))
        else:
            raw = start.get("dateTime", "")
            hhmm = ""
            try:
                hhmm = datetime.fromisoformat(raw).strftime("%H:%M")
            except ValueError:
                hhmm = ""
            out.append(CalEvent(start=hhmm, summary=summary, all_day=False))
    return out


def _load_credentials(credentials_path: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds
    if not credentials_path.exists():
        return None
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path), _SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def today_events(*, credentials_path: Path, token_path: Path) -> list[CalEvent]:
    try:
        credentials_path = Path(credentials_path)
        token_path = Path(token_path)
        creds = _load_credentials(credentials_path, token_path)
        if creds is None:
            return []
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=creds,
                        cache_discovery=False)
        now = datetime.now()
        start = datetime.combine(now.date(), time.min).astimezone(timezone.utc)
        end = datetime.combine(now.date(), time.max).astimezone(timezone.utc)
        resp = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime",
        ).execute()
        return events_from_api_items(resp.get("items", []))
    except Exception:
        return []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_google_calendar.py -v`
Expected: PASS (3 passed). (Los imports de google solo se cargan dentro de las
funciones, así que `events_from_api_items` y `today_events` sin token corren sin
tener los paquetes instalados.)

- [ ] **Step 6: Commit**

```bash
git add integrations/__init__.py integrations/google_calendar.py tests/test_google_calendar.py requirements.txt .gitignore
git commit -m "feat(integrations): google_calendar fail-safe (Fase 2 briefing)"
```

---

### Task 6: Integrar calendario en el briefing

**Files:**
- Modify: `proactivity/morning_brief.py`
- Modify: `tests/test_morning_brief.py`
- Modify: `.env.example`

- [ ] **Step 1: Extender la config y el collect (test primero)**

Añadir a `tests/test_morning_brief.py`:

```python
def test_calendar_disabled_by_default():
    cfg = MorningBriefConfig.from_env({})
    assert cfg.calendar_enabled is False


def test_collect_includes_events_when_provider_given(tmp_path):
    import proactivity.morning_brief as mb
    mb_cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path,
                                calendar_enabled=True)
    fake_event = type("E", (), {"summary": "Reunión", "start": "10:00",
                                 "all_day": False})()
    data = collect_morning_brief(
        vault_block="", cfg=mb_cfg,
        events_provider=lambda: [fake_event],
    )
    assert len(data.events) == 1
    assert data.events[0].summary == "Reunión"
```

- [ ] **Step 2: Run para ver fallar**

Run: `python -m pytest tests/test_morning_brief.py -k calendar -v`
Expected: FAIL (`calendar_enabled` no existe / `events_provider` no aceptado).

- [ ] **Step 3: Implementar en `morning_brief.py`**

Añadir campo a `MorningBriefConfig`:
```python
    calendar_enabled: bool = False
```
Y en `from_env`, dentro del `return cls(...)`:
```python
            calendar_enabled=_bool("JARVIS_BRIEF_CALENDAR", d.calendar_enabled),
```

Cambiar la firma de `collect_morning_brief` para aceptar un proveedor opcional
(inyección de dependencia → testeable sin red):
```python
def collect_morning_brief(*, vault_block: str,
                          cfg: MorningBriefConfig,
                          events_provider=None) -> BriefData:
    try:
        repos = scan_repo_status(cfg.repos_root)
    except Exception:
        repos = []
    try:
        news = latest_ai_news(cfg.news_dir, max_items=cfg.news_items,
                              max_age_days=cfg.news_max_age_days)
    except Exception:
        news = None
    events = []
    if cfg.calendar_enabled and events_provider is not None:
        try:
            events = events_provider() or []
        except Exception:
            events = []
    return BriefData(vault_block=(vault_block or "").strip(),
                     repos=repos, news=news, events=events)
```

(El render ya soporta `data.events` desde la Fase 1; usa `e.summary`.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_morning_brief.py -v`
Expected: PASS (todos, incluidos los 2 nuevos).

- [ ] **Step 5: Cablear el proveedor real en `jarvis.py`**

En `_on_connected`, donde se llama `collect_morning_brief`, pasar el proveedor
cuando el calendario esté activo:

```python
                    events_provider = None
                    if self._morning_cfg.calendar_enabled:
                        from integrations.google_calendar import today_events
                        cred = Path(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", ""))
                        events_provider = lambda: today_events(
                            credentials_path=cred,
                            token_path=Path("data") / "google_token.json",
                        )
                    data = collect_morning_brief(
                        vault_block=self._briefing_block_cache,
                        cfg=self._morning_cfg,
                        events_provider=events_provider,
                    )
```

- [ ] **Step 6: Documentar en `.env.example`**

Reemplazar la línea comentada de Fase 2 por:
```bash
JARVIS_BRIEF_CALENDAR=false
GOOGLE_CALENDAR_CREDENTIALS=H:\secrets\google_client_secret.json
```

- [ ] **Step 7: Commit**

```bash
git add proactivity/morning_brief.py tests/test_morning_brief.py jarvis.py .env.example
git commit -m "feat(proactivity): integrar calendario en el briefing (Fase 2)"
```

---

## Self-Review (completado por el autor del plan)

- **Cobertura del spec:** disparo hablado garantizado (Task 4 step 2), idempotencia por proceso (flag, Task 4 step 1), 4 fuentes (Tasks 1,2,3,5/6), fail-safe en cada recolector y en el wiring (try/except en todos), config env (Tasks 3,4,6), calendario en Fase 2 (Tasks 5,6), reutilización del AI News Agent (Task 2), frescura con `max_age_days` (Task 2 + render Task 3). ✔ sin huecos.
- **Placeholders:** sin TBD/TODO; todo el código está completo en cada step. ✔
- **Consistencia de tipos:** `RepoStatus(name,dirty,ahead,branch)`, `NewsDigest(date,age_days,headlines)`, `CalEvent(start,summary,all_day)`, `BriefData(vault_block,repos,news,events)`, `MorningBriefConfig(...)` usados igual en módulos y tests. `collect_morning_brief(*, vault_block, cfg, events_provider=None)` consistente entre Task 3 y Task 6. ✔
