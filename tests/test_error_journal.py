from __future__ import annotations

import json


def test_record_error_writes_jsonl(tmp_path, monkeypatch):
    from telemetry.error_journal import record_error

    journal = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")

    try:
        raise RuntimeError("synthetic failure")
    except RuntimeError as exc:
        record_error("test.source", exc=exc, context={"step": "unit"})

    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["source"] == "test.source"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["context"]["step"] == "unit"
    assert "traceback" in rows[0]


def test_record_error_can_be_disabled(tmp_path, monkeypatch):
    from telemetry.error_journal import record_error

    journal = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "false")

    record_error("test.disabled", message="should not be written")

    assert not journal.exists()


def test_jarvis_log_error_patterns_are_journaled(tmp_path, monkeypatch):
    import jarvis

    journal = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")

    app = object.__new__(jarvis.Jarvis)
    app._log("[CAMERA] error: camera busy")

    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["source"] == "jarvis.log"
    assert "camera busy" in rows[-1]["message"]


def test_tail_errors_returns_last_n_without_traceback_or_caller_path(tmp_path, monkeypatch):
    from telemetry.error_journal import record_error, tail_errors

    journal = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")

    for i in range(3):
        record_error("test.tail", message=f"fallo {i}", context={"token": "sk-live-secret"})

    rows = tail_errors(limit=2, path=tmp_path / "other.jsonl")

    assert [row["message"] for row in rows] == ["fallo 1", "fallo 2"]
    assert all("traceback" not in row for row in rows)
    assert all("context" not in row for row in rows)
    assert all(set(row) == {"ts", "severity", "source", "message"} for row in rows)
