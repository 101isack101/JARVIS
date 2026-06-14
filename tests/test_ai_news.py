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
