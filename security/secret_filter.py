"""Redaccion y filtrado de secretos antes de indexar o enviar contexto."""

from __future__ import annotations

import re
from pathlib import Path

from .policy import is_secret_path


SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(ANTHROPIC_API_KEY|GEMINI_API_KEY|OPENAI_API_KEY|API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*['\"]?([^\s'\"#]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(CLIENT_SECRET|SPOTIFY_CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN|ID_TOKEN)\s*=\s*['\"]?([^\s'\"#]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(client_secret|access_token|refresh_token|id_token|authorization_code|code)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{12,})"), r"\1=[REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"(?i)aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{30,}"), "aws_secret_access_key=[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1[REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
]


def should_skip_path(path: Path | str) -> bool:
    return is_secret_path(path)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_log_text(text: object, max_chars: int = 700) -> str:
    """Sanitiza texto antes de loggear payloads potencialmente grandes.

    Los logs deben servir para depurar sin convertirse en un segundo transcript
    completo de pantalla, paginas web o notas privadas. Primero redacta secretos;
    luego acorta valores largos conservando suficiente contexto para diagnostico.
    """
    raw = str(text)
    redacted = redact_secrets(raw).replace("\r", "\\r").replace("\n", "\\n")
    if len(redacted) <= max_chars:
        return redacted
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(redacted) - max_chars
    return (
        redacted[:head]
        + f"... [LOG_REDACTED {omitted} chars omitted] ..."
        + redacted[-tail:]
    )
