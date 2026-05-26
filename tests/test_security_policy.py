from pathlib import Path

from security.policy import SecurityError, assert_inside_root, is_secret_path
from security.secret_filter import redact_secrets


def test_assert_inside_root_blocks_parent_escape(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")

    try:
        assert_inside_root(allowed / ".." / "outside.txt", allowed)
    except SecurityError:
        pass
    else:
        raise AssertionError("parent escape should be blocked")


def test_secret_path_detection_blocks_env_and_keys(tmp_path):
    assert is_secret_path(tmp_path / ".env")
    assert is_secret_path(tmp_path / ".env .txt")
    assert is_secret_path(tmp_path / "spotify_token_cache.json")
    assert is_secret_path(tmp_path / "private.pem")
    assert is_secret_path(Path(".ssh") / "id_rsa")


def test_redact_secrets_masks_api_keys():
    text = "ANTHROPIC_API_KEY=sk-ant-1234567890abcdefghijklmnopqrstuvwxyz"
    redacted = redact_secrets(text)

    assert "sk-ant-" not in redacted
    assert "[REDACTED" in redacted


def test_redact_secrets_masks_oauth_tokens():
    text = (
        "SPOTIFY_CLIENT_SECRET=supersecretvalue12345\n"
        "access_token=abc12345678901234567890\n"
        "refresh_token: xyz12345678901234567890\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
    )
    redacted = redact_secrets(text)

    assert "supersecretvalue12345" not in redacted
    assert "abc12345678901234567890" not in redacted
    assert "xyz12345678901234567890" not in redacted
    assert "[REDACTED]" in redacted
