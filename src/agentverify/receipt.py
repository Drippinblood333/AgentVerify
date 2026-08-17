"""Pure construction and deterministic rendering of proof receipts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentverify.domain import (
    NonBlankText,
    Verdict,
    VerificationResult,
    aggregate_verdict,
)
from agentverify.plan import SupportedVerificationPlan, plan_digest


class ReceiptValidationError(ValueError):
    """Supplied fixture results cannot form an auditable proof receipt."""


class _ReceiptPlanCriterion(Protocol):
    id: str
    description: str


class EnvironmentMetadata(BaseModel):
    """Explicit runtime metadata supplied by an outer orchestration layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agentverify_version: NonBlankText
    python_version: NonBlankText
    platform: NonBlankText


class ReceiptCriterionResult(BaseModel):
    """An immutable criterion snapshot included in a proof receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    criterion_id: NonBlankText
    description: NonBlankText
    verdict: Verdict
    reason: NonBlankText
    evidence_refs: tuple[NonBlankText, ...]

    @model_validator(mode="after")
    def require_evidence_for_conclusive_verdicts(self) -> ReceiptCriterionResult:
        """Require opaque evidence references for conclusive receipt entries."""
        if self.verdict in {Verdict.PASS, Verdict.FAIL} and not self.evidence_refs:
            raise ValueError("PASS and FAIL results require at least one evidence reference")
        return self


class ProofReceipt(BaseModel):
    """An immutable, fixture-derived record suitable for human review."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    task: NonBlankText
    plan_digest: NonBlankText
    overall_verdict: Verdict
    completed: bool
    criteria: Annotated[tuple[ReceiptCriterionResult, ...], Field(min_length=1)]
    environment: EnvironmentMetadata
    limitations: Annotated[tuple[NonBlankText, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[ReceiptCriterionResult, ...],
    ) -> tuple[ReceiptCriterionResult, ...]:
        """Reject receipts whose criterion snapshots are ambiguous."""
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.criterion_id in seen:
                raise ValueError(
                    f"receipt criterion id must be unique: {criterion.criterion_id}"
                )
            seen.add(criterion.criterion_id)
        return criteria

    @model_validator(mode="after")
    def require_consistent_overall_verdict(self) -> ProofReceipt:
        """Reject receipts whose stated verdict contradicts their own snapshots."""
        expected = aggregate_receipt_verdict(
            [criterion.verdict for criterion in self.criteria],
            completed=self.completed,
        )
        if self.overall_verdict is not expected:
            raise ValueError(
                "overall verdict must match criterion verdicts and completion state: "
                f"expected {expected.value}, got {self.overall_verdict.value}"
            )
        return self


def aggregate_receipt_verdict(
    verdicts: Sequence[Verdict],
    *,
    completed: bool,
) -> Verdict:
    """Apply run-completion semantics to M2's deterministic aggregation rule."""
    aggregate = aggregate_verdict(verdicts)
    if aggregate is Verdict.FAIL:
        return Verdict.FAIL
    if not completed:
        return Verdict.UNKNOWN
    return aggregate


def _index_results(
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
) -> dict[str, VerificationResult]:
    """Validate fixture result identity and completeness against a frozen plan."""
    if not results:
        raise ReceiptValidationError("at least one verification result is required")

    plan_ids = {criterion.id for criterion in plan.criteria}
    indexed: dict[str, VerificationResult] = {}
    for result in results:
        if result.criterion_id not in plan_ids:
            raise ReceiptValidationError(
                f"result refers to unknown criterion id: {result.criterion_id}"
            )
        if result.criterion_id in indexed:
            raise ReceiptValidationError(
                f"result criterion id must be unique: {result.criterion_id}"
            )
        indexed[result.criterion_id] = result

    missing_ids = [criterion.id for criterion in plan.criteria if criterion.id not in indexed]
    if missing_ids:
        raise ReceiptValidationError(
            f"missing result for criterion id: {', '.join(missing_ids)}"
        )
    return indexed


def build_receipt(
    *,
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
    completed: bool,
    environment: EnvironmentMetadata,
    limitations: Sequence[str],
) -> ProofReceipt:
    """Build a receipt without reading, executing, probing, or writing anything."""
    plan_criteria = cast(Sequence[_ReceiptPlanCriterion], plan.criteria)
    indexed_results = _index_results(plan, results)
    ordered_results = tuple(indexed_results[criterion.id] for criterion in plan_criteria)
    criteria = tuple(
        ReceiptCriterionResult(
            criterion_id=criterion.id,
            description=criterion.description,
            verdict=result.verdict,
            reason=result.reason,
            evidence_refs=result.evidence_refs,
        )
        for criterion, result in zip(plan_criteria, ordered_results, strict=True)
    )
    return ProofReceipt(
        task=plan.task,
        plan_digest=plan_digest(plan),
        overall_verdict=aggregate_receipt_verdict(
            [result.verdict for result in ordered_results],
            completed=completed,
        ),
        completed=completed,
        criteria=criteria,
        environment=environment,
        limitations=tuple(limitations),
    )


def render_receipt_json(receipt: ProofReceipt) -> str:
    """Render a receipt as canonical UTF-8-compatible JSON with one newline."""
    payload: dict[str, Any] = receipt.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


def render_receipt_text(receipt: ProofReceipt) -> str:
    """Render a receipt as deterministic plain terminal text."""
    lines = [
        "AGENTVERIFY PROOF RECEIPT",
        "",
        f"Task: {receipt.task}",
        f"Plan: {receipt.plan_digest}",
        f"Verdict: {receipt.overall_verdict.value}",
        f"Run completed: {'yes' if receipt.completed else 'no'}",
        f"AgentVerify: {receipt.environment.agentverify_version}",
        f"Python: {receipt.environment.python_version}",
        f"Platform: {receipt.environment.platform}",
        "",
        "Criteria",
        "",
    ]
    for criterion in receipt.criteria:
        evidence = ", ".join(criterion.evidence_refs) or "(none)"
        lines.extend(
            (
                f"{criterion.verdict.value:<7} {criterion.criterion_id}  {criterion.description}",
                f"        Reason: {criterion.reason}",
                f"        Evidence: {evidence}",
                "",
            )
        )

    lines.extend(("Limitations", ""))
    lines.extend(f"- {limitation}" for limitation in receipt.limitations)
    return "\n".join(lines) + "\n"
