import os
import time

from runtime_preferences import ensure_runtime_preferences, preferences_prompt_block
from security.secret_filter import redact_log_text
from vision.screen import ScreenCapture


def test_redact_log_text_masks_and_truncates_sensitive_payloads():
    text = "ANTHROPIC_API_KEY=sk-ant-1234567890abcdefghijklmnopqrstuvwxyz\n" + ("x" * 1000)

    redacted = redact_log_text(text, max_chars=120)

    assert "sk-ant-" not in redacted
    assert "[REDACTED" in redacted
    assert "LOG_REDACTED" in redacted


def test_screen_capture_cleanup_old_removes_expired_png(tmp_path):
    old = tmp_path / "screen-old.png"
    old.write_bytes(b"old")
    old_time = time.time() - 48 * 3600
    os.utime(old, (old_time, old_time))

    capture = ScreenCapture(tmp_path, retention_hours=24)

    assert capture.cleanup_old() == 0
    assert not old.exists()


def test_runtime_preferences_persist_granular_notes_rule(tmp_path):
    path = tmp_path / "preferences.json"

    prefs = ensure_runtime_preferences(path)
    prompt = preferences_prompt_block(prefs)

    assert path.exists()
    assert prefs["obsidian_notes"]["granular_by_default"] is True
    assert "nota separada por tema" in prompt
    assert prefs["voice_experience"]["shorter_answers_by_default"] is False
