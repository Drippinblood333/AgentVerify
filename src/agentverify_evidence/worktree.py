"""Bounded local Git adapter for disposable revision verification."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_GIT_TIMEOUT_SECONDS = 10.0


class GitRevisionConfigurationError(ValueError):
    """A requested revision cannot be selected safely from the local repository."""


class GitWorktreeOperationalError(Exception):
    """A resolved local revision could not be managed reliably."""


@dataclass(frozen=True, slots=True)
class ResolvedRevision:
    """One symbolic selector frozen to an exact local commit and caller state."""

    requested_revision: str
    resolved_revision: str
    repository_root: Path
    caller_head_revision: str
    caller_dirty_worktree: bool
    git_version: str


@dataclass(slots=True)
class ManagedGitWorktree:
    """Own one exact detached worktree and its system-temporary parent."""

    revision: ResolvedRevision
    temporary_root: Path
    source_root: Path
    empty_hooks_root: Path

    @classmethod
    def create(cls, revision: ResolvedRevision) -> ManagedGitWorktree:
        temporary_root = Path(tempfile.mkdtemp(prefix="agentverify-worktree-"))
        source_root = temporary_root / "source"
        empty_hooks_root = temporary_root / "empty-hooks"
        empty_hooks_root.mkdir()
        managed = cls(revision, temporary_root, source_root, empty_hooks_root)
        try:
            result = _run_git(
                [
                    "git",
                    "-c",
                    f"core.hooksPath={empty_hooks_root}",
                    "-C",
                    str(revision.repository_root),
                    "worktree",
                    "add",
                    "--detach",
                    str(source_root),
                    revision.resolved_revision,
                ]
            )
            if result.returncode != 0:
                raise GitWorktreeOperationalError(
                    "disposable Git worktree could not be created"
                )
            head = _read_exact_commit(source_root, "HEAD")
            if head != revision.resolved_revision:
                raise GitWorktreeOperationalError(
                    "disposable Git worktree HEAD did not match the resolved revision"
                )
            if _read_dirty_state(source_root):
                raise GitWorktreeOperationalError(
                    "new disposable Git worktree was unexpectedly dirty"
                )
            return managed
        except BaseException as error:
            cleanup_confirmed = managed.cleanup()
            if isinstance(error, KeyboardInterrupt):
                if not cleanup_confirmed:
                    raise GitWorktreeOperationalError(
                        "verification was interrupted and partial disposable "
                        "worktree cleanup could not be confirmed"
                    ) from error
                raise
            if not cleanup_confirmed:
                raise GitWorktreeOperationalError(
                    "partial disposable Git worktree setup could not be cleaned up"
                ) from error
            if isinstance(error, (GitRevisionConfigurationError, GitWorktreeOperationalError)):
                raise
            raise GitWorktreeOperationalError(
                "disposable Git worktree setup could not complete"
            ) from error

    def is_dirty(self) -> bool:
        """Inspect the managed source after application cleanup and before removal."""
        try:
            return _read_dirty_state(self.source_root)
        except GitRevisionConfigurationError as error:
            raise GitWorktreeOperationalError(
                "post-run disposable Git worktree state could not be inspected"
            ) from error

    def cleanup(self) -> bool:
        """Remove only this worktree and confirm registration and path absence."""
        try:
            if _worktree_is_registered(
                self.revision.repository_root,
                self.source_root,
            ):
                result = _run_git(
                    [
                        "git",
                        "-C",
                        str(self.revision.repository_root),
                        "worktree",
                        "remove",
                        "--force",
                        str(self.source_root),
                    ]
                )
                if result.returncode != 0:
                    return False
            if _worktree_is_registered(self.revision.repository_root, self.source_root):
                return False
            if self.temporary_root.exists():
                shutil.rmtree(self.temporary_root)
            return not self.source_root.exists() and not self.temporary_root.exists()
        except (GitWorktreeOperationalError, OSError, subprocess.SubprocessError):
            return False


def resolve_revision(invocation_root: Path, requested_revision: str) -> ResolvedRevision:
    """Resolve one local selector once without fetching or mutating caller state."""
    if not requested_revision.strip() or "\x00" in requested_revision:
        raise GitRevisionConfigurationError("revision must be a non-empty Git selector")
    try:
        version_result = _run_git(["git", "--version"])
        if version_result.returncode != 0:
            raise GitRevisionConfigurationError("Git executable is unavailable")
        git_version = " ".join(version_result.stdout.split())[:128]
        root_result = _run_git(
            ["git", "-C", str(invocation_root), "rev-parse", "--show-toplevel"]
        )
    except FileNotFoundError as error:
        raise GitRevisionConfigurationError("Git executable is unavailable") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise GitRevisionConfigurationError("local Git preflight could not complete") from error
    if root_result.returncode != 0:
        raise GitRevisionConfigurationError(
            "--revision requires the invocation directory to be inside a local Git repository"
        )
    repository_root = Path(root_result.stdout.strip()).resolve()
    resolved_revision = _read_exact_commit(repository_root, requested_revision)
    caller_head_revision = _read_exact_commit(repository_root, "HEAD")
    caller_dirty_worktree = _read_dirty_state(repository_root)
    try:
        tree_result = _run_git(
            [
                "git",
                "-C",
                str(repository_root),
                "ls-tree",
                "-r",
                "-z",
                resolved_revision,
            ]
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
        raise GitRevisionConfigurationError(
            "resolved revision tree could not be inspected"
        ) from error
    if tree_result.returncode != 0:
        raise GitRevisionConfigurationError("resolved revision tree could not be inspected")
    if any(record.startswith("160000 ") for record in tree_result.stdout.split("\x00")):
        raise GitRevisionConfigurationError(
            "requested revision contains Git submodules, which M9 does not support"
        )
    return ResolvedRevision(
        requested_revision=requested_revision,
        resolved_revision=resolved_revision,
        repository_root=repository_root,
        caller_head_revision=caller_head_revision,
        caller_dirty_worktree=caller_dirty_worktree,
        git_version=git_version or "git version unavailable",
    )


def discover_repository_root(invocation_root: Path) -> Path | None:
    """Return the local repository root when optional read-only Git discovery works."""
    try:
        result = _run_git(
            ["git", "-C", str(invocation_root), "rev-parse", "--show-toplevel"]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return Path(result.stdout.strip()).resolve()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _read_exact_commit(repository_root: Path, selector: str) -> str:
    try:
        result = _run_git(
            [
                "git",
                "-C",
                str(repository_root),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{selector}^{{commit}}",
            ]
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
        raise GitRevisionConfigurationError(
            "local Git revision could not be read"
        ) from error
    revision = result.stdout.strip().lower()
    if result.returncode != 0 or _COMMIT_PATTERN.fullmatch(revision) is None:
        raise GitRevisionConfigurationError(
            f"revision does not identify one local commit: {selector!r}"
        )
    return revision


def _read_dirty_state(repository_root: Path) -> bool:
    try:
        result = _run_git(
            [
                "git",
                "-C",
                str(repository_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ]
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
        raise GitRevisionConfigurationError("Git worktree status could not be read") from error
    if result.returncode != 0:
        raise GitRevisionConfigurationError("Git worktree status could not be read")
    return bool(result.stdout)


def _worktree_is_registered(repository_root: Path, source_root: Path) -> bool:
    result = _run_git(
        [
            "git",
            "-C",
            str(repository_root),
            "worktree",
            "list",
            "--porcelain",
            "-z",
        ]
    )
    if result.returncode != 0:
        raise GitWorktreeOperationalError("Git worktree registration could not be inspected")
    target = os.path.normcase(str(source_root.resolve()))
    registered_paths = (
        field.removeprefix("worktree ")
        for field in result.stdout.split("\x00")
        if field.startswith("worktree ")
    )
    return any(os.path.normcase(str(Path(path).resolve())) == target for path in registered_paths)


def _run_git(argv: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        argv,
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT_SECONDS,
        env=environment,
    )
