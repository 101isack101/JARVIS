"""Escaneo fail-safe del estado git de los repos bajo una raíz.

Devuelve solo repos con algo que reportar (cambios sin commitear o sin push).
Ningún método propaga excepciones: el briefing nunca debe romper el arranque.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoStatus:
    name: str
    dirty: int   # nº de líneas de `git status --porcelain`
    ahead: int   # commits sin push (0 si no hay upstream)
    branch: str


def _git(repo: Path, *args: str, timeout_s: float) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout_s,
    )
    return out.stdout if out.returncode == 0 else ""


def _status_for(repo: Path, timeout_s: float) -> RepoStatus | None:
    try:
        porcelain = _git(repo, "status", "--porcelain", timeout_s=timeout_s)
        dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD",
                      timeout_s=timeout_s).strip() or "?"
        ahead_raw = _git(repo, "rev-list", "--count", "@{u}..HEAD",
                         timeout_s=timeout_s).strip()
        ahead = int(ahead_raw) if ahead_raw.isdigit() else 0
        if dirty == 0 and ahead == 0:
            return None
        return RepoStatus(name=repo.name, dirty=dirty, ahead=ahead, branch=branch)
    except Exception:
        return None


def scan_repo_status(root: Path, *, max_repos: int = 40,
                     per_repo_timeout_s: float = 3.0) -> list[RepoStatus]:
    try:
        root = Path(root)
        if not root.is_dir():
            return []
        results: list[RepoStatus] = []
        for child in sorted(root.iterdir()):
            if len(results) >= max_repos:
                break
            if not (child / ".git").exists():
                continue
            st = _status_for(child, per_repo_timeout_s)
            if st is not None:
                results.append(st)
        return results
    except Exception:
        return []
