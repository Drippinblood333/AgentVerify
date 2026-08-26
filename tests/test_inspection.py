"""Read-only run-directory integrity inspection and CLI behavior tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify.cli import EXIT_SUCCESS, EXIT_UNKNOWN, EXIT_USAGE, main
from agentverify.domain import AcceptanceCriterion, Verdict, VerificationPlan, VerificationResult
from agentverify.evidence import EvidenceKind, EvidenceStore
from agentverify.inspection import inspect_run_directory, sha256_file
from agentverify.provenance import SourceProvenance
from agentverify.receipt import (
    EnvironmentMetadataV2,
    ProofReceiptV2,
    build_receipt_v2,
    load_receipt,
    render_receipt_json,
)


def make_run(run_dir: Path) -> Path:
    return make_authority_run(
        run_dir,
        verdict=Verdict.PASS,
        referenced_kind=EvidenceKind.BROWSER_OBSERVATION,
        referenced_criterion_id="AC-001",
    )


def make_authority_run(
    run_dir: Path,
    *,
    verdict: Verdict,
    referenced_kind: EvidenceKind,
    referenced_criterion_id: str | None,
    include_unreferenced_observation: bool = False,
) -> Path:
    store = EvidenceStore(run_dir)
    if include_unreferenced_observation:
        store.record_json(
            kind=EvidenceKind.BROWSER_OBSERVATION,
            payload={"criterion_id": "AC-001", "reason": "visible", "verdict": "PASS"},
            producer="test",
            criterion_id="AC-001",
        )
    if referenced_kind is EvidenceKind.PROCESS_LOG:
        referenced = store.record_process_log(
            "application diagnostic",
            producer="test",
            criterion_id=referenced_criterion_id,
        )
    elif referenced_kind is EvidenceKind.SCREENSHOT:
        referenced = store.record_bytes(
            kind=EvidenceKind.SCREENSHOT,
            data=b"png",
            media_type="image/png",
            producer="test",
            criterion_id=referenced_criterion_id,
        )
    else:
        referenced = store.record_json(
            kind=EvidenceKind.BROWSER_OBSERVATION,
            payload={
                "criterion_id": "AC-001",
                "reason": "browser outcome",
                "verdict": verdict.value,
            },
            producer="test",
            criterion_id=referenced_criterion_id,
        )
    manifest_path = store.write_manifest()
    plan = VerificationPlan(
        schema_version=1,
        task="Inspect evidence authority",
        criteria=(AcceptanceCriterion(id="AC-001", description="Greeting appears"),),
    )
    receipt = build_receipt_v2(
        plan=plan,
        results=(
            VerificationResult(
                criterion_id="AC-001",
                verdict=verdict,
                reason="fixture outcome",
                evidence_refs=(referenced.relative_path,),
            ),
        ),
        completed=verdict is not Verdict.UNKNOWN,
        environment=EnvironmentMetadataV2(
            agentverify_version="0.1.0.dev0",
            python_version="3.14.3",
            platform="test",
            playwright_version="1.61.0",
        ),
        source_provenance=SourceProvenance(
            kind="unavailable",
            reason="Fixture is not a source checkout",
        ),
        evidence_manifest_digest=sha256_file(manifest_path),
        limitations=("Fixture-derived run.",),
    )
    (run_dir / "receipt.json").write_text(
        render_receipt_json(receipt),
        encoding="utf-8",
    )
    return run_dir


def test_intact_v2_run_inspects_successfully(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run")

    inspection = inspect_run_directory(run_dir)

    assert inspection.receipt.overall_verdict is Verdict.PASS
    assert inspection.receipt.schema_version == 2


def test_cli_inspect_reports_valid_run(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    run_dir = make_run(tmp_path / "run")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert "Verdict: PASS" in captured.out
    assert "Integrity: OK" in captured.out
    assert "Receipt schema: 2" in captured.out
    assert "Manifest: sha256:" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_conclusive_receipt_cannot_use_global_process_log(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    verdict: Verdict,
) -> None:
    run_dir = make_authority_run(
        tmp_path / "run",
        verdict=verdict,
        referenced_kind=EvidenceKind.PROCESS_LOG,
        referenced_criterion_id=None,
        include_unreferenced_observation=True,
    )

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "not assigned to its criterion" in captured.err
    assert "Integrity: OK" not in captured.out


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_conclusive_receipt_requires_referenced_browser_observation(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    verdict: Verdict,
) -> None:
    run_dir = make_authority_run(
        tmp_path / "run",
        verdict=verdict,
        referenced_kind=EvidenceKind.SCREENSHOT,
        referenced_criterion_id="AC-001",
        include_unreferenced_observation=True,
    )

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "lacks a referenced browser observation" in captured.err
    assert "Integrity: OK" not in captured.out


@pytest.mark.parametrize("verdict", [Verdict.PASS, Verdict.FAIL])
def test_conclusive_receipt_with_referenced_browser_observation_is_valid(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    verdict: Verdict,
) -> None:
    run_dir = make_authority_run(
        tmp_path / "run",
        verdict=verdict,
        referenced_kind=EvidenceKind.BROWSER_OBSERVATION,
        referenced_criterion_id="AC-001",
    )

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert f"Verdict: {verdict.value}" in captured.out
    assert "Integrity: OK" in captured.out
    assert captured.err == ""


def test_unknown_receipt_may_reference_global_process_log(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dir = make_authority_run(
        tmp_path / "run",
        verdict=Verdict.UNKNOWN,
        referenced_kind=EvidenceKind.PROCESS_LOG,
        referenced_criterion_id=None,
    )

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert "Verdict: UNKNOWN" in captured.out
    assert "Integrity: OK" in captured.out
    assert captured.err == ""


def test_cli_inspect_manifest_mismatch_is_integrity_warning(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run")
    manifest_path = run_dir / "evidence-manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "does not match" in captured.err
    assert "Verdict:" not in captured.out


def test_cli_inspect_artifact_tampering_is_integrity_warning(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run")
    artifact_path = next((run_dir / "artifacts").iterdir())
    artifact_path.write_bytes(b"tampered")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "mismatch" in captured.err


def test_cli_inspect_invalid_path_and_v1_receipt_are_usage_errors(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    missing_exit = main(["inspect", "--run-dir", str(tmp_path / "missing")])
    missing_output = capsys.readouterr()
    v1_run = tmp_path / "v1"
    v1_run.mkdir()
    golden = Path(__file__).parent / "golden" / "pass.json"
    (v1_run / "receipt.json").write_bytes(golden.read_bytes())
    v1_exit = main(["inspect", "--run-dir", str(v1_run)])
    v1_output = capsys.readouterr()

    assert missing_exit == EXIT_USAGE
    assert "does not exist" in missing_output.err
    assert v1_exit == EXIT_USAGE
    assert "schema 1" in v1_output.err


def test_cli_inspect_malformed_receipt_is_usage_error(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dir = tmp_path / "malformed"
    run_dir.mkdir()
    (run_dir / "receipt.json").write_text("{", encoding="utf-8")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "malformed or unsupported" in captured.err


def test_cli_inspect_rejects_mismatched_criterion_association(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dir = make_run(tmp_path / "run")
    receipt_path = run_dir / "receipt.json"
    receipt = load_receipt(receipt_path)
    assert isinstance(receipt, ProofReceiptV2)
    payload = receipt.model_dump(mode="json")
    payload["criteria"][0]["criterion_id"] = "AC-OTHER"
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["inspect", "--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "unknown receipt criterion" in captured.err
