"""Evidence metadata, storage, privacy, portability, and integrity tests."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from donewitness.evidence import (
    EVIDENCE_MANIFEST_FILENAME,
    EvidenceArtifact,
    EvidenceIntegrityError,
    EvidenceKind,
    EvidenceLimitError,
    EvidenceLimits,
    EvidenceManifest,
    EvidenceStorageError,
    EvidenceStore,
    render_manifest_json,
)


def artifact(
    *,
    artifact_id: str = "EV-000001",
    relative_path: str = "artifacts/000001-browser-observation.json",
    size_bytes: int = 2,
    digest: str | None = None,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=artifact_id,
        kind=EvidenceKind.BROWSER_OBSERVATION,
        criterion_id="AC-001",
        media_type="application/json",
        relative_path=relative_path,
        size_bytes=size_bytes,
        digest=digest or f"sha256:{'0' * 64}",
        captured_at=datetime(2026, 8, 15, tzinfo=UTC),
        producer="donewitness.browser",
        redacted=False,
    )


def test_artifact_metadata_is_strict_frozen_and_utc() -> None:
    metadata = artifact()

    assert metadata.captured_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="frozen"):
        metadata.size_bytes = 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("digest", "sha256:NOT-A-DIGEST"),
        ("size_bytes", -1),
        ("captured_at", datetime(2026, 8, 15)),
    ],
)
def test_artifact_rejects_malformed_metadata(field: str, value: object) -> None:
    data = artifact().model_dump()
    data[field] = value

    with pytest.raises(ValidationError):
        EvidenceArtifact.model_validate(data)


def test_manifest_rejects_duplicate_ids_and_paths() -> None:
    first = artifact()
    duplicate_id = artifact(relative_path="artifacts/000002-screenshot.png")
    duplicate_path = artifact(artifact_id="EV-000002")

    with pytest.raises(ValidationError, match="duplicate artifact id"):
        EvidenceManifest(artifacts=(first, duplicate_id))
    with pytest.raises(ValidationError, match="duplicate artifact relative path"):
        EvidenceManifest(artifacts=(first, duplicate_path))


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "../secret",
        "/artifact",
        "C:\\artifact",
        "artifacts/../../secret",
        "artifacts\\..\\secret",
    ],
)
def test_artifact_rejects_unsafe_relative_paths(relative_path: str) -> None:
    with pytest.raises(ValidationError):
        artifact(relative_path=relative_path)


def test_generated_paths_are_portable_and_manifest_json_is_stable(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    stored = store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"png",
        media_type="image/png",
        producer="test",
        criterion_id="../../AC-001",
    )
    manifest = store.build_manifest()

    assert stored.relative_path == "artifacts/000001-screenshot.png"
    assert "\\" not in stored.relative_path
    assert render_manifest_json(manifest) == render_manifest_json(manifest)
    assert render_manifest_json(manifest).endswith("\n")


def test_binary_and_text_size_limits_are_enforced_at_boundary(tmp_path: Path) -> None:
    binary_store = EvidenceStore(
        tmp_path / "binary",
        limits=EvidenceLimits(max_artifact_size_bytes=4),
    )
    binary_store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"1234",
        media_type="image/png",
        producer="test",
    )
    with pytest.raises(EvidenceLimitError, match="single artifact size"):
        binary_store.record_bytes(
            kind=EvidenceKind.SCREENSHOT,
            data=b"12345",
            media_type="image/png",
            producer="test",
        )

    text_store = EvidenceStore(
        tmp_path / "text",
        limits=EvidenceLimits(max_text_artifact_size_bytes=4),
    )
    text_store.record_text(
        kind=EvidenceKind.BROWSER_OBSERVATION,
        text="1234",
        media_type="text/plain",
        producer="test",
    )
    with pytest.raises(EvidenceLimitError, match="text artifact size"):
        text_store.record_text(
            kind=EvidenceKind.BROWSER_OBSERVATION,
            text="12345",
            media_type="text/plain",
            producer="test",
        )


def test_run_and_criterion_artifact_counts_are_enforced(tmp_path: Path) -> None:
    run_store = EvidenceStore(
        tmp_path / "run",
        limits=EvidenceLimits(max_artifacts_per_run=1),
    )
    run_store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"1",
        media_type="image/png",
        producer="test",
    )
    with pytest.raises(EvidenceLimitError, match="per run"):
        run_store.record_bytes(
            kind=EvidenceKind.SCREENSHOT,
            data=b"2",
            media_type="image/png",
            producer="test",
        )

    criterion_store = EvidenceStore(
        tmp_path / "criterion",
        limits=EvidenceLimits(max_artifacts_per_criterion=1),
    )
    criterion_store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"1",
        media_type="image/png",
        producer="test",
        criterion_id="AC-001",
    )
    with pytest.raises(EvidenceLimitError, match="for criterion"):
        criterion_store.record_bytes(
            kind=EvidenceKind.SCREENSHOT,
            data=b"2",
            media_type="image/png",
            producer="test",
            criterion_id="AC-001",
        )


def test_integrity_checks_real_bytes_missing_corruption_size_and_digest(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)
    stored = store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"actual",
        media_type="image/png",
        producer="test",
    )
    path = store.verify_artifact(stored)
    assert path.read_bytes() == b"actual"

    bad_size = stored.model_copy(update={"size_bytes": stored.size_bytes + 1})
    with pytest.raises(EvidenceIntegrityError, match="size mismatch"):
        store.verify_artifact(bad_size)

    bad_digest = stored.model_copy(update={"digest": f"sha256:{'f' * 64}"})
    with pytest.raises(EvidenceIntegrityError, match="digest mismatch"):
        store.verify_artifact(bad_digest)

    path.write_bytes(b"change")
    with pytest.raises(EvidenceIntegrityError, match="digest mismatch"):
        store.verify_artifact(stored)

    path.unlink()
    with pytest.raises(EvidenceIntegrityError, match="missing"):
        store.verify_artifact(stored)


def test_integrity_rejects_unsafe_model_and_symlink_escape(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "run")
    unsafe = artifact().model_copy(update={"relative_path": "../outside.txt"})
    with pytest.raises(EvidenceIntegrityError):
        store.verify_artifact(unsafe)

    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = store.run_root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are not available on this host")
    escaping = artifact(
        relative_path="escape.txt",
        size_bytes=len(b"outside"),
        digest=f"sha256:{hashlib.sha256(b'outside').hexdigest()}",
    )
    with pytest.raises(EvidenceIntegrityError, match="escapes"):
        store.verify_artifact(escaping)


def test_finalized_artifact_is_not_overwritten(tmp_path: Path) -> None:
    first_store = EvidenceStore(tmp_path)
    first_store.record_bytes(
        kind=EvidenceKind.SCREENSHOT,
        data=b"first",
        media_type="image/png",
        producer="test",
    )
    second_store = EvidenceStore(tmp_path)

    with pytest.raises(EvidenceStorageError, match="refusing to overwrite"):
        second_store.record_bytes(
            kind=EvidenceKind.SCREENSHOT,
            data=b"second",
            media_type="image/png",
            producer="test",
        )
    assert (tmp_path / "artifacts/000001-screenshot.png").read_bytes() == b"first"


def test_text_redaction_and_process_log_storage(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    stored = store.record_process_log(
        "ordinary output\nAuthorization: Bearer super-secret\n"
        "password=hunter2 token=abc123 api_key=my-key"
    )
    content = (tmp_path / stored.relative_path).read_text(encoding="utf-8")

    assert "ordinary output" in content
    assert content.count("[REDACTED]") == 4
    assert "super-secret" not in content
    assert "hunter2" not in content
    assert "abc123" not in content
    assert "my-key" not in content
    assert stored.redacted is True
    store.verify_artifact(stored)
    assert store.build_manifest().artifacts == (stored,)


def test_sensitive_text_kinds_cannot_bypass_redacting_api(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)

    with pytest.raises(EvidenceStorageError, match="redacting"):
        store.record_bytes(
            kind=EvidenceKind.PROCESS_LOG,
            data=b"token=secret",
            media_type="text/plain",
            producer="test",
        )


def test_manifest_and_artifacts_remain_valid_after_run_directory_moves(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-run"
    store = EvidenceStore(original)
    stored = store.record_json(
        kind=EvidenceKind.BROWSER_OBSERVATION,
        payload={"verdict": "PASS"},
        producer="test",
        criterion_id="AC-001",
    )
    store.write_manifest()

    moved = tmp_path / "moved-run"
    shutil.move(original, moved)
    moved_store = EvidenceStore(moved)
    manifest = moved_store.load_manifest()
    moved_store.verify_manifest(manifest)

    assert manifest.artifacts[0].relative_path == stored.relative_path
    assert (moved / EVIDENCE_MANIFEST_FILENAME).is_file()
