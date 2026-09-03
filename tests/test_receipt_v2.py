"""Receipt-v2 metadata and dual-version loading tests."""

from __future__ import annotations

import json
from pathlib import Path

from donewitness.browser_plan import BrowserVerificationPlan
from donewitness.domain import Verdict, VerificationResult
from donewitness.provenance import SourceProvenance
from donewitness.receipt import (
    EnvironmentMetadataV2,
    ProofReceipt,
    ProofReceiptV2,
    build_receipt_v2,
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


def receipt_v2() -> ProofReceiptV2:
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
            donewitness_version="0.1.0.dev0",
            python_version="3.14.3",
            platform="Windows",
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


def test_receipt_v2_contains_structured_m7_metadata() -> None:
    receipt = receipt_v2()
    rendered = render_receipt_text(receipt)

    assert receipt.schema_version == 2
    assert receipt.environment.playwright_version == "1.61.0"
    assert receipt.source_provenance.revision == "a" * 40
    assert receipt.evidence_manifest_digest == f"sha256:{'b' * 64}"
    assert "Receipt schema: 2" in rendered
    assert "Dirty worktree: no" in rendered


def test_loader_accepts_receipt_v1_and_v2(tmp_path: Path) -> None:
    v1_source = Path(__file__).parent / "golden" / "pass.json"
    v1_path = tmp_path / "v1.json"
    v2_path = tmp_path / "v2.json"
    v1_path.write_bytes(v1_source.read_bytes())
    v2_path.write_text(render_receipt_json(receipt_v2()), encoding="utf-8")

    assert isinstance(load_receipt(v1_path), ProofReceipt)
    loaded_v2 = load_receipt(v2_path)
    assert isinstance(loaded_v2, ProofReceiptV2)
    assert loaded_v2 == receipt_v2()
