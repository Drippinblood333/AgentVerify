"""Read-only integrity inspection for an existing AgentVerify run directory."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentverify.domain import Verdict
from agentverify.evidence import (
    EVIDENCE_MANIFEST_FILENAME,
    EvidenceArtifact,
    EvidenceError,
    EvidenceKind,
    EvidenceStore,
)
from agentverify.receipt import (
    ProofReceiptV2,
    ProofReceiptV3,
    ProofReceiptV4,
    ReceiptCriterionResult,
    ReceiptLoadError,
    load_receipt,
)

RECEIPT_JSON_FILENAME = "receipt.json"


class InspectionInputError(ValueError):
    """The requested run path or receipt is not a supported inspection input."""


class RunIntegrityError(Exception):
    """Persisted run files no longer satisfy their integrity relationships."""


@dataclass(frozen=True, slots=True)
class RunInspection:
    """Successful integrity inspection summary."""

    run_root: Path
    receipt: ProofReceiptV2 | ProofReceiptV3 | ProofReceiptV4


def sha256_file(path: Path) -> str:
    """Measure exact persisted bytes with a streaming SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def inspect_run_directory(run_dir: Path) -> RunInspection:
    """Validate receipt, manifest binding, artifact bytes, and criterion references."""
    run_root = _resolve_run_root(run_dir)
    receipt_path = _resolve_run_file(run_root, RECEIPT_JSON_FILENAME, input_file=True)
    try:
        receipt = load_receipt(receipt_path)
    except ReceiptLoadError as error:
        raise InspectionInputError(str(error)) from error
    if not isinstance(receipt, (ProofReceiptV2, ProofReceiptV3, ProofReceiptV4)):
        raise InspectionInputError(
            "receipt schema 1 does not include an evidence-manifest binding"
        )

    try:
        manifest_path = _resolve_run_file(
            run_root,
            EVIDENCE_MANIFEST_FILENAME,
            input_file=False,
        )
        current_digest = sha256_file(manifest_path)
    except (OSError, RunIntegrityError) as error:
        if isinstance(error, RunIntegrityError):
            raise
        raise RunIntegrityError("evidence manifest could not be measured") from error
    if current_digest != receipt.evidence_manifest_digest:
        raise RunIntegrityError(
            "current evidence manifest does not match the digest recorded in the receipt"
        )

    try:
        store = EvidenceStore(run_root)
        manifest = store.load_manifest()
        store.verify_manifest(manifest)
    except EvidenceError as error:
        raise RunIntegrityError(str(error)) from error

    artifacts_by_path = {
        artifact.relative_path: artifact for artifact in manifest.artifacts
    }
    criterion_ids = {criterion.criterion_id for criterion in receipt.criteria}
    for artifact in manifest.artifacts:
        if artifact.criterion_id is not None and artifact.criterion_id not in criterion_ids:
            raise RunIntegrityError(
                "manifest artifact references an unknown receipt criterion: "
                f"{artifact.relative_path}"
            )
    for criterion in receipt.criteria:
        _validate_criterion_evidence_authority(criterion, artifacts_by_path)
    return RunInspection(run_root=run_root, receipt=receipt)


def _validate_criterion_evidence_authority(
    criterion: ReceiptCriterionResult,
    artifacts_by_path: Mapping[str, EvidenceArtifact],
) -> None:
    conclusive = criterion.verdict in {Verdict.PASS, Verdict.FAIL}
    has_referenced_browser_observation = False
    for evidence_ref in criterion.evidence_refs:
        artifact = artifacts_by_path.get(evidence_ref)
        if artifact is None:
            raise RunIntegrityError(
                f"receipt evidence reference is absent from manifest: {evidence_ref}"
            )
        allowed_criterion_ids = (
            {criterion.criterion_id} if conclusive else {None, criterion.criterion_id}
        )
        if artifact.criterion_id not in allowed_criterion_ids:
            if conclusive:
                raise RunIntegrityError(
                    "conclusive receipt evidence reference is not assigned to its criterion: "
                    f"{evidence_ref}"
                )
            raise RunIntegrityError(
                "receipt evidence reference belongs to a different criterion: "
                f"{evidence_ref}"
            )
        if artifact.kind is EvidenceKind.BROWSER_OBSERVATION:
            has_referenced_browser_observation = True

    if conclusive and not has_referenced_browser_observation:
        raise RunIntegrityError(
            "conclusive receipt criterion lacks a referenced browser observation: "
            f"{criterion.criterion_id}"
        )


def _resolve_run_root(run_dir: Path) -> Path:
    try:
        if not run_dir.exists():
            raise InspectionInputError("run directory does not exist")
        resolved = run_dir.resolve(strict=True)
        if not resolved.is_dir():
            raise InspectionInputError("run directory path must be a directory")
        return resolved
    except InspectionInputError:
        raise
    except OSError as error:
        raise InspectionInputError("run directory could not be inspected") from error


def _resolve_run_file(run_root: Path, name: str, *, input_file: bool) -> Path:
    label = "proof receipt" if input_file else "evidence manifest"
    candidate = run_root / name
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        exception = InspectionInputError if input_file else RunIntegrityError
        raise exception(f"{label} does not exist") from error
    except OSError as error:
        exception = InspectionInputError if input_file else RunIntegrityError
        raise exception(f"{label} could not be resolved") from error
    if not resolved.is_relative_to(run_root) or not resolved.is_file():
        exception = InspectionInputError if input_file else RunIntegrityError
        raise exception(f"{label} is not a safe regular file")
    return resolved
