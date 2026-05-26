"""
actions/executor.py - Executor seguro para acciones locales.

Primera version deliberadamente conservadora:
  - JARVIS_MODE=dev: dry-run, no ejecuta.
  - JARVIS_MODE=prod: solo comandos PowerShell read-only con allowlist.
  - Timeouts cortos y salida truncada para que una tool no congele la voz.
"""

from __future__ import annotations

import os
import subprocess
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from security.policy import SecurityError, assert_inside_root


READ_ONLY_PREFIXES = (
    "pwd",
    "get-location",
    "dir",
    "ls",
    "get-childitem",
    "get-content",
    "select-string",
    "rg ",
    "git status",
    "git diff --stat",
    "git log ",
)

HARD_BLOCKED_TOKENS = (
    ">",
    ">>",
    "|",
    "&&",
    "||",
    ";",
)

INPUT_AUTOMATION_TOKENS = (
    "wscript.shell",
    "sendkeys",
    "new-object -comobject",
    "-comobject",
    "shell.application",
    "pyautogui",
    "keyboard.",
    "setcursorpos",
    "mouse_event",
    "keybd_event",
    "user32.dll",
    "add-type",
)

RISKY_TOKENS = (
    "remove-item",
    "rm ",
    "del ",
    "erase ",
    "move-item",
    "mv ",
    "copy-item",
    "cp ",
    "set-content",
    "add-content",
    "out-file",
    "git push",
    "git commit",
    "git tag",
    "pip install",
    "npm install",
    "npm run",
)


@dataclass
class ActionResult:
    executed: bool
    allowed: bool
    mode: str
    command: str
    cwd: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "executed": self.executed,
            "allowed": self.allowed,
            "mode": self.mode,
            "command": self.command,
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


@dataclass
class SafeActionExecutor:
    root: Path
    mode: str = field(default_factory=lambda: os.environ.get("JARVIS_MODE", "dev"))
    timeout_s: float = 8.0
    max_output_chars: int = 4000
    approval_broker: object | None = None

    def _inside_root(self, cwd: Path) -> bool:
        try:
            assert_inside_root(cwd, self.root, label="cwd")
            return True
        except SecurityError:
            return False

    def _normalize_command(self, command: str) -> str:
        return " ".join(command.strip().lower().split())

    def _is_allowed_readonly(self, command: str) -> bool:
        normalized = self._normalize_command(command)
        if not normalized:
            return False
        if any(tok in normalized for tok in HARD_BLOCKED_TOKENS):
            return False
        if any(tok in normalized for tok in INPUT_AUTOMATION_TOKENS):
            return False
        if any(tok in normalized for tok in RISKY_TOKENS):
            return False
        return any(normalized == p.strip() or normalized.startswith(p) for p in READ_ONLY_PREFIXES)

    def _hard_block_reason(self, command: str) -> str | None:
        normalized = self._normalize_command(command)
        if any(tok in normalized for tok in HARD_BLOCKED_TOKENS):
            return "composicion de shell no permitida"
        if any(tok in normalized for tok in INPUT_AUTOMATION_TOKENS):
            return "automatizacion de teclado/mouse/COM bloqueada"
        return None

    def _risk_for(self, command: str) -> str:
        normalized = self._normalize_command(command)
        if any(tok in normalized for tok in ("remove-item", "rm ", "del ", "erase ")):
            return "destructive"
        if "git push" in normalized:
            return "git_publish"
        if any(tok in normalized for tok in ("pip install", "npm install", "npm run")):
            return "install_or_run"
        if any(tok in normalized for tok in RISKY_TOKENS):
            return "write"
        return "command"

    def _request_approval(self, risk: str, command: str, cwd: Path) -> bool:
        if self.approval_broker is None:
            return False
        return bool(self.approval_broker.request(
            risk=risk,
            title=f"Jarvis quiere ejecutar un comando ({risk})",
            details=f"Comando: {command}\nDirectorio: {cwd}",
        ))

    def _clip_output(self, text: str, label: str) -> str:
        """Trunca output largo conservando head+tail con marcador explicito.

        Antes truncaba silenciosamente a max_output_chars desde el final, lo
        que escondia datos relevantes al modelo. Ahora si excede, devuelve
        head+aviso+tail para que el LLM sepa que hubo truncado y por que.
        """
        if not text:
            return ""
        if len(text) <= self.max_output_chars:
            return text
        half = max(self.max_output_chars // 2 - 80, 200)
        head = text[:half]
        tail = text[-half:]
        omitted = len(text) - 2 * half
        marker = (
            f"\n\n[... {label} truncado: {omitted} chars omitidos "
            f"({len(text)} total > {self.max_output_chars} limite). "
            f"Sugiere a Isaac filtrar el comando (Select-Object -First / "
            f"Select-String / out-file) si necesita ver todo. ...]\n\n"
        )
        return head + marker + tail

    def run_powershell(self, command: str, cwd: str | None = None) -> dict:
        workdir = (Path(cwd) if cwd else self.root).resolve()
        if not self._inside_root(workdir):
            return ActionResult(
                executed=False,
                allowed=False,
                mode=self.mode,
                command=command,
                cwd=str(workdir),
                error=f"cwd fuera del proyecto permitido: {workdir}",
            ).as_dict()

        normalized = self._normalize_command(command)
        hard_block = self._hard_block_reason(command)
        if hard_block:
            return ActionResult(
                executed=False,
                allowed=False,
                mode=self.mode,
                command=command,
                cwd=str(workdir),
                error=f"comando bloqueado: {hard_block}",
            ).as_dict()

        readonly = self._is_allowed_readonly(command)
        if not readonly:
            risk = self._risk_for(command)
            if not self._request_approval(risk, command, workdir):
                return ActionResult(
                    executed=False,
                    allowed=False,
                    mode=self.mode,
                    command=command,
                    cwd=str(workdir),
                    error=f"comando {risk} rechazado o sin aprobacion HITL",
                ).as_dict()

        if self.mode != "prod":
            return ActionResult(
                executed=False,
                allowed=True,
                mode=self.mode,
                command=command,
                cwd=str(workdir),
                stdout="dry-run: JARVIS_MODE no es prod",
            ).as_dict()

        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
            return ActionResult(
                executed=True,
                allowed=True,
                mode=self.mode,
                command=command,
                cwd=str(workdir),
                returncode=proc.returncode,
                stdout=self._clip_output(proc.stdout, "stdout"),
                stderr=self._clip_output(proc.stderr, "stderr"),
            ).as_dict()
        except Exception as exc:
            return ActionResult(
                executed=True,
                allowed=True,
                mode=self.mode,
                command=command,
                cwd=str(workdir),
                error=f"{type(exc).__name__}: {exc}",
            ).as_dict()

    def open_url(self, url: str | None = None) -> dict:
        """Abre el navegador por defecto en una URL segura.

        Esto se permite incluso en JARVIS_MODE=dev porque es una accion
        reversible/no destructiva y desbloquea el flujo conversacional.
        """
        target = (url or "about:blank").strip()
        if not target:
            target = "about:blank"
        if "://" not in target and target != "about:blank":
            target = "https://" + target

        parsed = urlparse(target)
        allowed = (
            target == "about:blank"
            or parsed.scheme in ("http", "https")
        )
        if not allowed:
            return {
                "executed": False,
                "allowed": False,
                "mode": self.mode,
                "url": target,
                "error": "solo se permiten URLs http(s) o about:blank",
            }

        try:
            ok = webbrowser.open(target, new=2)
            return {
                "executed": bool(ok),
                "allowed": True,
                "mode": self.mode,
                "url": target,
                "message": "navegador solicitado al sistema",
            }
        except Exception as exc:
            return {
                "executed": False,
                "allowed": True,
                "mode": self.mode,
                "url": target,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def open_powershell(self, cwd: str | None = None) -> dict:
        """Abre una ventana de PowerShell en un directorio validado.

        Esta accion reemplaza intentos inseguros con SendKeys/COM automation.
        Requiere aprobacion HITL y respeta JARVIS_MODE: en dev solo reporta dry-run.
        """
        workdir = (Path(cwd) if cwd else self.root).resolve()
        if not self._inside_root(workdir):
            return {
                "executed": False,
                "allowed": False,
                "mode": self.mode,
                "cwd": str(workdir),
                "error": f"cwd fuera del proyecto permitido: {workdir}",
            }
        if not self._request_approval("open_terminal", "Open PowerShell", workdir):
            return {
                "executed": False,
                "allowed": False,
                "mode": self.mode,
                "cwd": str(workdir),
                "error": "abrir PowerShell rechazado o sin aprobacion HITL",
            }
        if self.mode != "prod":
            return {
                "executed": False,
                "allowed": True,
                "mode": self.mode,
                "cwd": str(workdir),
                "stdout": "dry-run: JARVIS_MODE no es prod",
            }
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", "Set-Location -LiteralPath $args[0]", str(workdir)],
                cwd=str(workdir),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )
            return {
                "executed": True,
                "allowed": True,
                "mode": self.mode,
                "cwd": str(workdir),
                "message": "PowerShell solicitado al sistema",
            }
        except Exception as exc:
            return {
                "executed": False,
                "allowed": True,
                "mode": self.mode,
                "cwd": str(workdir),
                "error": f"{type(exc).__name__}: {exc}",
            }
