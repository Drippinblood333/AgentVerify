"""Stable machine-readable presentation for finalized verification results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agentverify_evidence.domain import Verdict

OUTPUT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class VerifySummary:
    """The versioned stdout contract for one finalized verification bundle."""

    output_schema_version: int
    verdict: str
    completed: bool
    exit_code: int
    receipt_schema_version: int
    receipt_json_path: str
    receipt_text_path: str
    evidence_manifest_path: str


def build_verify_summary(
    *,
    verdict: Verdict,
    completed: bool,
    exit_code: int,
    receipt_schema_version: int,
    receipt_json_path: Path,
    receipt_text_path: Path,
    evidence_manifest_path: Path,
) -> VerifySummary:
    """Build a summary only after the caller has a trusted finalized bundle."""
    return VerifySummary(
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        verdict=verdict.value,
        completed=completed,
        exit_code=exit_code,
        receipt_schema_version=receipt_schema_version,
        receipt_json_path=str(receipt_json_path.resolve()),
        receipt_text_path=str(receipt_text_path.resolve()),
        evidence_manifest_path=str(evidence_manifest_path.resolve()),
    )


def render_verify_summary(summary: VerifySummary) -> str:
    """Render deterministic compact JSON with exactly one trailing newline."""
    return (
        json.dumps(
            asdict(summary),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
