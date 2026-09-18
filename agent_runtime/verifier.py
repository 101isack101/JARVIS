"""Artifact persistence and evidence verification for read-only missions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .models import ArtifactMeta, Mission, MissionStep, StepStatus, canonical_json, new_id, utc_now
from .tools import MissionPolicy, _redact_value, result_summary


class VerificationError(RuntimeError):
    def __init__(self, message: str, code: str = "VERIFICATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


class ArtifactStore:
    def __init__(self, root: Path | str, *, max_chars: int = 20000) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_chars = max(1000, min(int(max_chars), 100000))

    def write_result(self, mission: Mission, step: MissionStep, result: dict[str, Any]) -> ArtifactMeta:
        artifact_id = new_id("art")
        mission_dir = (self.root / mission.id).resolve()
        if mission_dir.parent != self.root:
            raise VerificationError("mission_id produce path invalido", code="INVALID_ARTIFACT_PATH")
        mission_dir.mkdir(parents=True, exist_ok=True)
        target = mission_dir / f"{artifact_id}.json"
        clean_result = _redact_value(result, max_chars=self.max_chars)
        if len(canonical_json(clean_result)) > self.max_chars:
            serialized = canonical_json(clean_result)
            preview_limit = max(200, self.max_chars - 500)
            clean_result = {
                "ok": bool(result.get("ok")),
                "allowed": bool(result.get("allowed")),
                "operation": step.operation,
                "artifact_truncated": True,
                "summary": result_summary(step.operation, result),
                "preview": serialized[:preview_limit],
            }
        payload = {
            "mission_id": mission.id,
            "step_id": step.id,
            "step_ref": f"s{step.ordinal}",
            "operation": step.operation,
            "result": clean_result,
            "created_at": utc_now(),
        }
        encoded = (canonical_json(payload) + "\n").encode("utf-8")
        tmp = target.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(target)
        return ArtifactMeta(
            id=artifact_id,
            mission_id=mission.id,
            step_id=step.id,
            kind="tool_result",
            relative_path=str(target.relative_to(self.root)),
            sha256=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            redacted=True,
            created_at=payload["created_at"],
        )

    def read(self, artifact: ArtifactMeta) -> dict[str, Any]:
        path = self._resolve(artifact.relative_path)
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != artifact.sha256:
            raise VerificationError("hash de artefacto no coincide", code="ARTIFACT_CORRUPT")
        if len(encoded) != artifact.size_bytes:
            raise VerificationError("size de artefacto no coincide", code="ARTIFACT_CORRUPT")
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationError("artefacto no es JSON valido", code="ARTIFACT_CORRUPT") from exc
        if not isinstance(value, dict):
            raise VerificationError("artefacto no es object", code="ARTIFACT_CORRUPT")
        return value

    def validate(self, artifact: ArtifactMeta) -> bool:
        try:
            self.read(artifact)
            return True
        except (OSError, VerificationError):
            return False

    def _resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise VerificationError("artifact path fuera del root", code="INVALID_ARTIFACT_PATH") from exc
        return path


class MissionVerifier:
    def __init__(self, *, policy: MissionPolicy, artifacts: ArtifactStore) -> None:
        self.policy = policy
        self.artifacts = artifacts

    def verify_step(self, step: MissionStep, result: dict[str, Any]) -> dict[str, Any]:
        checks = ["policy_allowed"]
        if result.get("allowed") is not True or result.get("ok") is not True:
            raise VerificationError(str(result.get("error") or "tool no confirmo ok/allowed"))
        if result.get("operation") != step.operation:
            raise VerificationError("operation del resultado no coincide")
        checks.extend(["tool_ok", "operation_match"])

        if step.operation == "list_dir":
            items = result.get("items")
            if not isinstance(items, list):
                raise VerificationError("list_dir sin items")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise VerificationError("item de list_dir invalido")
                if not self.policy.evaluate("list_dir", {"path": item["path"]}).allowed:
                    raise VerificationError("list_dir devolvio path fuera de policy")
            checks.append("items_schema")
        elif step.operation == "read_file":
            if not isinstance(result.get("content"), str) or not isinstance(result.get("truncated"), bool):
                raise VerificationError("read_file sin content/truncated validos")
            if not isinstance(result.get("path"), str):
                raise VerificationError("read_file sin path valido")
            if not self.policy.evaluate("read_file", {"path": result["path"]}).allowed:
                raise VerificationError("read_file devolvio path fuera de policy")
            checks.append("content_schema")
        elif step.operation == "search_text":
            matches = result.get("matches")
            if not isinstance(matches, list):
                raise VerificationError("search_text sin matches")
            for match in matches:
                if (
                    not isinstance(match, dict)
                    or not isinstance(match.get("path"), str)
                    or not isinstance(match.get("line"), int)
                    or match["line"] < 1
                ):
                    raise VerificationError("match de search_text invalido")
                if not self.policy.evaluate("read_file", {"path": match["path"]}).allowed:
                    raise VerificationError("search_text devolvio path fuera de policy")
            checks.append("matches_schema")
        elif step.operation.startswith("git_"):
            if result.get("returncode") != 0:
                raise VerificationError("git result returncode != 0")
            checks.append("git_returncode")
        elif step.operation in {
            "obsidian_create_note",
            "obsidian_append_note",
            "obsidian_update_note",
        }:
            if result.get("verified") is not True:
                raise VerificationError("write sin verified")
            digest = result.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise VerificationError("write sin sha256")
            if not isinstance(result.get("path"), str) or not result["path"].strip():
                raise VerificationError("write sin path")
            checks.append("write_verified")
        elif step.operation == "obsidian_move_path":
            if result.get("verified") is not True:
                raise VerificationError("move sin verified")
            if not isinstance(result.get("from"), str) or not isinstance(result.get("to"), str):
                raise VerificationError("move sin from/to")
            checks.append("move_verified")
        elif step.operation == "file_organizer_apply":
            if result.get("verified") is not True:
                raise VerificationError("apply sin verified")
            if not isinstance(result.get("applied_count"), int) or result["applied_count"] < 1:
                raise VerificationError("apply sin applied_count")
            checks.append("apply_verified")
        else:
            raise VerificationError("operacion sin verifier", code="UNKNOWN_OPERATION")
        return {"status": "passed", "checks": checks, "summary": result_summary(step.operation, result)}

    def verify_mission(self, steps: list[MissionStep], artifacts_by_id: dict[str, ArtifactMeta]) -> None:
        if not steps:
            raise VerificationError("mision sin steps", code="INSUFFICIENT_EVIDENCE")
        for step in steps:
            if step.status != StepStatus.COMPLETED or step.verification_status != "passed":
                raise VerificationError(f"step s{step.ordinal} no verificado", code="INSUFFICIENT_EVIDENCE")
            if not step.artifact_id or step.artifact_id not in artifacts_by_id:
                raise VerificationError(f"step s{step.ordinal} sin artefacto", code="INSUFFICIENT_EVIDENCE")
            if not self.artifacts.validate(artifacts_by_id[step.artifact_id]):
                raise VerificationError(f"artefacto de s{step.ordinal} invalido", code="ARTIFACT_CORRUPT")


class ReportBuilder:
    REF_RE = re.compile(r"\[step:s(\d+)\]")

    def __init__(self, reasoner: Any, *, artifacts: ArtifactStore, max_output_tokens: int = 1800) -> None:
        self.reasoner = reasoner
        self.artifacts = artifacts
        self.max_output_tokens = max(256, min(int(max_output_tokens), 4096))

    def build(
        self,
        mission: Mission,
        steps: list[MissionStep],
        artifacts_by_id: dict[str, ArtifactMeta],
    ) -> str:
        evidence = []
        for step in steps:
            artifact = artifacts_by_id[step.artifact_id or ""]
            payload = self.artifacts.read(artifact)
            result = payload.get("result") or {}
            evidence.append({
                "step_ref": f"s{step.ordinal}",
                "purpose": step.purpose,
                "expected_evidence": step.expected_evidence,
                "summary": str(result.get("summary") or result_summary(step.operation, result)),
                "result": result,
            })
        if self.reasoner is None or not getattr(self.reasoner, "configured", True):
            text = self._template_report(mission, evidence)
            self.validate_report(text, steps)
            return text
        prompt = (
            "Redacta el informe final de una misión read-only de JARVIS. Usa únicamente "
            "EVIDENCE_DATA. Toda afirmación factual debe incluir una referencia [step:sN]. "
            "Responde en español, conciso, con hallazgos, riesgos y siguientes pasos.\n"
            f"GOAL_DATA={canonical_json(mission.goal)}\n"
            f"EVIDENCE_DATA={canonical_json(evidence)[:50000]}"
        )
        response = self.reasoner.ask(prompt, max_output_tokens=self.max_output_tokens)
        text = str(getattr(response, "text", response) or "").strip()
        self.validate_report(text, steps)
        return text

    @staticmethod
    def _template_report(mission: Mission, evidence: list[dict[str, Any]]) -> str:
        lines = [f"Informe plantilla L1: {mission.goal}"]
        if not evidence:
            return "Informe plantilla L1 sin pasos. [step:s1] evidencia insuficiente."
        for item in evidence:
            ref = str(item.get("step_ref") or "s1")
            ordinal = ref[1:] if ref.startswith("s") and ref[1:].isdigit() else "1"
            summary = str(item.get("summary") or item.get("purpose") or "evidencia")
            lines.append(f"- [step:s{ordinal}] {summary}")
        lines.append("Siguiente paso: revisar los artefactos citados.")
        return "\n".join(lines)

    def validate_report(self, text: str, steps: list[MissionStep]) -> None:
        refs = {int(value) for value in self.REF_RE.findall(text or "")}
        valid = {step.ordinal for step in steps}
        if not text or not refs or not refs.issubset(valid):
            raise VerificationError(
                "informe sin referencias validas a evidencia",
                code="UNSUPPORTED_REPORT",
            )
