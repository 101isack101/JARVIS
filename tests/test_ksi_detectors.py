import numpy as np

from memory.self_improvement.detectors import (
    detect_contradictions,
    detect_duplicate_clusters,
)
from memory.self_improvement.events import MemoryEvent


def _ev(text, project="JARVIS", section="Facts"):
    return MemoryEvent(id=text[:8], text=text, section=section, project=project)


def _fake_embed(vectors_by_text):
    def embed(texts):
        return np.array([vectors_by_text[t] for t in texts], dtype="float32")
    return embed


def test_clusters_group_near_duplicates():
    events = [_ev("a"), _ev("a-dup"), _ev("b")]
    embed = _fake_embed({
        "a": [1.0, 0.0],
        "a-dup": [0.99, 0.01],
        "b": [0.0, 1.0],
    })
    clusters = detect_duplicate_clusters(events, embed, threshold=0.9, min_size=2)
    assert len(clusters) == 1
    texts = sorted(e.text for e in clusters[0])
    assert texts == ["a", "a-dup"]


def test_no_clusters_when_all_distinct():
    events = [_ev("a"), _ev("b")]
    embed = _fake_embed({"a": [1.0, 0.0], "b": [0.0, 1.0]})
    assert detect_duplicate_clusters(events, embed, threshold=0.9, min_size=2) == []


def test_contradiction_detected_by_negation_polarity():
    e1 = _ev("Migrar el reasoner a la version 4.7")
    e2 = _ev("NO migrar el reasoner a la version 4.7")
    pairs = detect_contradictions([e1, e2])
    assert (e1, e2) in pairs or (e2, e1) in pairs


def test_no_contradiction_across_projects():
    e1 = _ev("usar X", project="A")
    e2 = _ev("no usar X", project="B")
    assert detect_contradictions([e1, e2]) == []
