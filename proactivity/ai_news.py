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
