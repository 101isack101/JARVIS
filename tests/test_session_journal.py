"""Tests para memory/session_journal.py — journal JSONL append-only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.session_journal import SessionJournal


def test_append_and_read_roundtrip(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn("hola jarvis", "buenas tardes señor")
    journal.append_turn("que hora es", "las tres")
    turns = journal.read_turns()
    assert len(turns) == 2
    assert turns[0]["user"] == "hola jarvis"
    assert turns[0]["jarvis"] == "buenas tardes señor"
    assert "ts" in turns[0]
    assert turns[1]["user"] == "que hora es"


def test_has_pending_and_turn_count(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    assert journal.has_pending() is False
    assert journal.turn_count() == 0
    journal.append_turn("uno", "dos")
    assert journal.has_pending() is True
    assert journal.turn_count() == 1


def test_clear_removes_journal(tmp_path: Path):
    path = tmp_path / "session_journal.jsonl"
    journal = SessionJournal(path)
    journal.append_turn("uno", "dos")
    journal.clear()
    assert journal.has_pending() is False
    assert journal.turn_count() == 0
    assert not path.exists()


def test_secrets_are_redacted_on_write(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn(
        "mi token es ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz",
    )
    raw = (tmp_path / "session_journal.jsonl").read_text(encoding="utf-8")
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in raw
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz" not in raw
    assert "REDACTED" in raw


def test_corrupt_line_is_skipped(tmp_path: Path):
    path = tmp_path / "session_journal.jsonl"
    journal = SessionJournal(path)
    journal.append_turn("buena", "linea")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{esto no es json valido\n")
    journal.append_turn("otra", "buena")
    turns = journal.read_turns()
    assert len(turns) == 2
    assert turns[0]["user"] == "buena"
    assert turns[1]["user"] == "otra"


def test_empty_turn_is_ignored(tmp_path: Path):
    journal = SessionJournal(tmp_path / "session_journal.jsonl")
    journal.append_turn("   ", "")
    assert journal.turn_count() == 0
    assert journal.has_pending() is False
