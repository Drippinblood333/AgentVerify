"""Tests for M2 domain models and deterministic rules."""

import pytest
from pydantic import ValidationError

from agentverify_evidence.domain import (
    AcceptanceCriterion,
    Verdict,
    VerificationPlan,
    aggregate_verdict,
)
from agentverify_evidence.plan import plan_digest


def make_plan() -> VerificationPlan:
    """Create the canonical password-reset plan used in domain tests."""
    return VerificationPlan(
        schema_version=1,
        task="Implement password reset",
        criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description="A user can request a password reset",
            ),
            AcceptanceCriterion(
                id="AC-002",
                description="Invalid reset tokens are rejected",
            ),
        ),
    )


def test_valid_plan_round_trips() -> None:
    plan = make_plan()

    restored = VerificationPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert isinstance(restored.criteria, tuple)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "   "),
        ("description", "\t"),
    ],
)
def test_criterion_rejects_blank_strings(field: str, value: str) -> None:
    data = {"id": "AC-001", "description": "Works"}
    data[field] = value

    with pytest.raises(ValidationError):
        AcceptanceCriterion.model_validate(data)


def test_plan_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VerificationPlan.model_validate_json(
            '{"schema_version":1,"task":"Task","criteria":'
            '[{"id":"AC-001","description":"Works"}],"model":"vendor"}'
        )


def test_plan_rejects_duplicate_criterion_ids() -> None:
    with pytest.raises(ValidationError, match="criterion id must be unique: AC-001"):
        VerificationPlan(
            schema_version=1,
            task="Task",
            criteria=(
                AcceptanceCriterion(id="AC-001", description="First"),
                AcceptanceCriterion(id="AC-001", description="Second"),
            ),
        )


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([Verdict.PASS], Verdict.PASS),
        ([Verdict.PASS, Verdict.PASS], Verdict.PASS),
        ([Verdict.PASS, Verdict.UNKNOWN], Verdict.UNKNOWN),
        ([Verdict.UNKNOWN, Verdict.UNKNOWN], Verdict.UNKNOWN),
        ([Verdict.FAIL], Verdict.FAIL),
        ([Verdict.UNKNOWN, Verdict.FAIL, Verdict.PASS], Verdict.FAIL),
    ],
)
def test_aggregate_verdict(
    verdicts: list[Verdict],
    expected: Verdict,
) -> None:
    assert aggregate_verdict(verdicts) is expected


def test_aggregate_verdict_rejects_an_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one criterion verdict is required"):
        aggregate_verdict([])


def test_plan_digest_is_stable() -> None:
    assert plan_digest(make_plan()) == (
        "sha256:b29aa3da660a1ad6474ed475abf617a2dc79f77b686724eb6a2b8ee1e3af1e91"
    )


def test_plan_digest_preserves_criterion_order() -> None:
    plan = make_plan()
    reordered = VerificationPlan(
        schema_version=plan.schema_version,
        task=plan.task,
        criteria=tuple(reversed(plan.criteria)),
    )

    assert plan_digest(reordered) != plan_digest(plan)
