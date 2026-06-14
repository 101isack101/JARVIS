from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.judge import MergeVerdict, judge_merge


def _ev(text):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project="JARVIS")


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeReasoner:
    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, context_extra="", max_tokens=300):
        self.calls += 1
        return _FakeResp(self._text)


def test_judge_returns_verdict_from_json():
    cluster = [_ev("Hecho A"), _ev("Hecho A repetido")]
    reasoner = _FakeReasoner('Aquí va: {"is_true_duplicate": true, "canonical_text": "Hecho A"} listo')
    verdict = judge_merge(reasoner, cluster, token_budget=1000)
    assert isinstance(verdict, MergeVerdict)
    assert verdict.is_true_duplicate is True
    assert verdict.canonical_text == "Hecho A"
    assert sorted(verdict.member_ids) == sorted(e.id for e in cluster)


def test_judge_skips_when_no_budget():
    reasoner = _FakeReasoner('{"is_true_duplicate": true, "canonical_text": "x"}')
    assert judge_merge(reasoner, [_ev("a"), _ev("b")], token_budget=0) is None
    assert reasoner.calls == 0


def test_judge_returns_none_on_bad_json():
    reasoner = _FakeReasoner("no hay json aquí")
    assert judge_merge(reasoner, [_ev("a"), _ev("b")], token_budget=1000) is None


def test_judge_returns_none_when_reasoner_raises():
    class Boom:
        def ask(self, *a, **k):
            raise RuntimeError("api down")
    assert judge_merge(Boom(), [_ev("a"), _ev("b")], token_budget=1000) is None
