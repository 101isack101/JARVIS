from __future__ import annotations

from pathlib import Path

from integrations.google_calendar import CalEvent, events_from_api_items


def test_parse_timed_event():
    items = [{"summary": "Standup",
              "start": {"dateTime": "2026-06-13T09:30:00-06:00"}}]
    out = events_from_api_items(items)
    assert out == [CalEvent(start="09:30", summary="Standup", all_day=False)]


def test_parse_all_day_event():
    items = [{"summary": "Feriado", "start": {"date": "2026-06-13"}}]
    out = events_from_api_items(items)
    assert out[0].all_day is True
    assert out[0].summary == "Feriado"


def test_no_token_returns_empty(tmp_path: Path):
    from integrations.google_calendar import today_events
    assert today_events(credentials_path=tmp_path / "nope.json",
                        token_path=tmp_path / "tok.json") == []
