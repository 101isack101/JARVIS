import tkinter as tk

import pytest

from security.approvals import PendingAction
from overlay.approval_dialog import ApprovalDialog
from overlay.window import JarvisOverlay
from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


def _make_overlay(on_close=None):
    try:
        return JarvisOverlay(TokenTracker(), BudgetGate(), on_close=on_close)
    except tk.TclError as exc:
        pytest.skip(f"Tkinter display unavailable: {exc}")


def test_memory_tool_start_does_not_keep_raw_args():
    overlay = _make_overlay()
    try:
        secret_content = "private body " * 100
        overlay.record_memory_tool_start(
            "jarvis_remember",
            {
                "title": "Sensitive note",
                "content": secret_content,
                "tags": ["private"],
            },
        )

        active = overlay._memory_active["jarvis_remember"]

        assert "args" not in active
        assert secret_content not in str(active)
        assert "Sensitive note" in active["summary"]
    finally:
        overlay.close()


def test_command_center_memory_tab_smoke():
    overlay = _make_overlay()
    try:
        overlay.record_memory_tool_start(
            "jarvis_recall",
            {"query": "agentics aws lambda", "top_k": 2},
        )
        overlay.record_memory_tool_end(
            "jarvis_recall",
            42.0,
            True,
            {"found": 1, "results": [{"title": "Agentics AWS"}]},
        )

        overlay.open_dashboard()
        overlay._select_dashboard_tab("memory")
        overlay._refresh_dashboard_once()

        content = overlay.command_center.text_widgets["memory"].get("1.0", "end-1c")
        assert "recall encontro 1" in content
    finally:
        overlay.close()


def test_voice_reactive_core_accepts_audio():
    overlay = _make_overlay()
    try:
        overlay.set_state("speaking")
        overlay.feed_voice_audio((12000).to_bytes(2, "little", signed=True) * 256)
        overlay.root.update_idletasks()

        assert overlay.core_visual is not None
    finally:
        overlay.close()


def test_compact_mode_resizes_core_and_hides_transcripts():
    overlay = _make_overlay()
    try:
        overlay.toggle_compact()
        overlay.root.update_idletasks()

        assert overlay.core_visual._compact is True
        assert not overlay.body.winfo_ismapped()
        assert not overlay.hint_label.winfo_ismapped()

        overlay.toggle_compact()
        overlay.root.update_idletasks()

        assert overlay.core_visual._compact is False
        assert overlay.body.winfo_ismapped()
        assert overlay.hint_label.winfo_ismapped()
    finally:
        overlay.close()


def test_close_cancels_pending_after_callbacks():
    overlay = _make_overlay()
    ran = []
    overlay.root.after(5000, lambda: ran.append("late"))

    overlay.close()

    assert ran == []


def test_approval_dialog_paints_content_and_resolves():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter display unavailable: {exc}")
    root.geometry("780x700+80+80")
    decisions = []
    events = []
    action = PendingAction(
        id="approval-test",
        risk="write",
        title="Jarvis quiere iniciar una grabacion OBS para memoria episodica",
        details="OBS Memory\nDetalles: {'title': 'Curso R'}",
        timeout_s=30,
    )
    dialog = ApprovalDialog(
        root,
        action,
        lambda action_id, approved: decisions.append((action_id, approved)),
        lambda message, level="info": events.append((message, level)),
    )
    try:
        dialog.show()
        root.update()

        assert dialog.window is not None
        assert dialog.window.winfo_ismapped()
        assert dialog.window.winfo_children()
        assert dialog.window.winfo_children()[0].cget("bg") != "white"

        dialog.decide(True)

        assert decisions == [("approval-test", True)]
        assert any("Aprobacion pendiente" in message for message, _ in events)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_approval_dialog_closes_even_if_log_event_fails():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter display unavailable: {exc}")
    root.geometry("780x700+80+80")
    decisions = []
    action = PendingAction(
        id="approval-close-test",
        risk="write",
        title="Jarvis quiere iniciar Study Mode",
        details="Study Mode",
        timeout_s=30,
    )

    def broken_log(_message, _level="info"):
        raise RuntimeError("overlay log unavailable")

    dialog = ApprovalDialog(
        root,
        action,
        lambda action_id, approved: decisions.append((action_id, approved)),
        broken_log,
    )
    try:
        dialog.show()
        root.update()
        window = dialog.window

        dialog.decide(False)
        root.update()

        assert decisions == [("approval-close-test", False)]
        assert dialog.window is None
        if window is not None:
            assert not bool(window.winfo_ismapped())
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_recursion_error_callback_closes_overlay():
    closed = []
    timed_out = []
    overlay = _make_overlay(on_close=lambda: closed.append(True))

    def boom():
        raise RecursionError("synthetic tkinter recursion")

    def timeout_close():
        timed_out.append(True)
        overlay.close()

    overlay.root.after(1, boom)
    overlay.root.after(1000, timeout_close)
    overlay.run()

    assert closed == [True]
    assert timed_out == []
