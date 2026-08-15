"""Tests for fixture-derived proof receipt construction and rendering."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentverify.domain import AcceptanceCriterion, Verdict, VerificationPlan, VerificationResult
from agentverify.receipt import (
    EnvironmentMetadata,
    ProofReceipt,
    ReceiptCriterionResult,
    ReceiptValidationError,
    build_receipt,
    render_receipt_json,
    render_receipt_text,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
ENVIRONMENT = EnvironmentMetadata(
    agentverify_version="0.1.0.dev0",
    python_version="3.14.3",
    platform="Windows",
)
LIMITATIONS = (
    "Results were supplied as fixtures.",
    "No application execution occurred.",
)


def make_plan() -> VerificationPlan:
    """Create the frozen plan used by all proof receipt fixtures."""
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


def result(
    criterion_id: str,
    verdict: Verdict,
    reason: str,
    *evidence_refs: str,
) -> VerificationResult:
    """Create one compact, explicit fixture result."""
    return VerificationResult(
        criterion_id=criterion_id,
        verdict=verdict,
        reason=reason,
        evidence_refs=evidence_refs,
    )


def build_fixture_receipt(case: str) -> ProofReceipt:
    """Build one of the three golden receipt cases."""
    cases = {
        "pass": (
            result(
                "AC-001",
                Verdict.PASS,
                "Reset request returned the expected state.",
                "fixtures/reset-request.json",
            ),
            result(
                "AC-002",
                Verdict.PASS,
                "Invalid reset token was rejected.",
                "fixtures/invalid-token.json",
            ),
        ),
        "fail": (
            result(
                "AC-001",
                Verdict.PASS,
                "Reset request returned the expected state.",
                "fixtures/reset-request.json",
            ),
            result(
                "AC-002",
                Verdict.FAIL,
                "Invalid reset token was accepted.",
                "fixtures/invalid-token.json",
            ),
        ),
        "unknown": (
            result(
                "AC-001",
                Verdict.PASS,
                "Reset request returned the expected state.",
                "fixtures/reset-request.json",
            ),
            result(
                "AC-002",
                Verdict.UNKNOWN,
                "The fixture did not include an invalid-token observation.",
            ),
        ),
    }
    return build_receipt(
        plan=make_plan(),
        results=cases[case],
        completed=True,
        environment=ENVIRONMENT,
        limitations=LIMITATIONS,
    )


@pytest.mark.parametrize("case", ["pass", "fail", "unknown"])
def test_golden_receipts(case: str) -> None:
    receipt = build_fixture_receipt(case)

    assert render_receipt_json(receipt) == (GOLDEN_DIR / f"{case}.json").read_text(
        encoding="utf-8"
    )
    assert render_receipt_text(receipt) == (GOLDEN_DIR / f"{case}.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_conclusive_results_require_evidence(verdict: Verdict) -> None:
    with pytest.raises(ValidationError, match="require at least one evidence reference"):
        result("AC-001", verdict, "A conclusive result needs a reference.")


def test_unknown_result_can_have_no_evidence() -> None:
    unknown = result("AC-001", Verdict.UNKNOWN, "The verifier was unavailable.")

    assert unknown.evidence_refs == ()


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_receipt_criterion_rejects_conclusive_verdict_without_evidence(
    verdict: Verdict,
) -> None:
    with pytest.raises(ValidationError, match="require at least one evidence reference"):
        ReceiptCriterionResult(
            criterion_id="AC-001",
            description="A user can request a password reset",
            verdict=verdict,
            reason="A conclusive result needs a reference.",
            evidence_refs=(),
        )


def test_result_rejects_a_blank_reason() -> None:
    with pytest.raises(ValidationError):
        result("AC-001", Verdict.UNKNOWN, "   ")


def test_build_receipt_rejects_empty_results() -> None:
    with pytest.raises(ReceiptValidationError, match="at least one verification result"):
        build_receipt(
            plan=make_plan(),
            results=(),
            completed=True,
            environment=ENVIRONMENT,
            limitations=LIMITATIONS,
        )


def test_build_receipt_rejects_missing_result() -> None:
    with pytest.raises(ReceiptValidationError, match="missing result for criterion id: AC-002"):
        build_receipt(
            plan=make_plan(),
            results=(
                result(
                    "AC-001",
                    Verdict.PASS,
                    "Reset request returned the expected state.",
                    "fixtures/reset-request.json",
                ),
            ),
            completed=True,
            environment=ENVIRONMENT,
            limitations=LIMITATIONS,
        )


def test_build_receipt_rejects_duplicate_result_id() -> None:
    with pytest.raises(ReceiptValidationError, match="result criterion id must be unique: AC-001"):
        build_receipt(
            plan=make_plan(),
            results=(
                result("AC-001", Verdict.PASS, "First observation.", "fixtures/first.json"),
                result("AC-001", Verdict.PASS, "Second observation.", "fixtures/second.json"),
                result("AC-002", Verdict.PASS, "Token was rejected.", "fixtures/token.json"),
            ),
            completed=True,
            environment=ENVIRONMENT,
            limitations=LIMITATIONS,
        )


def test_build_receipt_rejects_unknown_criterion_id() -> None:
    with pytest.raises(ReceiptValidationError, match="unknown criterion id: AC-999"):
        build_receipt(
            plan=make_plan(),
            results=(
                result("AC-001", Verdict.PASS, "Request worked.", "fixtures/request.json"),
                result("AC-999", Verdict.PASS, "Unexpected result.", "fixtures/unknown.json"),
            ),
            completed=True,
            environment=ENVIRONMENT,
            limitations=LIMITATIONS,
        )


def test_interrupted_run_cannot_produce_pass() -> None:
    receipt = build_receipt(
        plan=make_plan(),
        results=(
            result("AC-001", Verdict.PASS, "Request worked.", "fixtures/request.json"),
            result("AC-002", Verdict.PASS, "Token was rejected.", "fixtures/token.json"),
        ),
        completed=False,
        environment=ENVIRONMENT,
        limitations=LIMITATIONS,
    )

    assert receipt.overall_verdict is Verdict.UNKNOWN
    assert "Run completed: no" in render_receipt_text(receipt)


def test_fail_remains_fail_when_the_run_is_interrupted() -> None:
    receipt = build_receipt(
        plan=make_plan(),
        results=(
            result("AC-001", Verdict.FAIL, "Request was rejected.", "fixtures/request.json"),
            result("AC-002", Verdict.PASS, "Token was rejected.", "fixtures/token.json"),
        ),
        completed=False,
        environment=ENVIRONMENT,
        limitations=LIMITATIONS,
    )

    assert receipt.overall_verdict is Verdict.FAIL


def test_build_receipt_requires_explicit_limitations() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        build_receipt(
            plan=make_plan(),
            results=(
                result("AC-001", Verdict.PASS, "Request worked.", "fixtures/request.json"),
                result("AC-002", Verdict.PASS, "Token was rejected.", "fixtures/token.json"),
            ),
            completed=True,
            environment=ENVIRONMENT,
            limitations=(),
        )


def test_proof_receipt_rejects_overall_pass_with_a_fail_criterion() -> None:
    receipt = build_fixture_receipt("fail")

    with pytest.raises(ValidationError, match="expected FAIL, got PASS"):
        ProofReceipt(
            schema_version=receipt.schema_version,
            task=receipt.task,
            plan_digest=receipt.plan_digest,
            overall_verdict=Verdict.PASS,
            completed=receipt.completed,
            criteria=receipt.criteria,
            environment=receipt.environment,
            limitations=receipt.limitations,
        )


def test_proof_receipt_rejects_pass_for_an_incomplete_run() -> None:
    receipt = build_fixture_receipt("pass")

    with pytest.raises(ValidationError, match="expected UNKNOWN, got PASS"):
        ProofReceipt(
            schema_version=receipt.schema_version,
            task=receipt.task,
            plan_digest=receipt.plan_digest,
            overall_verdict=receipt.overall_verdict,
            completed=False,
            criteria=receipt.criteria,
            environment=receipt.environment,
            limitations=receipt.limitations,
        )


def test_proof_receipt_json_rejects_duplicate_criterion_ids() -> None:
    payload = build_fixture_receipt("pass").model_dump(mode="json")
    criteria = payload["criteria"]
    assert isinstance(criteria, list)
    criteria.append(dict(criteria[0]))

    with pytest.raises(ValidationError, match="receipt criterion id must be unique: AC-001"):
        ProofReceipt.model_validate_json(json.dumps(payload))


def test_proof_receipt_round_trips_through_rendered_json() -> None:
    receipt = build_fixture_receipt("unknown")

    assert ProofReceipt.model_validate_json(render_receipt_json(receipt)) == receipt


def test_receipt_uses_frozen_plan_order_not_result_input_order() -> None:
    receipt = build_receipt(
        plan=make_plan(),
        results=(
            result("AC-002", Verdict.PASS, "Token was rejected.", "fixtures/token.json"),
            result("AC-001", Verdict.PASS, "Request worked.", "fixtures/request.json"),
        ),
        completed=True,
        environment=ENVIRONMENT,
        limitations=LIMITATIONS,
    )

    assert [criterion.criterion_id for criterion in receipt.criteria] == ["AC-001", "AC-002"]
    assert receipt.criteria[0].description == "A user can request a password reset"


def test_repeated_rendering_is_byte_for_byte_stable() -> None:
    receipt = build_fixture_receipt("pass")

    assert render_receipt_json(receipt) == render_receipt_json(receipt)
    assert render_receipt_text(receipt) == render_receipt_text(receipt)
    assert render_receipt_json(receipt).endswith("\n")
    assert render_receipt_text(receipt).endswith("\n")
