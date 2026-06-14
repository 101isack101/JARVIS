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


def test_calendar_disabled_by_default():
    cfg = MorningBriefConfig.from_env({})
    assert cfg.calendar_enabled is False


def test_collect_includes_events_when_provider_given(tmp_path):
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


def test_collect_is_fail_safe(monkeypatch, tmp_path):
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
