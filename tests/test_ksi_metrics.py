from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.metrics import compute_health, write_health
from memory.self_improvement.review_log import append_review_log


def _ev(text, project="JARVIS", conf=0.6):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project, confidence=conf)


def test_compute_health_counts():
    events = [_ev("a"), _ev("b"), _ev("c", project="X")]
    clusters = [[events[0], events[1]]]
    health = compute_health(events, clusters, contradictions=[])
    assert health["total_events"] == 3
    assert health["projects"] == 2
    assert health["duplicate_clusters"] == 1
    assert health["open_contradictions"] == 0
    assert 0.0 <= health["avg_confidence"] <= 1.0


def test_write_health_creates_file(tmp_path):
    path = write_health(tmp_path, {"total_events": 5, "avg_confidence": 0.7})
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "total_events" in text and "5" in text


def test_append_review_log_appends(tmp_path):
    p1 = append_review_log(tmp_path, ["anotó card JARVIS", "propuso fusión a+b"])
    p2 = append_review_log(tmp_path, ["otra corrida"])
    assert p1 == p2
    text = p1.read_text(encoding="utf-8")
    assert "anotó card JARVIS" in text
    assert "otra corrida" in text
