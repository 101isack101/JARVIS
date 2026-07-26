from __future__ import annotations

from pathlib import Path

from proactivity.ai_news import NewsDigest
from proactivity.git_repos import RepoStatus
from proactivity.morning_brief import (
    BriefData,
    MorningBriefConfig,
    _WIKI_HOT_MAX_BYTES,
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


def test_config_wiki_hot_default_and_disable():
    cfg = MorningBriefConfig.from_env({})
    assert cfg.wiki_enabled is True
    assert cfg.wiki_hot_path.name == "hot.md"
    cfg_off = MorningBriefConfig.from_env({"JARVIS_BRIEF_WIKI": "false"})
    assert cfg_off.wiki_enabled is False


def test_collect_reads_wiki_hot(tmp_path):
    hot = tmp_path / "hot.md"
    hot.write_text(
        "# Hot cache\n\n*Reescribir en cada ingesta.*\n\n"
        "**Estado:** 22 páginas, 3 clusters; eje [[IA multipolar]] "
        "y curso [[CS50P|CS50 Python]].\n",
        encoding="utf-8",
    )
    cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path,
                             wiki_hot_path=hot)
    data = collect_morning_brief(vault_block="", cfg=cfg)
    assert data.wiki_hot is not None
    assert "22 páginas" in data.wiki_hot
    # El título y la línea de instrucción no se narran
    assert "Hot cache" not in data.wiki_hot
    assert "Reescribir" not in data.wiki_hot
    # Los wikilinks se aplanan para voz
    assert "[[" not in data.wiki_hot
    assert "IA multipolar" in data.wiki_hot
    assert "CS50 Python" in data.wiki_hot


def test_collect_wiki_hot_missing_file_is_fail_safe(tmp_path):
    cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path,
                             wiki_hot_path=tmp_path / "no-existe.md")
    data = collect_morning_brief(vault_block="", cfg=cfg)
    assert data.wiki_hot is None


def test_collect_wiki_hot_disabled(tmp_path):
    hot = tmp_path / "hot.md"
    hot.write_text("**Estado:** algo\n", encoding="utf-8")
    cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path,
                             wiki_hot_path=hot, wiki_enabled=False)
    data = collect_morning_brief(vault_block="", cfg=cfg)
    assert data.wiki_hot is None


def test_collect_wiki_hot_redacts_secrets_and_bounds_input(tmp_path):
    hot = tmp_path / "hot.md"
    secret = "sk-" + ("x" * 24)
    hot.write_text(f"**Estado:** OPENAI_API_KEY={secret}\n", encoding="utf-8")
    cfg = MorningBriefConfig(repos_root=tmp_path, news_dir=tmp_path, wiki_hot_path=hot)

    data = collect_morning_brief(vault_block="", cfg=cfg)

    assert data.wiki_hot == "**Estado:** OPENAI_API_KEY=[REDACTED]"
    assert secret not in data.wiki_hot

    hot.write_bytes(b"x" * (_WIKI_HOT_MAX_BYTES + 1))
    assert collect_morning_brief(vault_block="", cfg=cfg).wiki_hot is None


def test_render_includes_wiki_section():
    data = BriefData(vault_block="", repos=[], news=None, events=[],
                     wiki_hot="**Estado:** 22 páginas, 3 clusters conectados.")
    prompt = render_brief_prompt(data)
    assert "wiki" in prompt.lower()
    assert "22 páginas" in prompt
    assert "[ARRANQUE]" in prompt
    assert "referencia no confiable" in prompt


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
