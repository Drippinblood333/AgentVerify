"""Narrow orchestration from browser outcomes to verified domain results."""

from __future__ import annotations

import os
import platform as platform_module
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from donewitness import __version__
from donewitness.application import (
    ApplicationCleanupError,
    ApplicationStartError,
    ManagedApplication,
    ProcessOutput,
    endpoint_accepts_connection,
)
from donewitness.browser import BrowserExecutionResult, BrowserVerifier
from donewitness.browser_plan import BrowserVerificationPlan
from donewitness.domain import Verdict, VerificationResult
from donewitness.evidence import (
    EvidenceError,
    EvidenceKind,
    EvidenceManifest,
    EvidenceStore,
    redact_sensitive_text,
)
from donewitness.inspection import (
    InspectionInputError,
    RunIntegrityError,
    inspect_run_directory,
    sha256_file,
)
from donewitness.isolation import (
    DOCKER_PROFILE_NAME,
    DockerIsolationConfigurationError,
    DockerIsolationPreflight,
    DockerManagedApplication,
    preflight_docker_isolation,
)
from donewitness.plan import PlanError, load_plan, plan_digest
from donewitness.provenance import SourceProvenance, capture_source_provenance
from donewitness.receipt import (
    CurrentWorktreeSourceSelection,
    DirectExecutionMetadata,
    DockerExecutionMetadata,
    EnvironmentMetadataV2,
    ExternalPlanSource,
    GitWorktreeSourceSelection,
    PlanSource,
    ProofReceiptV4,
    RepositoryPlanSource,
    SourceSelection,
    build_receipt_v4,
    render_receipt_json,
    render_receipt_text,
)
from donewitness.worktree import (
    GitRevisionConfigurationError,
    GitWorktreeOperationalError,
    ManagedGitWorktree,
    ResolvedRevision,
    discover_repository_root,
    resolve_revision,
)


class ResultCoverageError(ValueError):
    """Browser execution results do not exactly cover their frozen plan."""


class RunConfigurationError(ValueError):
    """The supplied M6 run configuration is invalid before process startup."""


class RunOperationalError(Exception):
    """An expected operational failure prevented review output finalization."""


@dataclass(frozen=True, slots=True)
class LocalVerificationOutcome:
    """Completed local orchestration outputs and their authoritative receipt."""

    receipt: ProofReceiptV4
    run_root: Path
    receipt_json_path: Path
    receipt_text_path: Path
    evidence_manifest_path: Path
    plan_drift_warning: str | None


_UNSUPPORTED_EVIDENCE_REASON = (
    "Conclusive browser outcome could not be supported by trustworthy durable evidence"
)
_DIRECT_LIMITATIONS = (
    "Local application execution is not sandboxed and uses the current user's permissions.",
    "Textual redaction is best-effort; rich browser artifacts may contain sensitive data.",
)
_DOCKER_LIMITATIONS = (
    "The optional Docker isolation baseline reduces host exposure; Docker Engine, its "
    "Linux VM/kernel/runtime, and the host remain trusted infrastructure.",
    "The internal Docker bridge is intended to remove normal external connectivity but may "
    "still permit Docker-managed host or gateway communication depending on the runtime.",
    "When Docker does not activate a requested localhost mapping for an internal-only bridge, "
    "DoneWitness uses a bounded host-loopback TCP relay to the exact managed container port.",
    "The Docker isolation baseline provides no image signature, registry authenticity, "
    "attestation, remote execution, or universal host-network separation guarantee.",
    "Textual redaction is best-effort; rich browser artifacts may contain sensitive data.",
)
_PROCESS_LOG_TRUNCATION = "Process output was truncated to the configured evidence limit."
_INTERRUPTED_REASON = "Verification was interrupted"


def build_browser_verification_results(
    *,
    plan: BrowserVerificationPlan,
    executions: tuple[BrowserExecutionResult, ...],
    manifest: EvidenceManifest,
    evidence_root: Path,
) -> tuple[VerificationResult, ...]:
    """Validate coverage and evidence before constructing domain results."""
    criterion_ids = {criterion.id for criterion in plan.criteria}
    by_criterion: dict[str, BrowserExecutionResult] = {}
    for execution in executions:
        if execution.criterion_id not in criterion_ids:
            raise ResultCoverageError(
                f"result references unknown criterion: {execution.criterion_id}"
            )
        if execution.criterion_id in by_criterion:
            raise ResultCoverageError(
                f"duplicate result for criterion: {execution.criterion_id}"
            )
        by_criterion[execution.criterion_id] = execution

    missing = [
        criterion.id for criterion in plan.criteria if criterion.id not in by_criterion
    ]
    if missing:
        raise ResultCoverageError(f"missing result for criterion: {missing[0]}")

    artifacts_by_path = {
        artifact.relative_path: artifact for artifact in manifest.artifacts
    }
    store = EvidenceStore(evidence_root)
    results: list[VerificationResult] = []
    for criterion in plan.criteria:
        execution = by_criterion[criterion.id]
        validated_refs: list[str] = []
        evidence_is_trustworthy = bool(execution.evidence_refs)
        has_valid_browser_observation = False
        for evidence_ref in execution.evidence_refs:
            artifact = artifacts_by_path.get(evidence_ref)
            if artifact is None or artifact.criterion_id != criterion.id:
                evidence_is_trustworthy = False
                continue
            try:
                store.verify_artifact(artifact)
            except EvidenceError:
                evidence_is_trustworthy = False
                continue
            validated_refs.append(evidence_ref)
            if artifact.kind is EvidenceKind.BROWSER_OBSERVATION:
                has_valid_browser_observation = True

        verdict = execution.verdict
        reason = execution.reason
        conclusive_evidence_is_supported = (
            evidence_is_trustworthy and has_valid_browser_observation
        )
        if (
            verdict in {Verdict.PASS, Verdict.FAIL}
            and not conclusive_evidence_is_supported
        ):
            verdict = Verdict.UNKNOWN
            reason = _UNSUPPORTED_EVIDENCE_REASON

        results.append(
            VerificationResult(
                criterion_id=criterion.id,
                verdict=verdict,
                reason=reason,
                evidence_refs=tuple(validated_refs),
            )
        )
    return tuple(results)


def build_operational_unknown_results(
    *,
    plan: BrowserVerificationPlan,
    reason: str,
    evidence_refs: Sequence[str] = (),
) -> tuple[VerificationResult, ...]:
    """Build ordered UNKNOWN results when browser execution never occurred."""
    refs = tuple(evidence_refs)
    return tuple(
        VerificationResult(
            criterion_id=criterion.id,
            verdict=Verdict.UNKNOWN,
            reason=reason,
            evidence_refs=refs,
        )
        for criterion in plan.criteria
    )


def verify_local_application(
    *,
    plan: BrowserVerificationPlan,
    base_url: str,
    run_dir: Path,
    app_command: Sequence[str],
    startup_timeout_ms: int = 10_000,
    plan_source_path: Path | None = None,
    isolation_mode: Literal["none", "docker"] = "none",
    isolation_image: str | None = None,
    invocation_root: Path | None = None,
    requested_revision: str | None = None,
) -> LocalVerificationOutcome:
    """Run the complete local lifecycle, evidence, receipt, and integrity flow."""
    caller_root = (invocation_root or Path.cwd()).resolve()
    command = _validate_run_configuration(
        app_command=app_command,
        startup_timeout_ms=startup_timeout_ms,
        isolation_mode=isolation_mode,
        isolation_image=isolation_image,
    )
    verifier = BrowserVerifier(base_url)
    resolved_revision: ResolvedRevision | None = None
    managed_worktree: ManagedGitWorktree | None = None
    if requested_revision is not None:
        try:
            resolved_revision = resolve_revision(caller_root, requested_revision)
        except GitRevisionConfigurationError as error:
            raise RunConfigurationError(str(error)) from error
        try:
            managed_worktree = ManagedGitWorktree.create(resolved_revision)
        except (GitRevisionConfigurationError, GitWorktreeOperationalError) as error:
            raise RunOperationalError(str(error)) from error
    execution_root = (
        managed_worktree.source_root if managed_worktree is not None else caller_root
    )
    source_provenance = (
        SourceProvenance(
            kind="git",
            revision=resolved_revision.resolved_revision,
            dirty_worktree=False,
            git_version=resolved_revision.git_version,
        )
        if resolved_revision is not None
        else capture_source_provenance(execution_root)
    )
    plan_source = _build_plan_source(
        plan_source_path=plan_source_path,
        invocation_root=caller_root,
        source_provenance=source_provenance,
        resolved_revision=resolved_revision,
    )
    docker_preflight: DockerIsolationPreflight | None = None
    try:
        if isolation_mode == "docker":
            docker_preflight = preflight_docker_isolation(
                image_reference=isolation_image or "",
                base_url=base_url,
                run_dir=run_dir,
                source_root=execution_root,
            )
        if endpoint_accepts_connection(base_url):
            raise RunConfigurationError(
                "configured application endpoint is already accepting connections; "
                "choose a different free port so DoneWitness can attribute readiness "
                "to the managed application"
            )
        if managed_worktree is not None and run_dir.is_relative_to(execution_root):
            raise RunConfigurationError(
                "run directory must be outside the disposable source worktree"
            )
        run_root = _prepare_run_directory(run_dir)
    except DockerIsolationConfigurationError as error:
        if managed_worktree is not None and not managed_worktree.cleanup():
            raise RunOperationalError(
                "Docker preflight failed and disposable worktree cleanup was not confirmed"
            ) from error
        raise RunConfigurationError(str(error)) from error
    except BaseException as error:
        if managed_worktree is not None and not managed_worktree.cleanup():
            raise RunOperationalError(
                "preflight failed and disposable worktree cleanup was not confirmed"
            ) from error
        raise
    store = EvidenceStore(run_root)
    limitations = list(
        _DOCKER_LIMITATIONS if isolation_mode == "docker" else _DIRECT_LIMITATIONS
    )
    _append_provenance_limitation(limitations, source_provenance)
    process: ManagedApplication | DockerManagedApplication | None = None
    executions: tuple[BrowserExecutionResult, ...] | None = None
    operational_reason: str | None = None
    lifecycle_reliable = False
    interrupted = False
    cleanup_failed = False
    post_run_source_state: Literal["clean", "dirty", "unknown"] = "clean"
    worktree_cleanup_confirmed = True
    diagnostic_refs: tuple[str, ...] = ()

    try:
        try:
            if docker_preflight is None:
                process = ManagedApplication.start(
                    command,
                    max_log_bytes=store.limits.max_text_artifact_size_bytes,
                    cwd=execution_root,
                )
            else:
                process = DockerManagedApplication.start(
                    docker_preflight,
                    command,
                    max_log_bytes=store.limits.max_text_artifact_size_bytes,
                )
        except ApplicationStartError as error:
            operational_reason = (
                "Application executable could not start"
                if docker_preflight is None
                else str(error)
            )
        else:
            try:
                readiness = process.wait_for_readiness(
                    base_url,
                    timeout_ms=startup_timeout_ms,
                )
                if not readiness.ready:
                    operational_reason = readiness.reason
                else:
                    executions = verifier.verify_with_evidence(
                        plan,
                        evidence_store=store,
                    )
                    if process.note_unexpected_exit():
                        operational_reason = "Application exited unexpectedly during verification"
                        limitations.append(
                            "The application exited unexpectedly before managed shutdown."
                        )
                    else:
                        lifecycle_reliable = True
            except KeyboardInterrupt:
                interrupted = True
                operational_reason = _INTERRUPTED_REASON
                limitations.append("Verification was interrupted before reliable completion.")
        finally:
            if process is not None:
                try:
                    shutdown = process.stop()
                except ApplicationCleanupError:
                    cleanup_failed = True
                    lifecycle_reliable = False
                    limitations.append("Managed application cleanup could not be confirmed.")
                else:
                    if shutdown.force_killed:
                        limitations.append(
                            "The application required force termination after the "
                            "shutdown grace period."
                        )

                process_output: ProcessOutput | None
                try:
                    process_output = process.output()
                except ApplicationCleanupError:
                    process_output = None
                    cleanup_failed = True
                    lifecycle_reliable = False
                    limitations.append(
                        "The application output drain did not finalize reliably."
                    )

                if process_output is not None and process_output.text:
                    process_log, fit_truncated = _fit_process_log_text(
                        process_output.text,
                        max_bytes=store.limits.max_text_artifact_size_bytes,
                    )
                    if process_output.truncated or fit_truncated:
                        limitations.append(_PROCESS_LOG_TRUNCATION)
                    try:
                        process_artifact = store.record_process_log(
                            process_log,
                            producer="donewitness.application",
                        )
                    except EvidenceError:
                        limitations.append("Process output evidence could not be persisted.")
                    else:
                        diagnostic_refs = (process_artifact.relative_path,)
    finally:
        if managed_worktree is not None:
            try:
                post_run_source_state = (
                    "dirty" if managed_worktree.is_dirty() else "clean"
                )
            except GitWorktreeOperationalError:
                post_run_source_state = "unknown"
                limitations.append(
                    "Post-run disposable source state could not be inspected reliably."
                )
            if post_run_source_state != "clean":
                cleanup_failed = True
                lifecycle_reliable = False
            if post_run_source_state == "dirty":
                limitations.append(
                    "The application modified the disposable source worktree during verification."
                )
            worktree_cleanup_confirmed = managed_worktree.cleanup()
            if not worktree_cleanup_confirmed:
                cleanup_failed = True
                lifecycle_reliable = False
                limitations.append("Disposable Git worktree cleanup could not be confirmed.")

    try:
        manifest = store.build_manifest()
        store.verify_manifest(manifest)
        manifest_path = store.write_manifest(manifest)
        persisted_manifest = store.load_manifest()
        store.verify_manifest(persisted_manifest)
        manifest_digest = sha256_file(manifest_path)
    except (EvidenceError, OSError) as error:
        raise RunOperationalError("durable evidence could not be finalized") from error

    if executions is None:
        results = build_operational_unknown_results(
            plan=plan,
            reason=operational_reason or "Verification did not reach browser execution",
            evidence_refs=diagnostic_refs,
        )
    else:
        results = build_browser_verification_results(
            plan=plan,
            executions=executions,
            manifest=persisted_manifest,
            evidence_root=run_root,
        )

    completed = (
        executions is not None
        and lifecycle_reliable
        and not interrupted
        and not cleanup_failed
        and all(result.verdict is not Verdict.UNKNOWN for result in results)
    )
    execution = (
        DirectExecutionMetadata()
        if docker_preflight is None
        else DockerExecutionMetadata(
            isolation_profile=DOCKER_PROFILE_NAME,
            docker_server_version=docker_preflight.docker_server_version,
            image_reference=docker_preflight.image_reference,
            image_id=docker_preflight.image_id,
        )
    )
    source_selection: SourceSelection = (
        CurrentWorktreeSourceSelection()
        if resolved_revision is None
        else GitWorktreeSourceSelection(
            requested_revision=resolved_revision.requested_revision,
            resolved_revision=resolved_revision.resolved_revision,
            caller_head_revision=resolved_revision.caller_head_revision,
            caller_dirty_worktree=resolved_revision.caller_dirty_worktree,
            post_run_source_state=post_run_source_state,
            cleanup_confirmed=worktree_cleanup_confirmed,
        )
    )
    receipt = build_receipt_v4(
        plan=plan,
        results=results,
        completed=completed,
        environment=EnvironmentMetadataV2(
            donewitness_version=__version__,
            python_version=platform_module.python_version(),
            platform=platform_module.platform(),
            playwright_version=_playwright_version(),
        ),
        source_provenance=source_provenance,
        evidence_manifest_digest=manifest_digest,
        execution=execution,
        source_selection=source_selection,
        plan_source=plan_source,
        limitations=tuple(dict.fromkeys(limitations)),
    )
    receipt_json_path = run_root / "receipt.json"
    receipt_text_path = run_root / "receipt.txt"
    try:
        _atomic_create_text(receipt_json_path, render_receipt_json(receipt))
        _atomic_create_text(receipt_text_path, render_receipt_text(receipt))
    except OSError as error:
        raise RunOperationalError("proof receipt could not be finalized") from error

    try:
        inspect_run_directory(run_root)
    except (InspectionInputError, RunIntegrityError) as error:
        raise RunIntegrityError(
            f"new run failed final integrity self-check: {error}"
        ) from error

    plan_drift_warning = (
        detect_plan_drift(plan_source_path, expected_digest=receipt.plan_digest)
        if plan_source_path is not None
        else None
    )

    return LocalVerificationOutcome(
        receipt=receipt,
        run_root=run_root,
        receipt_json_path=receipt_json_path,
        receipt_text_path=receipt_text_path,
        evidence_manifest_path=manifest_path,
        plan_drift_warning=plan_drift_warning,
    )


def _build_plan_source(
    *,
    plan_source_path: Path | None,
    invocation_root: Path,
    source_provenance: SourceProvenance,
    resolved_revision: ResolvedRevision | None,
) -> PlanSource:
    if plan_source_path is None:
        return ExternalPlanSource()
    repository_root = (
        resolved_revision.repository_root
        if resolved_revision is not None
        else discover_repository_root(invocation_root)
    )
    if repository_root is None:
        return ExternalPlanSource()
    try:
        relative_path = plan_source_path.resolve().relative_to(repository_root).as_posix()
    except (OSError, ValueError):
        return ExternalPlanSource()
    caller_revision: str | None
    caller_dirty: bool | None
    if resolved_revision is not None:
        caller_revision = resolved_revision.caller_head_revision
        caller_dirty = resolved_revision.caller_dirty_worktree
    elif source_provenance.kind == "git":
        caller_revision = source_provenance.revision
        caller_dirty = source_provenance.dirty_worktree
    else:
        return ExternalPlanSource()
    if caller_revision is None or caller_dirty is None:
        return ExternalPlanSource()
    return RepositoryPlanSource(
        repository_relative_path=relative_path,
        caller_source_revision=caller_revision,
        caller_dirty_worktree=caller_dirty,
    )


def detect_plan_drift(plan_path: Path, *, expected_digest: str) -> str | None:
    """Compare a current plan source to the frozen canonical snapshot."""
    try:
        current_plan = load_plan(plan_path)
    except PlanError:
        return (
            "warning: current plan source no longer validates against the verified "
            f"snapshot; receipt applies to {expected_digest}"
        )
    if plan_digest(current_plan) != expected_digest:
        return (
            "warning: plan file changed after verification snapshot; "
            f"receipt applies to {expected_digest}"
        )
    return None


def _append_provenance_limitation(
    limitations: list[str],
    provenance: SourceProvenance,
) -> None:
    if provenance.kind == "unavailable":
        limitations.append(f"Source provenance unavailable: {provenance.reason}.")
    elif provenance.dirty_worktree:
        limitations.append(
            "The Git worktree was dirty, so the recorded HEAD revision does not uniquely "
            "identify the verified source bytes."
        )


def _playwright_version() -> str:
    try:
        return version("playwright")
    except PackageNotFoundError:
        return "unavailable"


def _validate_run_configuration(
    *,
    app_command: Sequence[str],
    startup_timeout_ms: int,
    isolation_mode: Literal["none", "docker"],
    isolation_image: str | None,
) -> tuple[str, ...]:
    if type(startup_timeout_ms) is not int or not 100 <= startup_timeout_ms <= 60_000:
        raise RunConfigurationError("startup timeout must be from 100 to 60000 milliseconds")
    command = tuple(app_command)
    if not command or not command[0].strip():
        raise RunConfigurationError("application command must not be empty")
    if any("\x00" in argument for argument in command):
        raise RunConfigurationError("application command arguments must not contain NUL bytes")
    if isolation_mode not in {"none", "docker"}:
        raise RunConfigurationError("isolation mode must be none or docker")
    if isolation_mode == "docker" and isolation_image is None:
        raise RunConfigurationError("Docker isolation requires --isolation-image")
    if isolation_mode == "none" and isolation_image is not None:
        raise RunConfigurationError(
            "--isolation-image is only valid with --isolation docker"
        )
    return command


def _prepare_run_directory(run_dir: Path) -> Path:
    probe_path: Path | None = None
    try:
        expanded = run_dir.expanduser()
        if expanded.exists() and not expanded.is_dir():
            raise RunConfigurationError("run directory path must be a directory")
        expanded.mkdir(parents=True, exist_ok=True)
        resolved = expanded.resolve(strict=True)
        if any(resolved.iterdir()):
            raise RunConfigurationError("run directory must be empty")
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".donewitness-preflight-",
            dir=resolved,
        )
        probe_path = Path(probe_name)
        with os.fdopen(descriptor, "wb"):
            pass
        probe_path.unlink()
        probe_path = None
    except RunConfigurationError:
        raise
    except OSError as error:
        raise RunConfigurationError("run directory could not be prepared") from error
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
    return resolved


def _fit_process_log_text(text: str, *, max_bytes: int) -> tuple[str, bool]:
    redacted, _ = redact_sensitive_text(text)
    if len(redacted.encode("utf-8")) <= max_bytes:
        return text, False

    marker = "\n[donewitness: process output truncated before persistence]\n"
    retained_characters = len(text)
    while retained_characters > 0:
        retained_characters //= 2
        candidate = f"{text[:retained_characters]}{marker}"
        redacted_candidate, _ = redact_sensitive_text(candidate)
        if len(redacted_candidate.encode("utf-8")) <= max_bytes:
            return candidate, True
    return marker, True


def _atomic_create_text(destination: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".donewitness-receipt-",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content.encode("utf-8"))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.link(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
