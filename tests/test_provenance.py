"""Real Git tests for the read-only M7 source provenance adapter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentverify_evidence.provenance import capture_source_provenance


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("Git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentVerify Tests")
    git(repo, "config", "user.email", "agentverify@example.invalid")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def test_clean_repository_records_full_head(git_repository: Path) -> None:
    provenance = capture_source_provenance(git_repository)

    assert provenance.kind == "git"
    assert provenance.revision == git(git_repository, "rev-parse", "HEAD")
    assert provenance.dirty_worktree is False
    assert provenance.git_version is not None
    assert provenance.reason is None


def test_modified_tracked_file_is_dirty(git_repository: Path) -> None:
    (git_repository / "tracked.txt").write_text("modified\n", encoding="utf-8")

    assert capture_source_provenance(git_repository).dirty_worktree is True


def test_staged_modification_is_dirty(git_repository: Path) -> None:
    (git_repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git(git_repository, "add", "tracked.txt")

    assert capture_source_provenance(git_repository).dirty_worktree is True


def test_untracked_file_is_dirty(git_repository: Path) -> None:
    (git_repository / "untracked.txt").write_text("new\n", encoding="utf-8")

    assert capture_source_provenance(git_repository).dirty_worktree is True


def test_outside_git_is_explicitly_unavailable(tmp_path: Path) -> None:
    provenance = capture_source_provenance(tmp_path)

    assert provenance.kind == "unavailable"
    assert provenance.revision is None
    assert provenance.dirty_worktree is None
    assert provenance.reason == "Current working directory is not inside a Git repository"


def test_missing_git_executable_is_explicitly_unavailable(tmp_path: Path) -> None:
    provenance = capture_source_provenance(
        tmp_path,
        git_executable="agentverify-git-executable-that-does-not-exist",
    )

    assert provenance.kind == "unavailable"
    assert provenance.reason == "Git executable is unavailable"
