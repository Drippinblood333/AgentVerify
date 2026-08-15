"""Narrow orchestration from browser outcomes to verified domain results."""

from __future__ import annotations

from pathlib import Path

from agentverify.browser import BrowserExecutionResult
from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import Verdict, VerificationResult
from agentverify.evidence import (
    EvidenceError,
    EvidenceKind,
    EvidenceManifest,
    EvidenceStore,
)


class ResultCoverageError(ValueError):
    """Browser execution results do not exactly cover their frozen plan."""


_UNSUPPORTED_EVIDENCE_REASON = (
    "Conclusive browser outcome could not be supported by trustworthy durable evidence"
)


def build_browser_verification_results(
    *,
    plan: BrowserVerificationPlan,
    executions: tuple[BrowserExecutionResult, ...],
    manifest: EvidenceManifest,
    evidence_root: Path,
) -> tuple[VerificationResult, ...]:
    """Validate coverage and evidence before constructing domain results."""
    criterion_ids = {criterion.id for criterion in plan.criteria}
    by_criterion: dict[str, BrowserExecutionResult] = {}
    for execution in executions:
        if execution.criterion_id not in criterion_ids:
            raise ResultCoverageError(
                f"result references unknown criterion: {execution.criterion_id}"
            )
        if execution.criterion_id in by_criterion:
            raise ResultCoverageError(
                f"duplicate result for criterion: {execution.criterion_id}"
            )
        by_criterion[execution.criterion_id] = execution

    missing = [
        criterion.id for criterion in plan.criteria if criterion.id not in by_criterion
    ]
    if missing:
        raise ResultCoverageError(f"missing result for criterion: {missing[0]}")

    artifacts_by_path = {
        artifact.relative_path: artifact for artifact in manifest.artifacts
    }
    store = EvidenceStore(evidence_root)
    results: list[VerificationResult] = []
    for criterion in plan.criteria:
        execution = by_criterion[criterion.id]
        validated_refs: list[str] = []
        evidence_is_trustworthy = bool(execution.evidence_refs)
        has_valid_browser_observation = False
        for evidence_ref in execution.evidence_refs:
            artifact = artifacts_by_path.get(evidence_ref)
            if artifact is None or artifact.criterion_id != criterion.id:
                evidence_is_trustworthy = False
                continue
            try:
                store.verify_artifact(artifact)
            except EvidenceError:
                evidence_is_trustworthy = False
                continue
            validated_refs.append(evidence_ref)
            if artifact.kind is EvidenceKind.BROWSER_OBSERVATION:
                has_valid_browser_observation = True

        verdict = execution.verdict
        reason = execution.reason
        conclusive_evidence_is_supported = (
            evidence_is_trustworthy and has_valid_browser_observation
        )
        if (
            verdict in {Verdict.PASS, Verdict.FAIL}
            and not conclusive_evidence_is_supported
        ):
            verdict = Verdict.UNKNOWN
            reason = _UNSUPPORTED_EVIDENCE_REASON

        results.append(
            VerificationResult(
                criterion_id=criterion.id,
                verdict=verdict,
                reason=reason,
                evidence_refs=tuple(validated_refs),
            )
        )
    return tuple(results)
