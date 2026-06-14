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
    calendar_enabled: bool = False

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
            calendar_enabled=_bool("JARVIS_BRIEF_CALENDAR", d.calendar_enabled),
        )


# CalEvent es de Fase 2; en Fase 1 events siempre es [].
@dataclass(frozen=True)
class BriefData:
    vault_block: str
    repos: list[RepoStatus]
    news: NewsDigest | None
    events: list = field(default_factory=list)


def collect_morning_brief(*, vault_block: str,
                          cfg: MorningBriefConfig,
                          events_provider=None) -> BriefData:
    """Llama a cada fuente envuelta en try/except. Nunca lanza.

    `events_provider` es un callable opcional que devuelve la agenda (Fase 2);
    se inyecta como dependencia para poder testear sin red.
    """
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
