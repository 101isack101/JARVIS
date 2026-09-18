"""Closed read-only capability catalog and policy adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from security.policy import is_inside_root, is_secret_path
from security.secret_filter import redact_secrets

from .models import MissionStep, PolicyDecision


@dataclass(frozen=True)
class OperationSpec:
    allowed_keys: frozenset[str]
    required_keys: frozenset[str]
    path_keys: frozenset[str] = frozenset()


READ_ONLY_OPERATIONS: dict[str, OperationSpec] = {
    "list_dir": OperationSpec(frozenset({"path", "limit"}), frozenset(), frozenset({"path"})),
    "read_file": OperationSpec(frozenset({"path", "max_chars"}), frozenset({"path"}), frozenset({"path"})),
    "search_text": OperationSpec(
        frozenset({"path", "query", "limit", "max_chars"}),
        frozenset({"query"}),
        frozenset({"path"}),
    ),
    "git_status": OperationSpec(frozenset(), frozenset()),
    "git_diff_stat": OperationSpec(frozenset(), frozenset()),
    "git_log": OperationSpec(frozenset({"limit"}), frozenset()),
}

WRITE_OPERATIONS: dict[str, OperationSpec] = {
    "obsidian_create_note": OperationSpec(
        frozenset({"path", "content"}), frozenset({"path", "content"}), frozenset({"path"})
    ),
    "obsidian_append_note": OperationSpec(
        frozenset({"path", "content"}), frozenset({"path", "content"}), frozenset({"path"})
    ),
    "obsidian_update_note": OperationSpec(
        frozenset({"path", "content"}), frozenset({"path", "content"}), frozenset({"path"})
    ),
    "obsidian_move_path": OperationSpec(
        frozenset({"path", "destination"}),
        frozenset({"path", "destination"}),
        frozenset({"path", "destination"}),
    ),
    "file_organizer_apply": OperationSpec(frozenset({"plan_id"}), frozenset({"plan_id"})),
}


class MissionPolicy:
    def __init__(self, root: Path | str, autonomy_level: str = "L1") -> None:
        self.root = Path(root).resolve()
        self.autonomy_level = "L2" if str(autonomy_level or "").strip().upper() == "L2" else "L1"

    def evaluate(self, operation: str, arguments: dict[str, Any] | None) -> PolicyDecision:
        name = (operation or "").strip()
        spec = READ_ONLY_OPERATIONS.get(name) or WRITE_OPERATIONS.get(name)
        if spec is None:
            return PolicyDecision(False, "blocked", "UNKNOWN_OPERATION", detail=name)
        is_write = name in WRITE_OPERATIONS
        if is_write and self.autonomy_level != "L2":
            return PolicyDecision(False, "blocked", "AUTONOMY_DENIED", detail=name)
        if not isinstance(arguments, dict):
            return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="arguments no es object")
        extra = set(arguments) - set(spec.allowed_keys)
        missing = set(spec.required_keys) - set(arguments)
        if extra or missing:
            return PolicyDecision(
                False,
                "blocked",
                "INVALID_ARGUMENTS",
                detail=f"extra={sorted(extra)} missing={sorted(missing)}",
            )

        normalized = dict(arguments)
        for key in spec.path_keys:
            if key not in normalized or normalized[key] in (None, ""):
                if key == "path" and not is_write:
                    normalized[key] = "."
                    continue
                return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail=f"{key} vacio")
            if not isinstance(normalized[key], str):
                return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail=f"{key} no es string")
            if is_write:
                decision = self._normalize_vault_rel(normalized[key])
                if not decision.allowed:
                    return decision
                normalized[key] = decision.normalized_arguments["path"]
            else:
                decision = self._normalize_path(normalized[key])
                if not decision.allowed:
                    return decision
                normalized[key] = decision.normalized_arguments[key]

        if "query" in normalized:
            query = normalized["query"]
            if not isinstance(query, str) or not query.strip() or len(query) > 500:
                return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="query invalido")
            normalized["query"] = query.strip()
        if "content" in normalized:
            content = normalized["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 20000:
                return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="content invalido")
            normalized["content"] = content
        if "plan_id" in normalized:
            plan_id = normalized["plan_id"]
            if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 80:
                return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="plan_id invalido")
            normalized["plan_id"] = plan_id.strip()
        for key, low, high in (("limit", 1, 500), ("max_chars", 200, 20000)):
            if key in normalized:
                value = normalized[key]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail=f"{key} fuera de rango")

        if is_write:
            return PolicyDecision(True, "write", "ALLOW_L2_WRITE", normalized_arguments=normalized)
        return PolicyDecision(True, "read", "ALLOW_READ_ONLY", normalized_arguments=normalized)

    def evaluate_step(self, step: MissionStep) -> PolicyDecision:
        return self.evaluate(step.operation, step.arguments)

    def _normalize_path(self, raw: str) -> PolicyDecision:
        candidate = Path(raw.strip() or ".")
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail=str(exc))
        if not is_inside_root(resolved, self.root):
            return PolicyDecision(False, "blocked", "OUTSIDE_ROOT", detail=str(resolved))
        if is_secret_path(resolved):
            return PolicyDecision(False, "blocked", "SECRET_PATH", detail=str(resolved))
        relative = "." if resolved == self.root else str(resolved.relative_to(self.root))
        return PolicyDecision(True, "read", "ALLOW_READ_ONLY", normalized_arguments={"path": relative})

    def _normalize_vault_rel(self, raw: str) -> PolicyDecision:
        text = raw.strip().replace("\\", "/").strip("/")
        if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
            return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="path absoluto")
        parts = Path(text).parts
        if ".." in parts:
            return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail="path escape")
        if any(part in {".obsidian", ".trash", ".git"} or part.startswith(".") for part in parts):
            return PolicyDecision(False, "blocked", "INVALID_ARGUMENTS", detail=text)
        if is_secret_path(text):
            return PolicyDecision(False, "blocked", "SECRET_PATH", detail=text)
        return PolicyDecision(True, "write", "ALLOW_L2_WRITE", normalized_arguments={"path": text})


class ReadOnlyToolAdapter:
    def __init__(
        self,
        executor: Any,
        *,
        policy: MissionPolicy,
        max_result_chars: int = 20000,
        approval_broker: Any | None = None,
        writers: dict[str, Any] | None = None,
    ) -> None:
        self.executor = executor
        self.policy = policy
        self.max_result_chars = max(1000, min(int(max_result_chars), 100000))
        self.approval_broker = approval_broker
        self.writers = dict(writers or {})

    def execute(self, step: MissionStep) -> dict[str, Any]:
        decision = self.policy.evaluate_step(step)
        if not decision.allowed:
            return {
                "ok": False,
                "allowed": False,
                "operation": step.operation,
                "error_code": decision.reason_code,
                "error": decision.detail or "bloqueado por policy",
            }
        if decision.risk == "write":
            if self.approval_broker is None or not bool(
                self.approval_broker.request(
                    risk="write",
                    title=f"JARVIS L2 quiere {step.operation}",
                    details=f"{step.operation} {decision.normalized_arguments}",
                )
            ):
                return {
                    "ok": False,
                    "allowed": False,
                    "operation": step.operation,
                    "error_code": "HITL_DENIED",
                    "error": "escritura L2 rechazada o sin aprobacion HITL",
                }
            writer = self.writers.get(step.operation)
            if writer is None:
                return {
                    "ok": False,
                    "allowed": False,
                    "operation": step.operation,
                    "error_code": "WRITER_UNAVAILABLE",
                    "error": f"sin writer para {step.operation}",
                }
            result = writer(**decision.normalized_arguments)
        else:
            result = self.executor.run_structured(step.operation, **decision.normalized_arguments)
        if not isinstance(result, dict):
            return {
                "ok": False,
                "allowed": False,
                "operation": step.operation,
                "error_code": "INVALID_TOOL_RESULT",
                "error": "executor devolvio un tipo invalido",
            }
        clean = _redact_value(result, max_chars=self.max_result_chars)
        if clean.get("operation") not in (None, step.operation):
            return {
                "ok": False,
                "allowed": False,
                "operation": step.operation,
                "error_code": "INVALID_TOOL_RESULT",
                "error": "operation del resultado no coincide",
            }
        if decision.risk == "write":
            clean["allowed"] = bool(clean.get("ok"))
        elif clean.get("allowed") is not True:
            clean.setdefault("error_code", "POLICY_REJECTED")
            clean["ok"] = False
        clean["operation"] = step.operation
        return clean


def result_summary(operation: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"{operation} fallo: {str(result.get('error') or result.get('error_code') or 'sin detalle')[:300]}"
    if operation == "list_dir":
        return f"{len(result.get('items') or [])} elementos listados"
    if operation == "read_file":
        suffix = " (truncado)" if result.get("truncated") else ""
        return f"archivo leído{suffix}: {result.get('path', '')}"
    if operation == "search_text":
        return f"{len(result.get('matches') or [])} coincidencias"
    if operation.startswith("git_"):
        text = str(result.get("stdout") or "").strip()
        lines = len(text.splitlines()) if text else 0
        return f"{operation} completado: {lines} líneas"
    if operation.startswith("obsidian_"):
        return f"{operation} verificado: {result.get('path') or result.get('to') or ''}"
    if operation == "file_organizer_apply":
        return f"organizer apply: {result.get('applied_count', 0)} movimientos"
    return f"{operation} completado"


def _redact_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        redacted = redact_secrets(value)
        if len(redacted) > max_chars:
            return redacted[: max_chars // 2] + "\n[TRUNCATED]\n" + redacted[-max_chars // 2 :]
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, max_chars=max_chars) for item in value[:500]]
    if isinstance(value, dict):
        return {str(key): _redact_value(item, max_chars=max_chars) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_secrets(str(value))[:max_chars]
