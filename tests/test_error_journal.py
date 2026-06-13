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
