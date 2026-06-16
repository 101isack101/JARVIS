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
    imp.run(_FakeVault(tmp_path))  # must NOT raise


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
    events = [_ev("aa"), _ev("bb")]
    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(token_budget=0),
        embed_fn=_embed, reasoner=None, event_loader=lambda _v: events,
    )
    imp.run(_FakeVault(tmp_path))
    assert (tmp_path / "self-improvement" / "health.md").exists()
    assert (tmp_path / "self-improvement" / "review-log.md").exists()


def test_run_is_fail_safe_when_embed_raises(tmp_path):
    def boom_embed(texts):
        raise RuntimeError("embed exploded")
    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(),
        embed_fn=boom_embed,
        event_loader=lambda _v: [_ev("hello")],
        reasoner=None,
    )
    imp.run(_FakeVault(tmp_path))  # must NOT raise


def test_run_writes_open_questions_section(tmp_path):
    from memory.self_improvement.config import KnowledgeImproverConfig
    from memory.self_improvement.gaps import gap_id
    from memory.obsidian_vault import ObsidianVault
    from memory import triage as triage_mod

    base = _ev("hecho muy viejo")
    old_event = base.__class__(
        id=base.id, text=base.text, section=base.section, project=base.project,
        learned_at="2026-01-01", confidence=0.8,
    )
    gid = gap_id("stale_fact", old_event.project, old_event.id)

    class _Resp:
        text = '{"%s": "¿Sigue vigente esto?"}' % gid

    class _Reasoner:
        def ask(self, *a, **k):
            return _Resp()

    imp = KnowledgeImprover(
        config=KnowledgeImproverConfig(token_budget=1000),
        embed_fn=_embed, reasoner=_Reasoner(), event_loader=lambda _v: [old_event],
    )
    vault = ObsidianVault(vault_path=tmp_path)
    imp.run(vault)
    card = triage_mod.project_card_path(vault, old_event.project)
    assert card.exists()
    assert "Preguntas abiertas" in card.read_text(encoding="utf-8")
