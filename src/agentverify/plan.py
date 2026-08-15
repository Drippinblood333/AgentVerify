"""Verification plan loading and deterministic digest support."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentverify.domain import VerificationPlan


class PlanError(Exception):
    """Base class for expected verification plan input errors."""


class PlanFileError(PlanError):
    """The plan path cannot be resolved to a readable regular file."""


class PlanEncodingError(PlanError):
    """The plan is not encoded as UTF-8."""


class PlanJSONError(PlanError):
    """The plan does not contain syntactically valid JSON."""


class PlanValidationError(PlanError):
    """The decoded JSON does not satisfy Verification Plan v1."""


def _format_location(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "plan"


def _format_validation_error(error: ValidationError) -> str:
    issues: list[str] = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = _format_location(issue["loc"])
        issues.append(f"{location}: {issue['msg']}")
    return "; ".join(issues)


def load_plan(path: Path) -> VerificationPlan:
    """Read a UTF-8 JSON file and validate it as Verification Plan v1."""
    try:
        normalized_path = path.expanduser().resolve()
    except OSError as error:
        raise PlanFileError(f"could not resolve plan path: {error}") from error

    if not normalized_path.exists():
        raise PlanFileError(f"plan does not exist: {normalized_path}")
    if not normalized_path.is_file():
        raise PlanFileError(f"plan must be a regular file: {normalized_path}")

    try:
        content = normalized_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise PlanEncodingError(f"plan is not valid UTF-8: {normalized_path}") from error
    except OSError as error:
        raise PlanFileError(f"could not read plan: {normalized_path}: {error}") from error

    try:
        json.loads(content)
    except json.JSONDecodeError as error:
        raise PlanJSONError(
            f"malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    try:
        return VerificationPlan.model_validate_json(content)
    except ValidationError as error:
        raise PlanValidationError(_format_validation_error(error)) from error


def plan_digest(plan: VerificationPlan) -> str:
    """Return a stable content fingerprint for a validated plan."""
    payload: dict[str, Any] = plan.model_dump(mode="json")
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_json).hexdigest()}"
