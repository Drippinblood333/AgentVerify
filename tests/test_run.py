"""Evidence-gated browser execution result bridge tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentverify_evidence.browser import BrowserExecutionResult
from agentverify_evidence.browser_plan import BrowserVerificationPlan
from agentverify_evidence.domain import Verdict
from agentverify_evidence.evidence import EvidenceKind, EvidenceStore
from agentverify_evidence.run import ResultCoverageError, build_browser_verification_results


def plan(*criterion_ids: str) -> BrowserVerificationPlan:
    return BrowserVerificationPlan.model_validate_json(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Bridge browser outcomes",
                "criteria": [
                    {
                        "id": criterion_id,
                        "description": f"Criterion {criterion_id}",
                        "procedure": {
                            "type": "browser",
                            "steps": [
                                {"type": "navigate", "path": "/"},
                                {"type": "assert_visible", "selector": "#ok"},
                            ],
                        },
                    }
                    for criterion_id in criterion_ids
                ],
            }
        )
    )


def durable_execution(
    store: EvidenceStore,
    criterion_id: str,
    verdict: Verdict,
) -> BrowserExecutionResult:
    artifact = store.record_json(
        kind=EvidenceKind.BROWSER_OBSERVATION,
        payload={"verdict": verdict.value},
        producer="test",
        criterion_id=criterion_id,
    )
    return BrowserExecutionResult(
        criterion_id=criterion_id,
        verdict=verdict,
        reason=f"browser said {verdict.value}",
        evidence_refs=(artifact.relative_path,),
    )


def supplemental_artifact(
    store: EvidenceStore,
    criterion_id: str,
    kind: EvidenceKind,
) -> str:
    artifact = store.record_bytes(
        kind=kind,
        data=b"supplemental",
        media_type=("image/png" if kind is EvidenceKind.SCREENSHOT else "application/zip"),
        producer="test",
        criterion_id=criterion_id,
    )
    return artifact.relative_path


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_intact_durable_evidence_preserves_conclusive_verdict(
    tmp_path: Path,
    verdict: Verdict,
) -> None:
    store = EvidenceStore(tmp_path)
    execution = durable_execution(store, "AC-001", verdict)

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is verdict
    assert result.evidence_refs == execution.evidence_refs


def test_pass_without_durable_evidence_becomes_unknown(tmp_path: Path) -> None:
    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(
            BrowserExecutionResult("AC-001", Verdict.PASS, "browser passed"),
        ),
        manifest=EvidenceStore(tmp_path).build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == ()
    assert "trustworthy durable evidence" in result.reason


@pytest.mark.parametrize(
    ("verdict", "kind"),
    [
        (Verdict.PASS, EvidenceKind.SCREENSHOT),
        (Verdict.FAIL, EvidenceKind.PLAYWRIGHT_TRACE),
    ],
)
def test_supplemental_evidence_cannot_substitute_for_browser_observation(
    tmp_path: Path,
    verdict: Verdict,
    kind: EvidenceKind,
) -> None:
    store = EvidenceStore(tmp_path)
    evidence_ref = supplemental_artifact(store, "AC-001", kind)
    execution = BrowserExecutionResult(
        criterion_id="AC-001",
        verdict=verdict,
        reason="conclusive browser outcome",
        evidence_refs=(evidence_ref,),
    )

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == (evidence_ref,)
    assert "trustworthy durable evidence" in result.reason


@pytest.mark.parametrize(
    ("verdict", "remove_observation"),
    [(Verdict.PASS, False), (Verdict.FAIL, True)],
)
def test_broken_observation_is_not_replaced_by_intact_rich_evidence(
    tmp_path: Path,
    verdict: Verdict,
    remove_observation: bool,
) -> None:
    store = EvidenceStore(tmp_path)
    execution = durable_execution(store, "AC-001", verdict)
    screenshot_ref = supplemental_artifact(
        store, "AC-001", EvidenceKind.SCREENSHOT
    )
    observation_path = tmp_path / execution.evidence_refs[0]
    if remove_observation:
        observation_path.unlink()
    else:
        original = observation_path.read_bytes()
        observation_path.write_bytes(b"x" * len(original))
    execution = BrowserExecutionResult(
        criterion_id="AC-001",
        verdict=verdict,
        reason="conclusive browser outcome",
        evidence_refs=(*execution.evidence_refs, screenshot_ref),
    )

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == (screenshot_ref,)


def test_unreferenced_observation_cannot_authorize_supplemental_evidence(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    durable_execution(store, "AC-001", Verdict.PASS)
    screenshot_ref = supplemental_artifact(
        store, "AC-001", EvidenceKind.SCREENSHOT
    )
    execution = BrowserExecutionResult(
        criterion_id="AC-001",
        verdict=Verdict.PASS,
        reason="browser passed",
        evidence_refs=(screenshot_ref,),
    )

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == (screenshot_ref,)


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_missing_or_corrupted_evidence_becomes_unknown(
    tmp_path: Path,
    verdict: Verdict,
) -> None:
    store = EvidenceStore(tmp_path)
    execution = durable_execution(store, "AC-001", verdict)
    evidence_path = tmp_path / execution.evidence_refs[0]
    if verdict is Verdict.PASS:
        evidence_path.unlink()
    else:
        original = evidence_path.read_bytes()
        evidence_path.write_bytes(b"x" * len(original))

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == ()


def test_manifest_omission_or_digest_mismatch_becomes_unknown(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    execution = durable_execution(store, "AC-001", Verdict.FAIL)

    omitted = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=EvidenceStore(tmp_path / "empty").build_manifest(),
        evidence_root=tmp_path,
    )[0]
    manifest = store.build_manifest().model_copy(
        update={
            "artifacts": (
                store.artifacts[0].model_copy(
                    update={"digest": f"sha256:{'f' * 64}"}
                ),
            )
        }
    )
    mismatched = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=manifest,
        evidence_root=tmp_path,
    )[0]

    assert omitted.verdict is Verdict.UNKNOWN
    assert mismatched.verdict is Verdict.UNKNOWN


def test_unknown_remains_unknown_and_keeps_only_valid_diagnostics(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    valid = durable_execution(store, "AC-001", Verdict.UNKNOWN)
    execution = BrowserExecutionResult(
        criterion_id="AC-001",
        verdict=Verdict.UNKNOWN,
        reason="browser uncertain",
        evidence_refs=(*valid.evidence_refs, "artifacts/missing.json"),
    )

    result = build_browser_verification_results(
        plan=plan("AC-001"),
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.reason == "browser uncertain"
    assert result.evidence_refs == valid.evidence_refs


def test_results_follow_plan_order(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    second = durable_execution(store, "AC-002", Verdict.PASS)
    first = durable_execution(store, "AC-001", Verdict.FAIL)

    results = build_browser_verification_results(
        plan=plan("AC-001", "AC-002"),
        executions=(second, first),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )

    assert [result.criterion_id for result in results] == ["AC-001", "AC-002"]


@pytest.mark.parametrize(
    "executions",
    [
        (BrowserExecutionResult("AC-UNKNOWN", Verdict.UNKNOWN, "unknown"),),
        (
            BrowserExecutionResult("AC-001", Verdict.UNKNOWN, "first"),
            BrowserExecutionResult("AC-001", Verdict.UNKNOWN, "duplicate"),
        ),
        (),
    ],
)
def test_bridge_rejects_unknown_duplicate_or_missing_coverage(
    tmp_path: Path,
    executions: tuple[BrowserExecutionResult, ...],
) -> None:
    with pytest.raises(ResultCoverageError):
        build_browser_verification_results(
            plan=plan("AC-001"),
            executions=executions,
            manifest=EvidenceStore(tmp_path).build_manifest(),
            evidence_root=tmp_path,
        )
