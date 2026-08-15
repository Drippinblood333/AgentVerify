"""Tests for Verification Plan v1 loading and validation."""

import json
from pathlib import Path

import pytest

from agentverify.plan import (
    PlanEncodingError,
    PlanFileError,
    PlanJSONError,
    PlanValidationError,
    load_plan,
    plan_digest,
)

VALID_PLAN: dict[str, object] = {
    "schema_version": 1,
    "task": "Implement password reset",
    "criteria": [
        {
            "id": "AC-001",
            "description": "A user can request a password reset",
        },
        {
            "id": "AC-002",
            "description": "Invalid reset tokens are rejected",
        },
    ],
}


def write_json(path: Path, data: object) -> None:
    """Write JSON test input without involving AgentVerify serialization."""
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_plan_accepts_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    write_json(path, VALID_PLAN)

    plan = load_plan(path)

    assert plan.schema_version == 1
    assert plan.task == "Implement password reset"
    assert [criterion.id for criterion in plan.criteria] == ["AC-001", "AC-002"]


def test_semantically_identical_json_has_the_same_digest(tmp_path: Path) -> None:
    compact = tmp_path / "compact.json"
    formatted = tmp_path / "formatted.json"
    compact.write_text(json.dumps(VALID_PLAN, separators=(",", ":")), encoding="utf-8")
    formatted.write_text(json.dumps(VALID_PLAN, indent=4), encoding="utf-8")

    assert plan_digest(load_plan(compact)) == plan_digest(load_plan(formatted))


def test_load_plan_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PlanFileError, match="plan does not exist"):
        load_plan(tmp_path / "missing.json")


def test_load_plan_rejects_a_directory(tmp_path: Path) -> None:
    with pytest.raises(PlanFileError, match="plan must be a regular file"):
        load_plan(tmp_path)


def test_load_plan_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.json"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(PlanEncodingError, match="plan is not valid UTF-8"):
        load_plan(path)


def test_load_plan_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text('{"schema_version": 1,', encoding="utf-8")

    with pytest.raises(PlanJSONError, match=r"malformed JSON at line 1, column \d+"):
        load_plan(path)


@pytest.mark.parametrize(
    ("data", "expected_location"),
    [
        (
            {
                "task": "Task",
                "criteria": [{"id": "AC-001", "description": "Works"}],
            },
            "schema_version",
        ),
        (
            {
                "schema_version": 2,
                "task": "Task",
                "criteria": [{"id": "AC-001", "description": "Works"}],
            },
            "criteria.0.procedure",
        ),
        (
            {
                "schema_version": 1,
                "criteria": [{"id": "AC-001", "description": "Works"}],
            },
            "task",
        ),
        (
            {
                "schema_version": 1,
                "task": "   ",
                "criteria": [{"id": "AC-001", "description": "Works"}],
            },
            "task",
        ),
        (
            {
                "schema_version": 1,
                "task": 123,
                "criteria": [{"id": "AC-001", "description": "Works"}],
            },
            "task",
        ),
        ({"schema_version": 1, "task": "Task"}, "criteria"),
        ({"schema_version": 1, "task": "Task", "criteria": []}, "criteria"),
        (
            {
                "schema_version": 1,
                "task": "Task",
                "criteria": [{"description": "Works"}],
            },
            "criteria.0.id",
        ),
        (
            {
                "schema_version": 1,
                "task": "Task",
                "criteria": [{"id": "AC-001", "description": "   "}],
            },
            "criteria.0.description",
        ),
        (
            {
                "schema_version": 1,
                "task": "Task",
                "criteria": [
                    {"id": "AC-001", "description": "First"},
                    {"id": "AC-001", "description": "Second"},
                ],
            },
            "criteria",
        ),
        (
            {
                "schema_version": 1,
                "task": "Task",
                "criteria": [{"id": "AC-001", "description": "Works"}],
                "provider": "vendor",
            },
            "provider",
        ),
        (
            {
                "schema_version": 1,
                "task": "Task",
                "criteria": [
                    {
                        "id": "AC-001",
                        "description": "Works",
                        "selector": "#submit",
                    }
                ],
            },
            "criteria.0.selector",
        ),
    ],
)
def test_load_plan_rejects_invalid_schema(
    tmp_path: Path,
    data: dict[str, object],
    expected_location: str,
) -> None:
    path = tmp_path / "invalid.json"
    write_json(path, data)

    with pytest.raises(PlanValidationError, match=expected_location):
        load_plan(path)
