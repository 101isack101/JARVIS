import json

from tests.test_web_overlay_headless import _make_overlay


def _mission(status="running"):
    return {
        "id": "msn_test",
        "goal": "Evaluar el estado técnico de JARVIS",
        "status": status,
        "autonomy_level": "L1",
        "created_at": "2026-07-11T12:00:00Z",
        "updated_at": "2026-07-11T12:00:01Z",
        "finished_at": None,
        "completed_steps": 1,
        "total_steps": 3,
        "error_code": None,
        "final_summary": None,
    }


def test_mission_event_updates_snapshot_and_emits_sse():
    overlay = _make_overlay()
    try:
        client = overlay.register_client()
        overlay.record_mission_event({
            "id": 7,
            "mission_id": "msn_test",
            "step_id": "stp_one",
            "event_type": "step.completed",
            "level": "info",
            "payload": {"summary": "1 archivo leído"},
            "created_at": "2026-07-11T12:00:01Z",
            "mission": _mission(),
        })
        first = json.loads(client.get(timeout=1))
        second = json.loads(client.get(timeout=1))
        assert {first["command"], second["command"]} == {"missionUpdated", "missionEvent"}
        snapshot = overlay.snapshot()
        assert snapshot["missions"][0]["id"] == "msn_test"
        assert snapshot["missionEvents"][0]["event_type"] == "step.completed"
        assert "mission" not in snapshot["missionEvents"][0]
    finally:
        overlay.close()


def test_set_missions_is_bounded():
    overlay = _make_overlay()
    try:
        overlay.set_missions([{**_mission(), "id": f"msn_{i}"} for i in range(30)])
        assert len(overlay.snapshot()["missions"]) == 20
    finally:
        overlay.close()


def test_mission_detail_is_bounded_and_emits_sse():
    overlay = _make_overlay()
    try:
        client = overlay.register_client()
        overlay.set_mission_detail({
            "ok": True,
            "mission": {**_mission(), "final_summary": "x" * 5000},
            "steps": [{"id": f"stp_{i}"} for i in range(12)],
            "events": [{"id": i, "event_type": "step.completed"} for i in range(60)],
        })
        emitted = json.loads(client.get(timeout=1))
        assert emitted["command"] == "missionDetail"
        detail = overlay.snapshot()["missionDetail"]
        assert len(detail["steps"]) == 8
        assert len(detail["events"]) == 50
        assert len(detail["mission"]["final_summary"]) == 4000
        assert detail["mission"]["final_summary"].endswith("…")
    finally:
        overlay.close()
