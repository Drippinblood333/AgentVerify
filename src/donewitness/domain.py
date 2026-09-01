"""Minimal domain models and deterministic verdict rules."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class AcceptanceCriterion(BaseModel):
    """One observable condition that the requested task must satisfy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: NonBlankText
    description: NonBlankText


class VerificationPlan(BaseModel):
    """A frozen, versioned set of acceptance criteria for one task."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    task: NonBlankText
    criteria: Annotated[tuple[AcceptanceCriterion, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[AcceptanceCriterion, ...],
    ) -> tuple[AcceptanceCriterion, ...]:
        """Reject ambiguous plans containing the same criterion ID more than once."""
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.id in seen:
                raise ValueError(f"criterion id must be unique: {criterion.id}")
            seen.add(criterion.id)
        return criteria


class Verdict(StrEnum):
    """The possible deterministic outcomes of a verification criterion."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class VerificationResult(BaseModel):
    """A supplied outcome for one frozen acceptance criterion."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    criterion_id: NonBlankText
    verdict: Verdict
    reason: NonBlankText
    evidence_refs: tuple[NonBlankText, ...] = ()

    @model_validator(mode="after")
    def require_evidence_for_conclusive_verdicts(self) -> VerificationResult:
        """Require opaque evidence references for conclusive supplied results."""
        if self.verdict in {Verdict.PASS, Verdict.FAIL} and not self.evidence_refs:
            raise ValueError("PASS and FAIL results require at least one evidence reference")
        return self


def aggregate_verdict(verdicts: Sequence[Verdict]) -> Verdict:
    """Aggregate a non-empty set of criterion verdicts deterministically."""
    if not verdicts:
        raise ValueError("at least one criterion verdict is required")
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    if Verdict.UNKNOWN in verdicts:
        return Verdict.UNKNOWN
    return Verdict.PASS
