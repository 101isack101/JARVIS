from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from proactivity.git_repos import RepoStatus, scan_repo_status


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_clean_repo_is_excluded(tmp_path: Path):
    _make_repo(tmp_path, "clean")
    result = scan_repo_status(tmp_path)
    assert result == []


def test_dirty_repo_is_reported(tmp_path: Path):
    repo = _make_repo(tmp_path, "dirty")
    (repo / "new.txt").write_text("y", encoding="utf-8")
    result = scan_repo_status(tmp_path)
    assert len(result) == 1
    assert result[0].name == "dirty"
    assert result[0].dirty >= 1
    assert isinstance(result[0], RepoStatus)


def test_non_git_dir_ignored(tmp_path: Path):
    (tmp_path / "not_a_repo").mkdir()
    assert scan_repo_status(tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path):
    assert scan_repo_status(tmp_path / "nope") == []
