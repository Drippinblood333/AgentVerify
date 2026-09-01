"""Receipt-v3 execution metadata and compatibility tests."""

from __future__ import annotations

import json
from pathlib import Path

from agentverify_evidence.browser_plan import BrowserVerificationPlan
from agentverify_evidence.domain import Verdict, VerificationResult
from agentverify_evidence.provenance import SourceProvenance
from agentverify_evidence.receipt import (
    DirectExecutionMetadata,
    DockerExecutionMetadata,
    EnvironmentMetadataV2,
    ProofReceipt,
    ProofReceiptV2,
    ProofReceiptV3,
    build_receipt_v2,
    build_receipt_v3,
    load_receipt,
    render_receipt_json,
    render_receipt_text,
)


def plan() -> BrowserVerificationPlan:
    return BrowserVerificationPlan.model_validate_json(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Verify greeting",
                "criteria": [
                    {
                        "id": "AC-001",
                        "description": "Greeting appears",
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


def build_v3(execution: DirectExecutionMetadata | DockerExecutionMetadata) -> ProofReceiptV3:
    return build_receipt_v3(
        plan=plan(),
        results=(
            VerificationResult(
                criterion_id="AC-001",
                verdict=Verdict.PASS,
                reason="The greeting was visible.",
                evidence_refs=("artifacts/000001-browser-observation.json",),
            ),
        ),
        completed=True,
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
        execution=execution,
        limitations=("Fixture-derived M8 receipt.",),
    )


def build_historical_v2() -> ProofReceiptV2:
    return build_receipt_v2(
        plan=plan(),
        results=(
            VerificationResult(
                criterion_id="AC-001",
                verdict=Verdict.PASS,
                reason="The greeting was visible.",
                evidence_refs=("artifacts/000001-browser-observation.json",),
            ),
        ),
        completed=True,
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
        limitations=("Fixture-derived M7 receipt.",),
    )


def test_receipt_v3_records_direct_execution_without_docker_claims() -> None:
    receipt = build_v3(DirectExecutionMetadata())
    rendered = render_receipt_text(receipt)

    assert receipt.schema_version == 3
    assert receipt.execution.isolation_mode == "none"
    assert "Isolation mode: none" in rendered
    assert "Docker image ID:" not in rendered


def test_receipt_v3_records_resolved_docker_execution_identity() -> None:
    execution = DockerExecutionMetadata(
        isolation_profile="agentverify-docker-baseline-v1",
        docker_server_version="28.3.1",
        image_reference="python:3.12-slim",
        image_id=f"sha256:{'c' * 64}",
    )
    receipt = build_v3(execution)
    rendered = render_receipt_text(receipt)

    assert receipt.execution == execution
    assert "Isolation mode: docker" in rendered
    assert "Isolation profile: agentverify-docker-baseline-v1" in rendered
    assert "Docker image reference: python:3.12-slim" in rendered
    assert f"Docker image ID: sha256:{'c' * 64}" in rendered


def test_loader_accepts_receipt_v1_v2_and_v3(tmp_path: Path) -> None:
    v1_path = Path(__file__).parent / "golden" / "pass.json"
    v2_path = tmp_path / "v2.json"
    v3_path = tmp_path / "v3.json"
    receipt_v2 = build_historical_v2()
    v2_path.write_text(render_receipt_json(receipt_v2), encoding="utf-8")
    receipt_v3 = build_v3(DirectExecutionMetadata())
    v3_path.write_text(render_receipt_json(receipt_v3), encoding="utf-8")

    assert isinstance(load_receipt(v1_path), ProofReceipt)
    assert isinstance(load_receipt(v2_path), ProofReceiptV2)
    assert load_receipt(v3_path) == receipt_v3
