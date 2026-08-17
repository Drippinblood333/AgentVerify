"""Narrow orchestration from browser outcomes to verified domain results."""

from __future__ import annotations

import os
import platform as platform_module
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from agentverify import __version__
from agentverify.application import (
    ApplicationCleanupError,
    ApplicationStartError,
    ManagedApplication,
    ProcessOutput,
    endpoint_accepts_connection,
)
from agentverify.browser import BrowserExecutionResult, BrowserVerifier
from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import Verdict, VerificationResult
from agentverify.evidence import (
    EvidenceError,
    EvidenceKind,
    EvidenceManifest,
    EvidenceStore,
    redact_sensitive_text,
)
from agentverify.inspection import (
    InspectionInputError,
    RunIntegrityError,
    inspect_run_directory,
    sha256_file,
)
from agentverify.plan import PlanError, load_plan, plan_digest
from agentverify.provenance import SourceProvenance, capture_source_provenance
from agentverify.receipt import (
    EnvironmentMetadataV2,
    ProofReceiptV2,
    build_receipt_v2,
    render_receipt_json,
    render_receipt_text,
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

    receipt: ProofReceiptV2
    run_root: Path
    receipt_json_path: Path
    receipt_text_path: Path
    evidence_manifest_path: Path
    plan_drift_warning: str | None


_UNSUPPORTED_EVIDENCE_REASON = (
    "Conclusive browser outcome could not be supported by trustworthy durable evidence"
)
_BASE_LIMITATIONS = (
    "Local application execution is not sandboxed and uses the current user's permissions.",
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
) -> LocalVerificationOutcome:
    """Run the complete local lifecycle, evidence, receipt, and integrity flow."""
    command = _validate_run_configuration(
        app_command=app_command,
        startup_timeout_ms=startup_timeout_ms,
    )
    verifier = BrowserVerifier(base_url)
    if endpoint_accepts_connection(base_url):
        raise RunConfigurationError(
            "configured application endpoint is already accepting connections; "
            "choose a different free port so AgentVerify can attribute readiness "
            "to the managed application"
        )
    source_provenance = capture_source_provenance()
    run_root = _prepare_run_directory(run_dir)
    store = EvidenceStore(run_root)
    limitations = list(_BASE_LIMITATIONS)
    _append_provenance_limitation(limitations, source_provenance)
    process: ManagedApplication | None = None
    executions: tuple[BrowserExecutionResult, ...] | None = None
    operational_reason: str | None = None
    lifecycle_reliable = False
    interrupted = False
    cleanup_failed = False
    diagnostic_refs: tuple[str, ...] = ()

    try:
        try:
            process = ManagedApplication.start(
                command,
                max_log_bytes=store.limits.max_text_artifact_size_bytes,
            )
        except ApplicationStartError:
            operational_reason = "Application executable could not start"
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
                limitations.append("The application output drain did not finalize reliably.")

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
                        producer="agentverify.application",
                    )
                except EvidenceError:
                    limitations.append("Process output evidence could not be persisted.")
                else:
                    diagnostic_refs = (process_artifact.relative_path,)

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
    receipt = build_receipt_v2(
        plan=plan,
        results=results,
        completed=completed,
        environment=EnvironmentMetadataV2(
            agentverify_version=__version__,
            python_version=platform_module.python_version(),
            platform=platform_module.platform(),
            playwright_version=_playwright_version(),
        ),
        source_provenance=source_provenance,
        evidence_manifest_digest=manifest_digest,
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
) -> tuple[str, ...]:
    if type(startup_timeout_ms) is not int or not 100 <= startup_timeout_ms <= 60_000:
        raise RunConfigurationError("startup timeout must be from 100 to 60000 milliseconds")
    command = tuple(app_command)
    if not command or not command[0].strip():
        raise RunConfigurationError("application command must not be empty")
    if any("\x00" in argument for argument in command):
        raise RunConfigurationError("application command arguments must not contain NUL bytes")
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
            prefix=".agentverify-preflight-",
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

    marker = "\n[agentverify: process output truncated before persistence]\n"
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
            prefix=".agentverify-receipt-",
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
