import math

from telemetry.persistence import UsagePersistence
from telemetry.tracker import TokenTracker


def test_usage_persistence_flushes_deltas_not_duplicate_snapshots(tmp_path):
    tracker = TokenTracker()
    persistence = UsagePersistence(tmp_path / "usage.db", session_id="s1")

    tracker.record("claude-sonnet-4-6", output_tokens=1_000)
    first_rows = persistence.flush_snapshot(tracker)
    first_total = persistence.total_cost_window(hours_back=24)

    second_rows = persistence.flush_snapshot(tracker)
    second_total = persistence.total_cost_window(hours_back=24)

    tracker.record("claude-sonnet-4-6", output_tokens=500)
    third_rows = persistence.flush_snapshot(tracker)
    third_total = persistence.total_cost_window(hours_back=24)

    assert first_rows == 1
    assert second_rows == 0
    assert third_rows == 1
    assert math.isclose(first_total, second_total)
    assert math.isclose(third_total, tracker.snapshot().total_cost_usd)


def test_usage_persistence_groups_by_provider_and_can_exclude_session(tmp_path):
    tracker = TokenTracker()
    persistence = UsagePersistence(tmp_path / "usage.db", session_id="old")
    tracker.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=1_000)
    persistence.flush_snapshot(tracker)

    included = persistence.cost_by_provider_window(hours_back=24)
    excluded = persistence.cost_by_provider_window(hours_back=24, exclude_session_id="old")

    assert included["gemini"] > 0
    assert excluded["gemini"] == 0
