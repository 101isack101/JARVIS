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
