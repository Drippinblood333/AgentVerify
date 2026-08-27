"""Real local Git tests for the narrow M9 disposable-worktree adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import agentverify.worktree as worktree_module
from agentverify.worktree import (
    GitRevisionConfigurationError,
    ManagedGitWorktree,
    resolve_revision,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentVerify Test")
    git(repo, "config", "user.email", "agentverify@example.invalid")
    (repo / "source.txt").write_text("revision A\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "revision A")
    return repo, git(repo, "rev-parse", "HEAD")


def test_symbolic_selector_is_frozen_before_branch_moves(tmp_path: Path) -> None:
    repo, revision_a = repository(tmp_path)
    branch = git(repo, "branch", "--show-current")
    resolved = resolve_revision(repo, branch)

    (repo / "source.txt").write_text("revision B\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "revision B")
    assert git(repo, "rev-parse", branch) != revision_a

    managed = ManagedGitWorktree.create(resolved)
    source_root = managed.source_root
    try:
        assert git(source_root, "rev-parse", "HEAD") == revision_a
        assert (source_root / "source.txt").read_text(encoding="utf-8") == "revision A\n"
    finally:
        assert managed.cleanup()
    assert not source_root.exists()
    assert str(source_root) not in git(repo, "worktree", "list", "--porcelain")


def test_gitlink_revision_is_rejected_before_worktree_creation(tmp_path: Path) -> None:
    repo, revision = repository(tmp_path)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{revision},vendor/sub")
    git(repo, "commit", "-m", "add gitlink")

    before = git(repo, "worktree", "list", "--porcelain")
    with pytest.raises(GitRevisionConfigurationError, match="submodules"):
        resolve_revision(repo, "HEAD")
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_resolved_revision_records_dirty_caller_without_modifying_it(tmp_path: Path) -> None:
    repo, revision = repository(tmp_path)
    (repo / "source.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    before = git(repo, "status", "--porcelain=v1", "--untracked-files=normal")

    resolved = resolve_revision(repo, revision)

    assert resolved.caller_dirty_worktree is True
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=normal") == before


def test_partial_setup_failure_removes_exact_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = repository(tmp_path)
    resolved = resolve_revision(repo, revision)
    before = git(repo, "worktree", "list", "--porcelain")
    original = worktree_module._read_dirty_state

    def fail_new_worktree_status(path: Path) -> bool:
        if path != repo:
            raise GitRevisionConfigurationError("injected setup inspection failure")
        return original(path)

    monkeypatch.setattr(worktree_module, "_read_dirty_state", fail_new_worktree_status)
    with pytest.raises(GitRevisionConfigurationError, match="injected"):
        ManagedGitWorktree.create(resolved)
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_interrupt_after_worktree_add_still_removes_exact_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, revision = repository(tmp_path)
    resolved = resolve_revision(repo, revision)
    before = git(repo, "worktree", "list", "--porcelain")
    original = worktree_module._run_git

    def interrupt_after_add(argv: list[str]) -> subprocess.CompletedProcess[str]:
        result = original(argv)
        if "worktree" in argv and "add" in argv:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(worktree_module, "_run_git", interrupt_after_add)
    with pytest.raises(KeyboardInterrupt):
        ManagedGitWorktree.create(resolved)
    assert git(repo, "worktree", "list", "--porcelain") == before
