"""Receipt-v4 source-selection, plan-source, and compatibility tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from agentverify_evidence.browser_plan import BrowserVerificationPlan
from agentverify_evidence.domain import Verdict, VerificationResult
from agentverify_evidence.provenance import SourceProvenance
from agentverify_evidence.receipt import (
    DirectExecutionMetadata,
    EnvironmentMetadataV2,
    ExternalPlanSource,
    GitWorktreeSourceSelection,
    ProofReceipt,
    ProofReceiptV2,
    ProofReceiptV3,
    ProofReceiptV4,
    RepositoryPlanSource,
    build_receipt_v4,
    load_receipt,
    render_receipt_json,
    render_receipt_text,
)


def plan() -> BrowserVerificationPlan:
    return BrowserVerificationPlan.model_validate_json(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Verify selected source",
                "criteria": [
                    {
                        "id": "AC-001",
                        "description": "Selected source is observable",
                        "procedure": {
                            "type": "browser",
                            "steps": [
                                {"type": "navigate", "path": "/"},
                                {"type": "assert_visible", "selector": "#message"},
                            ],
                        },
                    }
                ],
            }
        )
    )


def build_v4(
    *,
    cleanup_confirmed: bool = True,
    post_run_source_state: Literal["clean", "dirty", "unknown"] = "clean",
) -> ProofReceiptV4:
    return build_receipt_v4(
        plan=plan(),
        results=(
            VerificationResult(
                criterion_id="AC-001",
                verdict=Verdict.PASS,
                reason="Selected source was visible.",
                evidence_refs=("artifacts/000001-browser-observation.json",),
            ),
        ),
        completed=cleanup_confirmed and post_run_source_state == "clean",
        environment=EnvironmentMetadataV2(
            agentverify_version="0.1.0.dev0",
            python_version="3.14.3",
            platform="Linux",
            playwright_version="1.61.0",
        ),
        source_provenance=SourceProvenance(
            kind="git",
            revision="a" * 40,
            dirty_worktree=False,
            git_version="git version 2.51.0",
        ),
        evidence_manifest_digest=f"sha256:{'b' * 64}",
        execution=DirectExecutionMetadata(),
        source_selection=GitWorktreeSourceSelection(
            requested_revision="release-candidate",
            resolved_revision="a" * 40,
            caller_head_revision="c" * 40,
            caller_dirty_worktree=True,
            post_run_source_state=post_run_source_state,
            cleanup_confirmed=cleanup_confirmed,
        ),
        plan_source=RepositoryPlanSource(
            repository_relative_path="verification/greeting.plan.json",
            caller_source_revision="c" * 40,
            caller_dirty_worktree=True,
        ),
        limitations=("Fixture-derived M9 receipt.",),
    )


def test_receipt_v4_records_exact_source_and_repository_plan_without_absolute_path() -> None:
    receipt = build_v4()
    rendered_json = render_receipt_json(receipt)
    rendered_text = render_receipt_text(receipt)

    assert receipt.schema_version == 4
    assert '"resolved_revision":"' + "a" * 40 + '"' in rendered_json
    assert '"repository_relative_path":"verification/greeting.plan.json"' in rendered_json
    assert "Requested revision: release-candidate" in rendered_text
    assert "Worktree cleanup confirmed: yes" in rendered_text
    assert "Plan repository path: verification/greeting.plan.json" in rendered_text


def test_external_plan_source_has_no_path_field() -> None:
    payload = ExternalPlanSource().model_dump(mode="json")
    assert payload == {"kind": "external"}


@pytest.mark.parametrize("path", ("/absolute/plan.json", "../plan.json", "dir\\plan.json"))
def test_repository_plan_source_rejects_nonportable_or_escaping_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        RepositoryPlanSource(
            repository_relative_path=path,
            caller_source_revision="a" * 40,
            caller_dirty_worktree=False,
        )


def test_loader_preserves_v1_v2_v3_and_accepts_v4(tmp_path: Path) -> None:
    historical = (
        (Path(__file__).parent / "golden" / "pass.json", ProofReceipt),
        (Path(__file__).parent / "golden" / "pass-v2.json", ProofReceiptV2),
        (Path(__file__).parent / "golden" / "pass-v3.json", ProofReceiptV3),
    )
    for path, expected_type in historical:
        if path.exists():
            assert isinstance(load_receipt(path), expected_type)
    v4_path = tmp_path / "v4.json"
    receipt = build_v4()
    v4_path.write_text(render_receipt_json(receipt), encoding="utf-8")
    assert load_receipt(v4_path) == receipt


def test_cleanup_failure_makes_pass_results_unknown() -> None:
    receipt = build_v4(cleanup_confirmed=False)
    assert receipt.completed is False
    assert receipt.overall_verdict is Verdict.UNKNOWN


@pytest.mark.parametrize("state", ("dirty", "unknown"))
def test_non_clean_post_run_source_state_makes_pass_results_unknown(
    state: Literal["dirty", "unknown"],
) -> None:
    receipt = build_v4(post_run_source_state=state)
    assert receipt.completed is False
    assert receipt.overall_verdict is Verdict.UNKNOWN


@pytest.mark.parametrize("state", ("dirty", "unknown"))
def test_receipt_v4_rejects_completed_non_clean_source_state(
    state: Literal["dirty", "unknown"],
) -> None:
    payload = build_v4().model_dump(mode="python")
    payload["source_selection"]["post_run_source_state"] = state
    with pytest.raises(ValidationError, match="non-clean"):
        ProofReceiptV4.model_validate(payload)


def test_receipt_v4_rejects_unsupported_post_run_source_state() -> None:
    payload = build_v4().model_dump(mode="python")
    payload["source_selection"]["post_run_source_state"] = "unavailable"
    with pytest.raises(ValidationError):
        ProofReceiptV4.model_validate(payload)
