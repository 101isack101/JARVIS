"""Safe local file organizer for Jarvis.

This module intentionally avoids shell commands. Jarvis can inspect allowed
folders, create a persisted move plan, and apply that plan only after HITL
approval. It never deletes files and it does not overwrite destinations.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security.policy import SecurityError, assert_inside_any_root, is_secret_path


CATEGORY_BY_SUFFIX = {
    ".png": "Images",
    ".jpg": "Images",
    ".jpeg": "Images",
    ".webp": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".mp4": "Videos",
    ".mov": "Videos",
    ".mkv": "Videos",
    ".avi": "Videos",
    ".webm": "Videos",
    ".mp3": "Audio",
    ".wav": "Audio",
    ".m4a": "Audio",
    ".flac": "Audio",
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".xls": "Documents",
    ".xlsx": "Documents",
    ".ppt": "Documents",
    ".pptx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".csv": "Data",
    ".json": "Data",
    ".xml": "Data",
    ".yaml": "Data",
    ".yml": "Data",
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".tsx": "Code",
    ".html": "Code",
    ".css": "Code",
    ".ps1": "Code",
    ".bat": "Code",
    ".exe": "Installers",
    ".msi": "Installers",
    ".lnk": "Shortcuts",
    ".url": "Shortcuts",
    ".appref-ms": "Shortcuts",
}

IGNORED_DIR_NAMES = {
    "$recycle.bin",
    ".cache",
    ".codex",
    ".git",
    ".hg",
    ".idea",
    ".obsidian",
    ".pytest_cache",
    ".svn",
    ".trash",
    ".venv",
    "_jarvis_organized",
    "_jarvis_organized_preview",
    "__pycache__",
    "appdata",
    "node_modules",
    "program files",
    "program files (x86)",
    "programdata",
    "windows",
}

CRITICAL_ROOT_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _split_env_paths(value: str) -> list[Path]:
    return [Path(part.strip()) for part in value.split(os.pathsep) if part.strip()]


def default_allowed_roots() -> list[Path]:
    configured = os.environ.get("JARVIS_ORGANIZER_ROOTS", "").strip()
    if configured:
        roots = _split_env_paths(configured)
    else:
        home = Path.home()
        names = ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music")
        roots = [home / name for name in names]
        workspace = os.environ.get("JARVIS_WORKSPACE_ROOT", "").strip()
        if workspace:
            roots.append(Path(workspace))

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved).lower()
        if key not in seen and _is_safe_allowed_root(resolved):
            unique.append(resolved)
            seen.add(key)
    return unique


def _is_drive_root(path: Path) -> bool:
    return str(path).rstrip("\\/").lower() == path.anchor.rstrip("\\/").lower()


def _is_safe_allowed_root(path: Path) -> bool:
    if _is_drive_root(path):
        return False
    parts = {part.lower() for part in path.parts}
    return not bool(parts & CRITICAL_ROOT_NAMES)


def _is_hidden_or_internal(path: Path) -> bool:
    return any(part.lower() in IGNORED_DIR_NAMES or part.startswith(".") for part in path.parts)


def _category_for(path: Path) -> str:
    if path.is_dir():
        return "Folders"
    return CATEGORY_BY_SUFFIX.get(path.suffix.lower(), "Other")


def _item_type(path: Path) -> str:
    return "dir" if path.is_dir() else "file"


def _dir_contains_blocked_path(path: Path) -> bool:
    try:
        for child in path.rglob("*"):
            rel = child.relative_to(path)
            if _is_hidden_or_internal(rel) or is_secret_path(child):
                return True
    except OSError:
        return True
    return False


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise SecurityError(f"no se pudo generar destino unico para {path}")


@dataclass
class FileOrganizer:
    allowed_roots: list[Path] = field(default_factory=default_allowed_roots)
    state_dir: Path = field(default_factory=lambda: Path("data") / "file_organizer")
    mode: str = field(default_factory=lambda: os.environ.get("JARVIS_ORGANIZER_MODE") or os.environ.get("JARVIS_MODE", "dev"))
    approval_broker: object | None = None
    max_plan_items: int = 500
    strict_root_validation: bool = True

    def __post_init__(self) -> None:
        resolved = []
        for root in self.allowed_roots:
            candidate = Path(root).expanduser().resolve()
            if self.strict_root_validation and not _is_safe_allowed_root(candidate):
                continue
            resolved.append(candidate)
        self.allowed_roots = resolved
        self.state_dir = Path(self.state_dir).resolve()
        self.plans_dir.mkdir(parents=True, exist_ok=True)

    @property
    def plans_dir(self) -> Path:
        return self.state_dir / "plans"

    def status(self) -> dict:
        return {
            "ok": True,
            "mode": self.mode,
            "allowed_roots": [str(root) for root in self.allowed_roots],
            "plans_dir": str(self.plans_dir),
            "hitl_required_for_apply": True,
            "delete_supported": False,
            "overwrite_supported": False,
            "cross_volume_moves": "blocked; copy manually if needed",
            "real_moves_enabled": self.mode == "prod",
            "mode_env": "JARVIS_ORGANIZER_MODE overrides JARVIS_MODE for this tool",
        }

    def _resolve_allowed(self, path: str | Path | None, *, label: str) -> Path:
        if not self.allowed_roots:
            raise SecurityError("no hay roots permitidos para organizar archivos")
        raw = Path(path).expanduser() if path else self.allowed_roots[0]
        resolved = raw.resolve()
        assert_inside_any_root(resolved, self.allowed_roots, label=label)
        if is_secret_path(resolved):
            raise SecurityError(f"{label} sensible bloqueado: {resolved}")
        return resolved

    def _iter_items(
        self,
        root: Path,
        *,
        recursive: bool,
        limit: int,
        include_folders: bool = False,
    ) -> tuple[list[Path], list[str]]:
        warnings: list[str] = []
        items: list[Path] = []
        if recursive and include_folders:
            recursive = False
            warnings.append("include_folders usa solo elementos top-level para evitar mover padres e hijos a la vez")
        iterator = root.rglob("*") if recursive else root.iterdir()
        for candidate in iterator:
            if len(items) >= limit:
                warnings.append(f"scan limitado a {limit} elementos")
                break
            try:
                if _is_hidden_or_internal(candidate.relative_to(root)):
                    continue
                if is_secret_path(candidate):
                    continue
                if candidate.is_dir():
                    if not include_folders:
                        continue
                    if _dir_contains_blocked_path(candidate):
                        warnings.append(f"carpeta omitida por contener paths sensibles/internos: {candidate}")
                        continue
                    items.append(candidate.resolve())
                    continue
                if candidate.is_file():
                    items.append(candidate.resolve())
            except (OSError, ValueError) as exc:
                warnings.append(f"omitido {candidate}: {type(exc).__name__}")
        return items, warnings

    def scan(
        self,
        root: str | None = None,
        *,
        recursive: bool = False,
        limit: int = 100,
        include_folders: bool = False,
    ) -> dict:
        try:
            target = self._resolve_allowed(root, label="root")
            if not target.exists():
                return {"ok": False, "allowed": True, "error": f"no existe: {target}"}
            if not target.is_dir():
                return {"ok": False, "allowed": True, "error": f"no es carpeta: {target}"}
            items, warnings = self._iter_items(
                target,
                recursive=recursive,
                limit=max(1, min(limit, 500)),
                include_folders=include_folders,
            )
            return {
                "ok": True,
                "root": str(target),
                "recursive": recursive,
                "include_folders": include_folders,
                "count": len(items),
                "warnings": warnings,
                "items": [
                    {
                        "path": str(path),
                        "name": path.name,
                        "type": _item_type(path),
                        "category": _category_for(path),
                        "size": path.stat().st_size if path.is_file() else None,
                        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    }
                    for path in items
                ],
            }
        except SecurityError as exc:
            return {"ok": False, "allowed": False, "error": str(exc)}

    def plan(
        self,
        source_root: str | None = None,
        target_root: str | None = None,
        *,
        recursive: bool = False,
        limit: int = 100,
        scheme: str = "by_type",
        include_folders: bool = False,
    ) -> dict:
        try:
            source = self._resolve_allowed(source_root, label="source_root")
            target = self._resolve_allowed(target_root, label="target_root") if target_root else source / "_Jarvis_Organized"
            assert_inside_any_root(target, self.allowed_roots, label="target_root")
            if source == target:
                return {"ok": False, "allowed": False, "error": "source_root y target_root son iguales"}
            if not source.exists() or not source.is_dir():
                return {"ok": False, "allowed": True, "error": f"source_root no es carpeta existente: {source}"}

            items, warnings = self._iter_items(
                source,
                recursive=recursive,
                limit=max(1, min(limit, self.max_plan_items)),
                include_folders=include_folders,
            )
            moves: list[dict[str, Any]] = []
            for src in items:
                if target in src.parents:
                    continue
                category = _category_for(src)
                stat = src.stat()
                if scheme == "by_year_month":
                    modified = datetime.fromtimestamp(stat.st_mtime)
                    dest = target / category / f"{modified.year:04d}-{modified.month:02d}" / src.name
                elif scheme == "by_extension":
                    ext = src.suffix.lower().lstrip(".") or "no-extension"
                    dest = target / ext / src.name
                else:
                    dest = target / category / src.name
                dest = _unique_destination(dest)
                if src.resolve() == dest.resolve():
                    continue
                moves.append({
                    "source": str(src),
                    "destination": str(dest),
                    "type": _item_type(src),
                    "category": category,
                    "size": stat.st_size if src.is_file() else None,
                })

            plan_id = uuid.uuid4().hex[:12]
            plan_doc = {
                "id": plan_id,
                "created_at": _now_iso(),
                "source_root": str(source),
                "target_root": str(target),
                "recursive": recursive,
                "include_folders": include_folders,
                "scheme": scheme,
                "moves": moves,
                "warnings": warnings,
                "applied": False,
            }
            plan_path = self.plans_dir / f"{plan_id}.json"
            plan_path.write_text(json.dumps(plan_doc, indent=2, ensure_ascii=False), encoding="utf-8")
            return {
                "ok": True,
                "plan_id": plan_id,
                "plan_path": str(plan_path),
                "move_count": len(moves),
                "total_bytes": sum(int(move["size"] or 0) for move in moves),
                "warnings": warnings,
                "preview": moves[:25],
                "requires_approval_to_apply": True,
            }
        except SecurityError as exc:
            return {"ok": False, "allowed": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "allowed": True, "error": f"{type(exc).__name__}: {exc}"}

    def _load_plan(self, plan_id: str) -> tuple[Path, dict]:
        safe_id = "".join(ch for ch in (plan_id or "") if ch.isalnum() or ch in "-_")
        if not safe_id:
            raise SecurityError("plan_id requerido")
        path = (self.plans_dir / f"{safe_id}.json").resolve()
        assert_inside_any_root(path, [self.plans_dir], label="plan")
        if not path.exists():
            raise SecurityError(f"plan no existe: {safe_id}")
        return path, json.loads(path.read_text(encoding="utf-8"))

    def _request_apply_approval(self, plan: dict) -> bool:
        if self.approval_broker is None:
            return False
        preview = "\n".join(
            f"- {Path(move['source']).name} -> {move['destination']}"
            for move in plan.get("moves", [])[:8]
        )
        details = (
            f"Plan: {plan.get('id')}\n"
            f"Movimientos: {len(plan.get('moves', []))}\n"
            f"Origen: {plan.get('source_root')}\n"
            f"Destino: {plan.get('target_root')}\n\n"
            f"Preview:\n{preview}"
        )
        return bool(self.approval_broker.request(
            risk="file_move",
            title="Jarvis quiere organizar archivos",
            details=details,
            timeout_s=45.0,
        ))

    def _request_preview_approval(self, plan: dict) -> bool:
        if self.approval_broker is None:
            return False
        preview_dir = Path(plan.get("target_root", "")) / "_PREVIEW"
        return bool(self.approval_broker.request(
            risk="file_preview",
            title="Jarvis quiere crear una vista previa de organizacion",
            details=(
                f"Plan: {plan.get('id')}\n"
                f"Carpeta preview: {preview_dir}\n"
                "No mueve archivos originales. Crea carpetas vacias y MOVE_PLAN.md."
            ),
            timeout_s=30.0,
        ))

    def preview(self, plan_id: str) -> dict:
        try:
            _, plan = self._load_plan(plan_id)
            if not self._request_preview_approval(plan):
                return {
                    "ok": False,
                    "allowed": False,
                    "executed": False,
                    "error": "preview rechazado o sin aprobacion HITL",
                }
            target = self._resolve_allowed(plan.get("target_root"), label="target_root")
            preview_dir = target.parent / f"{target.name}_PREVIEW"
            assert_inside_any_root(preview_dir, self.allowed_roots, label="preview_dir")
            moves = plan.get("moves") or []
            categories = sorted({str(move.get("category") or "Other") for move in moves})
            preview_dir.mkdir(parents=True, exist_ok=True)
            for category in categories:
                (preview_dir / category).mkdir(parents=True, exist_ok=True)
            lines = [
                f"# Jarvis organizer preview - {plan.get('id')}",
                "",
                "Esta carpeta es solo una vista previa. No contiene tus archivos originales.",
                f"Origen: {plan.get('source_root')}",
                f"Destino real propuesto: {plan.get('target_root')}",
                f"Movimientos propuestos: {len(moves)}",
                "",
                "## Primeros movimientos",
            ]
            for move in moves[:100]:
                lines.append(f"- `{move.get('source')}` -> `{move.get('destination')}`")
            if len(moves) > 100:
                lines.append(f"- ... {len(moves) - 100} movimientos adicionales omitidos.")
            (preview_dir / "MOVE_PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {
                "ok": True,
                "allowed": True,
                "executed": True,
                "preview_dir": str(preview_dir),
                "categories": categories,
                "move_count": len(moves),
                "note": "Preview creado: carpetas vacias y MOVE_PLAN.md; no se movieron archivos.",
            }
        except SecurityError as exc:
            return {"ok": False, "allowed": False, "executed": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "allowed": True, "executed": False, "error": f"{type(exc).__name__}: {exc}"}

    def apply(self, plan_id: str) -> dict:
        try:
            plan_path, plan = self._load_plan(plan_id)
            if plan.get("applied"):
                return {"ok": False, "allowed": False, "error": "plan ya fue aplicado"}
            moves = plan.get("moves") or []
            if not moves:
                return {"ok": True, "allowed": True, "executed": False, "message": "plan vacio"}
            if not self._request_apply_approval(plan):
                return {"ok": False, "allowed": False, "executed": False, "error": "organizacion rechazada o sin aprobacion HITL"}
            if self.mode != "prod":
                return {
                    "ok": False,
                    "allowed": True,
                    "executed": False,
                    "mode": self.mode,
                    "error": (
                        "No movi archivos: el organizador esta en dry-run. "
                        "Configura JARVIS_ORGANIZER_MODE=prod para habilitar movimientos reales."
                    ),
                    "stdout": "dry-run: JARVIS_ORGANIZER_MODE/JARVIS_MODE no es prod",
                    "would_move": len(moves),
                    "requires": "JARVIS_ORGANIZER_MODE=prod",
                }

            applied: list[dict] = []
            skipped: list[dict] = []
            for move in moves:
                src = self._resolve_allowed(move.get("source"), label="source")
                dest = self._resolve_allowed(move.get("destination"), label="destination")
                if not src.exists():
                    skipped.append({**move, "reason": "source no existe"})
                    continue
                expected_type = move.get("type") or "file"
                if expected_type == "dir" and not src.is_dir():
                    skipped.append({**move, "reason": "source no es carpeta"})
                    continue
                if expected_type != "dir" and not src.is_file():
                    skipped.append({**move, "reason": "source no es archivo"})
                    continue
                if is_secret_path(src) or is_secret_path(dest):
                    skipped.append({**move, "reason": "path sensible"})
                    continue
                if src.is_dir() and _dir_contains_blocked_path(src):
                    skipped.append({**move, "reason": "carpeta contiene paths sensibles/internos"})
                    continue
                if src.drive.lower() != dest.drive.lower():
                    skipped.append({**move, "reason": "cross-volume move bloqueado"})
                    continue
                dest = _unique_destination(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dest)
                applied.append({**move, "destination": str(dest)})

            plan["applied"] = True
            plan["applied_at"] = _now_iso()
            plan["applied_count"] = len(applied)
            plan["skipped"] = skipped
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
            return {
                "ok": True,
                "allowed": True,
                "executed": True,
                "mode": self.mode,
                "applied_count": len(applied),
                "skipped_count": len(skipped),
                "applied": applied[:50],
                "skipped": skipped[:50],
                "manifest": str(plan_path),
            }
        except SecurityError as exc:
            return {"ok": False, "allowed": False, "executed": False, "error": str(exc)}
        except (OSError, shutil.Error) as exc:
            return {"ok": False, "allowed": True, "executed": True, "error": f"{type(exc).__name__}: {exc}"}
