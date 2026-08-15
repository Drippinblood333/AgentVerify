"""Bounded, portable evidence artifacts and integrity verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from agentverify.domain import NonBlankText

EVIDENCE_MANIFEST_FILENAME = "evidence-manifest.json"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_ARTIFACT_ID_PATTERN = r"^EV-[0-9]{6}$"
_SENSITIVE_TEXT_KINDS: frozenset[EvidenceKind]


class EvidenceError(Exception):
    """Base class for expected evidence persistence and integrity failures."""


class EvidenceStorageError(EvidenceError):
    """Evidence could not be persisted or loaded safely."""


class EvidenceLimitError(EvidenceError):
    """An evidence artifact or collection would exceed configured limits."""


class EvidenceManifestError(EvidenceError):
    """A persisted evidence manifest is missing or invalid."""


class EvidenceIntegrityError(EvidenceError):
    """Persisted evidence no longer matches its manifest metadata."""


class EvidenceKind(StrEnum):
    """The deliberately small set of evidence kinds supported by M5."""

    BROWSER_OBSERVATION = "browser_observation"
    SCREENSHOT = "screenshot"
    PLAYWRIGHT_TRACE = "playwright_trace"
    CONSOLE_ERRORS = "console_errors"
    NETWORK_SUMMARY = "network_summary"
    PROCESS_LOG = "process_log"


_SENSITIVE_TEXT_KINDS = frozenset(
    {EvidenceKind.CONSOLE_ERRORS, EvidenceKind.PROCESS_LOG}
)


class EvidenceLimits(BaseModel):
    """Finite evidence collection limits for one caller-supplied run root."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_artifacts_per_run: Annotated[int, Field(ge=1)] = 128
    max_artifacts_per_criterion: Annotated[int, Field(ge=1)] = 8
    max_artifact_size_bytes: Annotated[int, Field(ge=1)] = 16 * 1024 * 1024
    max_text_artifact_size_bytes: Annotated[int, Field(ge=1)] = 256 * 1024
    max_console_entries: Annotated[int, Field(ge=1)] = 100
    max_network_entries: Annotated[int, Field(ge=1)] = 200


def _validate_relative_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("artifact relative path must be a nonblank string")
    if relative_path != relative_path.strip() or "\\" in relative_path:
        raise ValueError("artifact relative path must use portable POSIX separators")
    if relative_path.startswith("/") or PureWindowsPath(relative_path).drive:
        raise ValueError("artifact relative path must not be absolute")
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact relative path must not contain traversal components")
    if any(character.isspace() or ord(character) < 32 for character in relative_path):
        raise ValueError("artifact relative path must not contain whitespace or control characters")
    return relative_path


class EvidenceArtifact(BaseModel):
    """Immutable metadata describing bytes that were persisted by AgentVerify."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: Annotated[
        str,
        StringConstraints(strict=True, pattern=_ARTIFACT_ID_PATTERN),
    ]
    kind: EvidenceKind
    criterion_id: NonBlankText | None = None
    media_type: NonBlankText
    relative_path: str
    size_bytes: Annotated[int, Field(ge=0)]
    digest: Annotated[str, StringConstraints(strict=True, pattern=_DIGEST_PATTERN)]
    captured_at: datetime
    producer: NonBlankText
    redacted: bool

    @field_validator("relative_path")
    @classmethod
    def require_safe_relative_path(cls, relative_path: str) -> str:
        return _validate_relative_path(relative_path)

    @field_validator("captured_at")
    @classmethod
    def require_aware_utc_timestamp(cls, captured_at: datetime) -> datetime:
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return captured_at.astimezone(UTC)


class EvidenceManifest(BaseModel):
    """Immutable, portable index of evidence artifacts for one run root."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    artifacts: tuple[EvidenceArtifact, ...] = ()

    @model_validator(mode="after")
    def require_unique_artifacts(self) -> EvidenceManifest:
        artifact_ids: set[str] = set()
        relative_paths: set[str] = set()
        for artifact in self.artifacts:
            if artifact.artifact_id in artifact_ids:
                raise ValueError(f"duplicate artifact id: {artifact.artifact_id}")
            if artifact.relative_path in relative_paths:
                raise ValueError(f"duplicate artifact relative path: {artifact.relative_path}")
            artifact_ids.add(artifact.artifact_id)
            relative_paths.add(artifact.relative_path)
        return self


_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)(authorization\s*:\s*)(?:bearer\s+)?[^\s,;\"']+"
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?(?:password|passwd|token|api_key|apikey|secret)[\"']?\s*[=:]\s*)"
    r"([\"']?)[^\s&;,\"']+"
)


def redact_sensitive_text(text: str) -> tuple[str, bool]:
    """Best-effort redaction for a small set of common secret-bearing forms."""
    redacted = _AUTHORIZATION_PATTERN.sub(r"\1[REDACTED]", text)

    def replace_secret(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}[REDACTED]"

    redacted = _SECRET_VALUE_PATTERN.sub(replace_secret, redacted)
    return redacted, redacted != text


def render_manifest_json(manifest: EvidenceManifest) -> str:
    """Render stable manifest JSON with a final newline."""
    payload: dict[str, Any] = manifest.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


_ARTIFACT_FILENAMES: dict[EvidenceKind, str] = {
    EvidenceKind.BROWSER_OBSERVATION: "browser-observation.json",
    EvidenceKind.SCREENSHOT: "screenshot.png",
    EvidenceKind.PLAYWRIGHT_TRACE: "trace.zip",
    EvidenceKind.CONSOLE_ERRORS: "console-errors.json",
    EvidenceKind.NETWORK_SUMMARY: "network-summary.json",
    EvidenceKind.PROCESS_LOG: "process-log.txt",
}


class EvidenceStore:
    """Small local evidence store with bounded, atomic, non-overwriting writes."""

    def __init__(
        self,
        run_root: Path,
        *,
        limits: EvidenceLimits | None = None,
    ) -> None:
        self._limits = limits or EvidenceLimits()
        try:
            run_root.mkdir(parents=True, exist_ok=True)
            self._run_root = run_root.resolve(strict=True)
        except OSError as error:
            raise EvidenceStorageError(f"could not prepare evidence run root: {error}") from error
        if not self._run_root.is_dir():
            raise EvidenceStorageError("evidence run root must be a directory")
        self._artifacts: list[EvidenceArtifact] = []
        self._next_artifact_number = 1

    @property
    def run_root(self) -> Path:
        return self._run_root

    @property
    def limits(self) -> EvidenceLimits:
        return self._limits

    @property
    def artifacts(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(self._artifacts)

    def record_bytes(
        self,
        *,
        kind: EvidenceKind,
        data: bytes,
        media_type: str,
        producer: str,
        criterion_id: str | None = None,
        redacted: bool = False,
    ) -> EvidenceArtifact:
        """Persist bounded binary data; sensitive textual kinds require text APIs."""
        if kind in _SENSITIVE_TEXT_KINDS:
            raise EvidenceStorageError(
                f"{kind.value} evidence must use a redacting text or JSON API"
            )
        return self._persist_bytes(
            kind=kind,
            data=data,
            media_type=media_type,
            producer=producer,
            criterion_id=criterion_id,
            redacted=redacted,
            textual=False,
        )

    def record_text(
        self,
        *,
        kind: EvidenceKind,
        text: str,
        media_type: str,
        producer: str,
        criterion_id: str | None = None,
        redact: bool = False,
    ) -> EvidenceArtifact:
        """Persist bounded UTF-8 text with mandatory redaction for sensitive kinds."""
        should_redact = redact or kind in _SENSITIVE_TEXT_KINDS
        stored_text, transformed = (
            redact_sensitive_text(text) if should_redact else (text, False)
        )
        return self._persist_bytes(
            kind=kind,
            data=stored_text.encode("utf-8"),
            media_type=media_type,
            producer=producer,
            criterion_id=criterion_id,
            redacted=transformed,
            textual=True,
        )

    def record_json(
        self,
        *,
        kind: EvidenceKind,
        payload: object,
        producer: str,
        criterion_id: str | None = None,
        redact: bool = False,
    ) -> EvidenceArtifact:
        """Persist canonical bounded JSON, redacting sensitive textual kinds."""
        try:
            text = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError) as error:
            raise EvidenceStorageError(f"evidence JSON is not serializable: {error}") from error
        return self.record_text(
            kind=kind,
            text=text,
            media_type="application/json",
            producer=producer,
            criterion_id=criterion_id,
            redact=redact,
        )

    def record_process_log(
        self,
        text: str,
        *,
        criterion_id: str | None = None,
        producer: str = "agentverify.process",
    ) -> EvidenceArtifact:
        """Store already-observed process text without managing the process itself."""
        return self.record_text(
            kind=EvidenceKind.PROCESS_LOG,
            text=text,
            media_type="text/plain; charset=utf-8",
            producer=producer,
            criterion_id=criterion_id,
        )

    def adopt_file(
        self,
        *,
        kind: EvidenceKind,
        source: Path,
        media_type: str,
        producer: str,
        criterion_id: str | None = None,
    ) -> EvidenceArtifact:
        """Copy a bounded finalized file into the evidence store."""
        if kind in _SENSITIVE_TEXT_KINDS:
            raise EvidenceStorageError(
                f"{kind.value} evidence must use a redacting text or JSON API"
            )
        try:
            resolved_source = source.resolve(strict=True)
            if not resolved_source.is_file():
                raise EvidenceStorageError("adopted evidence source must be a regular file")
            source_size = resolved_source.stat().st_size
            if source_size > self._limits.max_artifact_size_bytes:
                raise EvidenceLimitError(
                    "artifact exceeds maximum single artifact size: "
                    f"{source_size} > {self._limits.max_artifact_size_bytes}"
                )
            data = resolved_source.read_bytes()
        except EvidenceError:
            raise
        except OSError as error:
            raise EvidenceStorageError(f"could not read adopted evidence file: {error}") from error
        return self._persist_bytes(
            kind=kind,
            data=data,
            media_type=media_type,
            producer=producer,
            criterion_id=criterion_id,
            redacted=False,
            textual=False,
        )

    def build_manifest(self) -> EvidenceManifest:
        return EvidenceManifest(artifacts=tuple(self._artifacts))

    def write_manifest(self, manifest: EvidenceManifest | None = None) -> Path:
        """Atomically write one immutable evidence manifest without overwriting."""
        selected_manifest = manifest or self.build_manifest()
        destination = self._run_root / EVIDENCE_MANIFEST_FILENAME
        self._atomic_create(destination, render_manifest_json(selected_manifest).encode("utf-8"))
        return destination

    def load_manifest(self) -> EvidenceManifest:
        """Load a strict manifest from this run root."""
        try:
            manifest_path = self._resolve_existing_path(EVIDENCE_MANIFEST_FILENAME)
        except EvidenceIntegrityError as error:
            raise EvidenceManifestError("evidence manifest is missing or unsafe") from error
        try:
            content = manifest_path.read_text(encoding="utf-8")
            return EvidenceManifest.model_validate_json(content)
        except UnicodeDecodeError as error:
            raise EvidenceManifestError("evidence manifest is not valid UTF-8") from error
        except ValidationError as error:
            raise EvidenceManifestError(f"evidence manifest is invalid: {error}") from error
        except OSError as error:
            raise EvidenceManifestError(f"could not read evidence manifest: {error}") from error

    def verify_artifact(self, artifact: EvidenceArtifact) -> Path:
        """Verify path containment, file type, byte size, and SHA-256 digest."""
        artifact_path = self._resolve_existing_path(artifact.relative_path)
        try:
            size_bytes, digest = self._measure_file(artifact_path)
        except OSError as error:
            raise EvidenceIntegrityError(
                f"could not inspect evidence artifact: {artifact.relative_path}"
            ) from error
        if size_bytes != artifact.size_bytes:
            raise EvidenceIntegrityError(
                f"evidence artifact size mismatch: {artifact.relative_path}"
            )
        if digest != artifact.digest:
            raise EvidenceIntegrityError(
                f"evidence artifact digest mismatch: {artifact.relative_path}"
            )
        return artifact_path

    def verify_manifest(self, manifest: EvidenceManifest) -> None:
        """Verify every artifact, failing the complete operation on the first defect."""
        for artifact in manifest.artifacts:
            self.verify_artifact(artifact)

    def _persist_bytes(
        self,
        *,
        kind: EvidenceKind,
        data: bytes,
        media_type: str,
        producer: str,
        criterion_id: str | None,
        redacted: bool,
        textual: bool,
    ) -> EvidenceArtifact:
        self._check_limits(criterion_id=criterion_id, size_bytes=len(data), textual=textual)
        artifact_number = self._next_artifact_number
        relative_path = f"artifacts/{artifact_number:06d}-{_ARTIFACT_FILENAMES[kind]}"
        destination = self._safe_destination(relative_path)
        self._atomic_create(destination, data)
        try:
            size_bytes, digest = self._measure_file(destination)
        except OSError as error:
            raise EvidenceStorageError("could not inspect finalized evidence bytes") from error
        artifact = EvidenceArtifact(
            artifact_id=f"EV-{artifact_number:06d}",
            kind=kind,
            criterion_id=criterion_id,
            media_type=media_type,
            relative_path=relative_path,
            size_bytes=size_bytes,
            digest=digest,
            captured_at=datetime.now(UTC),
            producer=producer,
            redacted=redacted,
        )
        self.verify_artifact(artifact)
        self._artifacts.append(artifact)
        self._next_artifact_number += 1
        return artifact

    def _check_limits(
        self,
        *,
        criterion_id: str | None,
        size_bytes: int,
        textual: bool,
    ) -> None:
        if len(self._artifacts) >= self._limits.max_artifacts_per_run:
            raise EvidenceLimitError("maximum artifacts per run exceeded")
        if criterion_id is not None:
            criterion_count = sum(
                artifact.criterion_id == criterion_id for artifact in self._artifacts
            )
            if criterion_count >= self._limits.max_artifacts_per_criterion:
                raise EvidenceLimitError(
                    f"maximum artifacts for criterion exceeded: {criterion_id}"
                )
        if size_bytes > self._limits.max_artifact_size_bytes:
            raise EvidenceLimitError(
                "artifact exceeds maximum single artifact size: "
                f"{size_bytes} > {self._limits.max_artifact_size_bytes}"
            )
        if textual and size_bytes > self._limits.max_text_artifact_size_bytes:
            raise EvidenceLimitError(
                "text artifact exceeds maximum text artifact size: "
                f"{size_bytes} > {self._limits.max_text_artifact_size_bytes}"
            )

    def _safe_destination(self, relative_path: str) -> Path:
        try:
            validated_path = _validate_relative_path(relative_path)
        except ValueError as error:
            raise EvidenceStorageError(str(error)) from error
        destination = self._run_root.joinpath(*validated_path.split("/"))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = destination.parent.resolve(strict=True)
        except OSError as error:
            raise EvidenceStorageError(f"could not prepare artifact directory: {error}") from error
        if not resolved_parent.is_relative_to(self._run_root):
            raise EvidenceStorageError("artifact destination escapes the evidence run root")
        return destination

    def _resolve_existing_path(self, relative_path: str) -> Path:
        try:
            validated_path = _validate_relative_path(relative_path)
        except ValueError as error:
            raise EvidenceIntegrityError(str(error)) from error
        candidate = self._run_root.joinpath(*validated_path.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise EvidenceIntegrityError(
                f"evidence artifact is missing: {relative_path}"
            ) from error
        except OSError as error:
            raise EvidenceIntegrityError(
                f"could not resolve evidence artifact: {relative_path}"
            ) from error
        if not resolved.is_relative_to(self._run_root):
            raise EvidenceIntegrityError("evidence artifact escapes the run root")
        if not resolved.is_file():
            raise EvidenceIntegrityError(
                f"evidence artifact is not a regular file: {relative_path}"
            )
        return resolved

    @staticmethod
    def _atomic_create(destination: Path, data: bytes) -> None:
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".agentverify-evidence-",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.link(temporary_path, destination)
        except FileExistsError as error:
            raise EvidenceStorageError(
                f"refusing to overwrite finalized evidence: {destination.name}"
            ) from error
        except OSError as error:
            raise EvidenceStorageError(
                f"could not finalize evidence atomically: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    raise EvidenceStorageError(
                        f"could not remove temporary evidence file: {error}"
                    ) from error

    @staticmethod
    def _measure_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as artifact_file:
            while chunk := artifact_file.read(1024 * 1024):
                size_bytes += len(chunk)
                digest.update(chunk)
        return size_bytes, f"sha256:{digest.hexdigest()}"
