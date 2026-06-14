from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.judge import MergeVerdict
from memory.self_improvement.proposer import to_signals
from proactivity.opportunity_queue import _WHAT_BY_KIND, _suggestion_struct


def _ev(text, project="JARVIS"):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project)


def test_merge_verdict_becomes_signal():
    verdict = MergeVerdict(is_true_duplicate=True, canonical_text="Hecho fusionado", member_ids=["a", "b"])
    signals = to_signals([verdict], [], project_by_members={"a": "JARVIS", "b": "JARVIS"})
    assert len(signals) == 1
    sig = signals[0]
    assert sig.kind == "memory_merge"
    assert sig.project == "JARVIS"
    assert sig.payload["snippet"] == "Hecho fusionado"
    assert sig.payload["members"] == ["a", "b"]


def test_false_duplicate_is_skipped():
    verdict = MergeVerdict(is_true_duplicate=False, canonical_text="", member_ids=["a"])
    assert to_signals([verdict], [], project_by_members={"a": "JARVIS"}) == []


def test_contradiction_becomes_supersede_signal():
    a, b = _ev("usar X"), _ev("no usar X")
    signals = to_signals([], [(a, b)], project_by_members={})
    assert len(signals) == 1
    assert signals[0].kind == "memory_supersede"
    assert signals[0].project == "JARVIS"


def test_new_kinds_have_human_labels_and_render():
    assert "memory_merge" in _WHAT_BY_KIND
    assert "memory_supersede" in _WHAT_BY_KIND
    a, b = _ev("usar X"), _ev("no usar X")
    sig = to_signals([], [(a, b)], project_by_members={})[0]
    struct = _suggestion_struct(sig)
    assert struct["what"] == _WHAT_BY_KIND["memory_supersede"]
