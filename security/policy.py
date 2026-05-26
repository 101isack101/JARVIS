"""Politica central de seguridad para Jarvis.

La idea es que los prompts nunca sean la unica barrera. Rutas, secretos y
acciones de riesgo se validan aqui antes de tocar disco o enviar contexto a LLMs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SecurityError(Exception):
    """Operacion bloqueada por politica de seguridad."""


SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "secrets.json",
    "secret.json",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "id_rsa",
    "id_ed25519",
}

SECRET_NAME_PARTS = {
    ".env",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "token",
    "apikey",
    "api_key",
}

SECRET_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".der",
    ".kdbx",
}

SECRET_DIR_PARTS = {
    ".aws",
    ".azure",
    ".config/gcloud",
    ".gnupg",
    ".ssh",
}

BLOCKED_INTERNAL_PARTS = {".git", ".obsidian", ".trash"}


@dataclass(frozen=True)
class PathDecision:
    allowed: bool
    reason: str = ""
    resolved: Path | None = None


def is_inside_root(path: Path | str, root: Path | str) -> bool:
    """True si path resuelto queda dentro de root resuelto."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def assert_inside_root(path: Path | str, root: Path | str, label: str = "path") -> Path:
    """Devuelve path resuelto o lanza SecurityError si intenta escapar."""
    resolved = Path(path).resolve()
    if not is_inside_root(resolved, root):
        raise SecurityError(f"{label} fuera del root permitido: {resolved}")
    return resolved


def assert_inside_any_root(
    path: Path | str,
    roots: list[Path | str] | tuple[Path | str, ...],
    label: str = "path",
) -> Path:
    resolved = Path(path).resolve()
    if not any(is_inside_root(resolved, root) for root in roots):
        allowed = ", ".join(str(Path(r).resolve()) for r in roots)
        raise SecurityError(f"{label} fuera de roots permitidos: {resolved}; allowed={allowed}")
    return resolved


def _normalized_parts(path: Path) -> list[str]:
    return [part.lower() for part in path.parts]


def is_secret_path(path: Path | str) -> bool:
    """Detecta archivos/carpetas que no deben indexarse ni enviarse a modelos."""
    p = Path(path)
    name = p.name.lower()
    if name in SECRET_FILENAMES:
        return True
    if any(part in name for part in SECRET_NAME_PARTS):
        return True
    if p.suffix.lower() in SECRET_SUFFIXES:
        return True
    parts = _normalized_parts(p)
    joined = "/".join(parts)
    if any(part in BLOCKED_INTERNAL_PARTS for part in parts):
        return True
    return any(secret_part in joined for secret_part in SECRET_DIR_PARTS)


def path_decision(path: Path | str, root: Path | str) -> PathDecision:
    try:
        resolved = assert_inside_root(path, root)
    except SecurityError as exc:
        return PathDecision(False, str(exc), None)
    if is_secret_path(resolved):
        return PathDecision(False, f"path sensible bloqueado: {resolved}", resolved)
    return PathDecision(True, resolved=resolved)
