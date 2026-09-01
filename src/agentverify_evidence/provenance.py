"""Read-only source provenance capture for the current working directory."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from agentverify_evidence.domain import NonBlankText

_REVISION_PATTERN = r"^[0-9a-f]{40}$"
_DEFAULT_TIMEOUT_SECONDS = 3.0


class SourceProvenance(BaseModel):
    """Strict source metadata for a Git worktree or an unavailable adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["git", "unavailable"]
    revision: Annotated[
        str,
        StringConstraints(strict=True, pattern=_REVISION_PATTERN),
    ] | None = None
    dirty_worktree: bool | None = None
    git_version: NonBlankText | None = None
    reason: NonBlankText | None = None

    @model_validator(mode="after")
    def require_consistent_variant(self) -> SourceProvenance:
        if self.kind == "git":
            if self.revision is None or self.dirty_worktree is None:
                raise ValueError("Git provenance requires revision and dirty-worktree state")
            if self.reason is not None:
                raise ValueError("Git provenance must not include an unavailable reason")
        elif self.revision is not None or self.dirty_worktree is not None:
            raise ValueError("unavailable provenance must not claim Git source state")
        elif self.reason is None:
            raise ValueError("unavailable provenance requires a reason")
        return self


def capture_source_provenance(
    cwd: Path | None = None,
    *,
    git_executable: str = "git",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> SourceProvenance:
    """Capture bounded, read-only Git metadata without blocking verification."""
    working_directory = (cwd or Path.cwd()).resolve()
    git_version: str | None = None
    try:
        version_result = _run_git(
            [git_executable, "--version"],
            timeout_seconds=timeout_seconds,
        )
        if version_result.returncode == 0:
            git_version = _concise_output(version_result.stdout)

        root_result = _run_git(
            [git_executable, "-C", str(working_directory), "rev-parse", "--show-toplevel"],
            timeout_seconds=timeout_seconds,
        )
        if root_result.returncode != 0:
            return _unavailable(
                "Current working directory is not inside a Git repository",
                git_version=git_version,
            )

        revision_result = _run_git(
            [git_executable, "-C", str(working_directory), "rev-parse", "HEAD"],
            timeout_seconds=timeout_seconds,
        )
        revision = revision_result.stdout.strip().lower()
        if revision_result.returncode != 0 or re.fullmatch(_REVISION_PATTERN, revision) is None:
            return _unavailable(
                "Git HEAD revision could not be read",
                git_version=git_version,
            )

        status_result = _run_git(
            [
                git_executable,
                "-C",
                str(working_directory),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            timeout_seconds=timeout_seconds,
        )
        if status_result.returncode != 0:
            return _unavailable(
                "Git worktree status could not be read",
                git_version=git_version,
            )
        return SourceProvenance(
            kind="git",
            revision=revision,
            dirty_worktree=bool(status_result.stdout),
            git_version=git_version,
        )
    except FileNotFoundError:
        return _unavailable("Git executable is unavailable")
    except subprocess.TimeoutExpired:
        return _unavailable("Git provenance capture timed out", git_version=git_version)
    except OSError:
        return _unavailable("Git provenance capture is unavailable", git_version=git_version)


def _run_git(
    argv: list[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )


def _concise_output(output: str) -> str | None:
    concise = " ".join(output.split())[:128]
    return concise or None


def _unavailable(reason: str, *, git_version: str | None = None) -> SourceProvenance:
    return SourceProvenance(
        kind="unavailable",
        git_version=git_version,
        reason=reason,
    )
