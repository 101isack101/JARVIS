from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from memory.tools import ToolContext, jarvis_proactive_check
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine


class FakeRAG:
    def search(self, query, top_k=3):
        return []


def _engine_with_pending(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    card = triage_mod.project_card_path(vault, "Polymath IDE")
    card.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(
        vault, card,
        body="# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
        frontmatter={"type": "project-memory-card", "importance": "high", "confidence": "high"},
    )
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    eng.observe(vault, FakeRAG(), "sigamos con Polymath IDE")
    return vault, eng


def test_proactive_check_returns_opportunity(tmp_path):
    vault, eng = _engine_with_pending(tmp_path)
    ctx = ToolContext(vault=vault, rag=FakeRAG(), proactivity=eng)

    out = jarvis_proactive_check(ctx)
    assert out["has_opportunity"] is True
    assert out["opportunity"]["project"] == "Polymath IDE"


def test_proactive_check_empty_when_no_engine(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    ctx = ToolContext(vault=vault, rag=FakeRAG())  # proactivity=None
    out = jarvis_proactive_check(ctx)
    assert out["has_opportunity"] is False


def test_proactive_check_dismiss_flag_suppresses_next(tmp_path):
    vault, eng = _engine_with_pending(tmp_path)
    ctx = ToolContext(vault=vault, rag=FakeRAG(), proactivity=eng)

    first = jarvis_proactive_check(ctx)
    assert first["has_opportunity"] is True
    out = jarvis_proactive_check(ctx, dismissed_last=True)
    assert out["has_opportunity"] is False
