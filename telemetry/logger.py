"""
telemetry/logger.py - Logger estructurado de Jarvis (loguru).

Provee un unico punto de entrada `get_logger(name)` para que el resto del
codigo migre de `print(...)` a `log.info(...)`, `log.warning(...)`, etc.

Diseno:
- Sink de archivo en `data/jarvis.log` con rotacion (10 MB) y retencion (7 dias).
- Sink de stdout con color para INFO+ (configurable via JARVIS_LOG_LEVEL).
- Encoding UTF-8 explicito en archivo para evitar mojibake en Windows.
- Filtro automatico: el `redact_secrets` se aplica antes de loggear cualquier
  mensaje que provenga de tools o salida de comandos, para no filtrar API keys.
- Fallback defensivo: si el archivo de log esta bloqueado por otro proceso
  (ej. jarvis_run.bat redirigiendo stdout al mismo path en Windows), el sink
  de archivo se omite con un warning a stderr. Jarvis sigue funcionando con
  solo el sink de consola.

Usage:
    from telemetry.logger import get_logger
    log = get_logger(__name__)
    log.info("Sesion iniciando")
    log.warning("...", extra={"session_id": "abc"})
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger as _root_logger

_CONFIGURED = False


def _redact_filter(record) -> bool:
    """Sanitiza el mensaje antes de emitirlo si parece contener un secreto.

    No bloquea el log (devuelve True siempre) — solo modifica record["message"].
    Aprovecha security.secret_filter.redact_secrets si esta disponible.
    """
    try:
        from security.secret_filter import redact_log_text

        msg = record["message"]
        record["message"] = redact_log_text(msg, max_chars=2000)
    except Exception:
        pass
    return True


def configure_logger(
    log_dir: Path | None = None,
    level: str | None = None,
    rotation: str | None = None,
    retention: str | None = None,
) -> None:
    """Configura los sinks una sola vez. Idempotente."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = (level or os.environ.get("JARVIS_LOG_LEVEL", "INFO")).upper()
    rotation = rotation or os.environ.get("JARVIS_LOG_ROTATION", "10 MB")
    retention = retention or os.environ.get("JARVIS_LOG_RETENTION", "7 days")

    log_dir = log_dir or Path(__file__).resolve().parent.parent / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "jarvis.log"

    _root_logger.remove()  # quita el sink default de loguru

    # Sink de consola: nivel configurable, colorido, formato compacto.
    _root_logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan> "
            "<level>{message}</level>"
        ),
        filter=_redact_filter,
        backtrace=False,
        diagnose=False,  # diagnose=True puede filtrar valores de variables
    )

    # Sink de archivo: siempre DEBUG, rotacion + retencion, UTF-8 explicito.
    # Fallback: en Windows, si jarvis_run.bat (u otro proceso) tiene el path
    # abierto con un handle exclusivo, loguru lanza PermissionError. En ese
    # caso registramos el problema en stderr y seguimos sin sink de archivo.
    # Jarvis funciona con solo el sink de consola; el .bat captura ese stderr
    # vacía via su propio redirect.
    try:
        _root_logger.add(
            str(log_file),
            level="DEBUG",
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line} - {message}"
            ),
            filter=_redact_filter,
            backtrace=False,
            diagnose=False,
            enqueue=True,  # thread-safe writes, evita interleaving entre threads
        )
    except PermissionError as exc:
        # Caso clasico: jarvis_run.bat redirigiendo stdout al mismo archivo.
        # Loguru en consola sigue funcionando — captura el caso por stderr.
        sys.stderr.write(
            f"[logger] WARN: no pude abrir sink de archivo {log_file} ({exc}). "
            f"Loguru seguira solo en consola/stderr. Probable causa: otro proceso "
            f"(jarvis_run.bat con `>>`) tiene el archivo abierto. Considera quitar "
            f"el redirect del .bat — loguru ya rota y retiene logs.\n"
        )
    except OSError as exc:
        # Disco lleno, path invalido, etc.
        sys.stderr.write(
            f"[logger] WARN: sink de archivo deshabilitado por {type(exc).__name__}: {exc}\n"
        )

    _CONFIGURED = True


def get_logger(name: str | None = None):
    """Devuelve un logger bindeado al modulo `name` (auto-config si hace falta)."""
    if not _CONFIGURED:
        configure_logger()
    if name:
        return _root_logger.bind(name=name)
    return _root_logger


__all__ = ["configure_logger", "get_logger"]
