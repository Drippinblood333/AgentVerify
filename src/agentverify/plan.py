"""Verification plan loading and deterministic digest support."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import VerificationPlan

type SupportedVerificationPlan = VerificationPlan | BrowserVerificationPlan


class PlanError(Exception):
    """Base class for expected verification plan input errors."""


class PlanFileError(PlanError):
    """The plan path cannot be resolved to a readable regular file."""


class PlanEncodingError(PlanError):
    """The plan is not encoded as UTF-8."""


class PlanJSONError(PlanError):
    """The plan does not contain syntactically valid JSON."""


class PlanValidationError(PlanError):
    """The decoded JSON does not satisfy a supported Verification Plan version."""


def _format_location(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "plan"


def _format_validation_error(error: ValidationError) -> str:
    issues: list[str] = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = _format_location(issue["loc"])
        issues.append(f"{location}: {issue['msg']}")
    return "; ".join(issues)


def load_plan(path: Path) -> SupportedVerificationPlan:
    """Read a UTF-8 JSON file and validate an explicitly supported plan version."""
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
        payload: Any = json.loads(content)
    except json.JSONDecodeError as error:
        raise PlanJSONError(
            f"malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    if not isinstance(payload, dict):
        raise PlanValidationError("plan: Input should be a valid object")

    if "schema_version" not in payload:
        raise PlanValidationError("schema_version: Field required")

    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise PlanValidationError(
            f"schema_version: unsupported schema version: {schema_version!r}"
        )

    model = VerificationPlan if schema_version == 1 else BrowserVerificationPlan
    try:
        return model.model_validate_json(content)
    except ValidationError as error:
        raise PlanValidationError(_format_validation_error(error)) from error


def plan_digest(plan: SupportedVerificationPlan) -> str:
    """Return a stable content fingerprint for a validated plan."""
    payload: dict[str, Any] = plan.model_dump(mode="json")
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_json).hexdigest()}"
