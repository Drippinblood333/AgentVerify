"""Focused tests for the versioned machine-readable CLI result contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, _verification_exit_code
from agentverify.cli_result import (
    OUTPUT_SCHEMA_VERSION,
    VerifySummary,
    render_verify_summary,
)
from agentverify.domain import Verdict


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (Verdict.PASS, EXIT_PASS),
        (Verdict.FAIL, EXIT_FAIL),
        (Verdict.UNKNOWN, EXIT_UNKNOWN),
    ],
)
def test_verdict_exit_code_is_the_single_result_mapping(
    verdict: Verdict,
    expected: int,
) -> None:
    assert _verification_exit_code(verdict) == expected


def test_machine_summary_rendering_is_compact_deterministic_json() -> None:
    summary = VerifySummary(
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        verdict="PASS",
        completed=True,
        exit_code=EXIT_PASS,
        receipt_schema_version=4,
        receipt_json_path=str(Path("C:/AgentVerify run/receipt.json")),
        receipt_text_path=str(Path("C:/AgentVerify run/receipt.txt")),
        evidence_manifest_path=str(Path("C:/AgentVerify run/evidence-manifest.json")),
    )

    rendered = render_verify_summary(summary)

    assert rendered.endswith("\n")
    assert rendered.count("\n") == 1
    assert ": " not in rendered
    assert json.loads(rendered)["receipt_json_path"] == summary.receipt_json_path
