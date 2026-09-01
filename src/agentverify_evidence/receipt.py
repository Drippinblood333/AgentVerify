"""Pure construction and deterministic rendering of proof receipts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from agentverify_evidence.domain import (
    NonBlankText,
    Verdict,
    VerificationResult,
    aggregate_verdict,
)
from agentverify_evidence.plan import SupportedVerificationPlan, plan_digest
from agentverify_evidence.provenance import SourceProvenance

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class ReceiptValidationError(ValueError):
    """Supplied fixture results cannot form an auditable proof receipt."""


class ReceiptLoadError(ValueError):
    """A persisted proof receipt cannot be loaded as a supported schema."""


class _ReceiptPlanCriterion(Protocol):
    id: str
    description: str


class EnvironmentMetadata(BaseModel):
    """Explicit runtime metadata supplied by an outer orchestration layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agentverify_version: NonBlankText
    python_version: NonBlankText
    platform: NonBlankText


class EnvironmentMetadataV2(EnvironmentMetadata):
    """Receipt-v2 runtime metadata, including the Playwright package version."""

    playwright_version: NonBlankText


class ReceiptCriterionResult(BaseModel):
    """An immutable criterion snapshot included in a proof receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    criterion_id: NonBlankText
    description: NonBlankText
    verdict: Verdict
    reason: NonBlankText
    evidence_refs: tuple[NonBlankText, ...]

    @model_validator(mode="after")
    def require_evidence_for_conclusive_verdicts(self) -> ReceiptCriterionResult:
        """Require opaque evidence references for conclusive receipt entries."""
        if self.verdict in {Verdict.PASS, Verdict.FAIL} and not self.evidence_refs:
            raise ValueError("PASS and FAIL results require at least one evidence reference")
        return self


class ProofReceipt(BaseModel):
    """An immutable, fixture-derived record suitable for human review."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    task: NonBlankText
    plan_digest: NonBlankText
    overall_verdict: Verdict
    completed: bool
    criteria: Annotated[tuple[ReceiptCriterionResult, ...], Field(min_length=1)]
    environment: EnvironmentMetadata
    limitations: Annotated[tuple[NonBlankText, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[ReceiptCriterionResult, ...],
    ) -> tuple[ReceiptCriterionResult, ...]:
        """Reject receipts whose criterion snapshots are ambiguous."""
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.criterion_id in seen:
                raise ValueError(
                    f"receipt criterion id must be unique: {criterion.criterion_id}"
                )
            seen.add(criterion.criterion_id)
        return criteria

    @model_validator(mode="after")
    def require_consistent_overall_verdict(self) -> ProofReceipt:
        """Reject receipts whose stated verdict contradicts their own snapshots."""
        expected = aggregate_receipt_verdict(
            [criterion.verdict for criterion in self.criteria],
            completed=self.completed,
        )
        if self.overall_verdict is not expected:
            raise ValueError(
                "overall verdict must match criterion verdicts and completion state: "
                f"expected {expected.value}, got {self.overall_verdict.value}"
            )
        return self


ProofReceiptV1 = ProofReceipt


class ProofReceiptV2(BaseModel):
    """M7 receipt binding a run to source metadata and persisted evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    task: NonBlankText
    plan_digest: NonBlankText
    overall_verdict: Verdict
    completed: bool
    criteria: Annotated[tuple[ReceiptCriterionResult, ...], Field(min_length=1)]
    environment: EnvironmentMetadataV2
    source_provenance: SourceProvenance
    evidence_manifest_digest: Annotated[
        str,
        StringConstraints(strict=True, pattern=_DIGEST_PATTERN),
    ]
    limitations: Annotated[tuple[NonBlankText, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[ReceiptCriterionResult, ...],
    ) -> tuple[ReceiptCriterionResult, ...]:
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.criterion_id in seen:
                raise ValueError(
                    f"receipt criterion id must be unique: {criterion.criterion_id}"
                )
            seen.add(criterion.criterion_id)
        return criteria

    @model_validator(mode="after")
    def require_consistent_overall_verdict(self) -> ProofReceiptV2:
        expected = aggregate_receipt_verdict(
            [criterion.verdict for criterion in self.criteria],
            completed=self.completed,
        )
        if self.overall_verdict is not expected:
            raise ValueError(
                "overall verdict must match criterion verdicts and completion state: "
                f"expected {expected.value}, got {self.overall_verdict.value}"
            )
        return self


class DirectExecutionMetadata(BaseModel):
    """Receipt-v3 metadata for the unchanged direct execution route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    isolation_mode: Literal["none"] = "none"


class DockerExecutionMetadata(BaseModel):
    """Receipt-v3 metadata for the concrete local Docker image used."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    isolation_mode: Literal["docker"] = "docker"
    isolation_profile: NonBlankText
    docker_server_version: NonBlankText
    image_reference: NonBlankText
    image_id: Annotated[
        str,
        StringConstraints(strict=True, pattern=_DIGEST_PATTERN),
    ]


type ExecutionMetadata = Annotated[
    DirectExecutionMetadata | DockerExecutionMetadata,
    Field(discriminator="isolation_mode"),
]


class CurrentWorktreeSourceSelection(BaseModel):
    """Receipt-v4 marker for the unchanged caller-worktree route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["current_worktree"] = "current_worktree"


class GitWorktreeSourceSelection(BaseModel):
    """Receipt-v4 state for one disposable exact-revision source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["git_worktree"] = "git_worktree"
    requested_revision: NonBlankText
    resolved_revision: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")
    ]
    caller_head_revision: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")
    ]
    caller_dirty_worktree: bool
    post_run_source_state: Literal["clean", "dirty", "unknown"]
    cleanup_confirmed: bool


type SourceSelection = Annotated[
    CurrentWorktreeSourceSelection | GitWorktreeSourceSelection,
    Field(discriminator="mode"),
]


class RepositoryPlanSource(BaseModel):
    """Caller-plan provenance when the authoritative plan is inside the repository."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["repository"] = "repository"
    repository_relative_path: NonBlankText
    caller_source_revision: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")
    ]
    caller_dirty_worktree: bool

    @field_validator("repository_relative_path")
    @classmethod
    def require_safe_posix_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if value == "." or path.is_absolute() or "\\" in value or ".." in path.parts:
            raise ValueError("repository plan path must be a safe relative POSIX path")
        return value


class ExternalPlanSource(BaseModel):
    """Non-identifying marker for a caller plan outside the selected repository."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["external"] = "external"


type PlanSource = Annotated[
    RepositoryPlanSource | ExternalPlanSource,
    Field(discriminator="kind"),
]


class ProofReceiptV3(BaseModel):
    """M8 receipt recording the selected direct or Docker execution route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[3] = 3
    task: NonBlankText
    plan_digest: NonBlankText
    overall_verdict: Verdict
    completed: bool
    criteria: Annotated[tuple[ReceiptCriterionResult, ...], Field(min_length=1)]
    environment: EnvironmentMetadataV2
    source_provenance: SourceProvenance
    evidence_manifest_digest: Annotated[
        str,
        StringConstraints(strict=True, pattern=_DIGEST_PATTERN),
    ]
    execution: ExecutionMetadata
    limitations: Annotated[tuple[NonBlankText, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[ReceiptCriterionResult, ...],
    ) -> tuple[ReceiptCriterionResult, ...]:
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.criterion_id in seen:
                raise ValueError(
                    f"receipt criterion id must be unique: {criterion.criterion_id}"
                )
            seen.add(criterion.criterion_id)
        return criteria

    @model_validator(mode="after")
    def require_consistent_overall_verdict(self) -> ProofReceiptV3:
        expected = aggregate_receipt_verdict(
            [criterion.verdict for criterion in self.criteria],
            completed=self.completed,
        )
        if self.overall_verdict is not expected:
            raise ValueError(
                "overall verdict must match criterion verdicts and completion state: "
                f"expected {expected.value}, got {self.overall_verdict.value}"
            )
        return self


class ProofReceiptV4(BaseModel):
    """M9 receipt recording source selection and authoritative plan provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[4] = 4
    task: NonBlankText
    plan_digest: NonBlankText
    overall_verdict: Verdict
    completed: bool
    criteria: Annotated[tuple[ReceiptCriterionResult, ...], Field(min_length=1)]
    environment: EnvironmentMetadataV2
    source_provenance: SourceProvenance
    evidence_manifest_digest: Annotated[
        str,
        StringConstraints(strict=True, pattern=_DIGEST_PATTERN),
    ]
    execution: ExecutionMetadata
    source_selection: SourceSelection
    plan_source: PlanSource
    limitations: Annotated[tuple[NonBlankText, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[ReceiptCriterionResult, ...],
    ) -> tuple[ReceiptCriterionResult, ...]:
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.criterion_id in seen:
                raise ValueError(
                    f"receipt criterion id must be unique: {criterion.criterion_id}"
                )
            seen.add(criterion.criterion_id)
        return criteria

    @model_validator(mode="after")
    def require_consistent_overall_verdict(self) -> ProofReceiptV4:
        expected = aggregate_receipt_verdict(
            [criterion.verdict for criterion in self.criteria],
            completed=self.completed,
        )
        if self.overall_verdict is not expected:
            raise ValueError(
                "overall verdict must match criterion verdicts and completion state: "
                f"expected {expected.value}, got {self.overall_verdict.value}"
            )
        if isinstance(self.source_selection, GitWorktreeSourceSelection):
            selection = self.source_selection
            if (
                self.source_provenance.kind != "git"
                or self.source_provenance.revision != selection.resolved_revision
                or self.source_provenance.dirty_worktree is not False
            ):
                raise ValueError(
                    "git-worktree selection requires matching clean exact source provenance"
                )
            if self.completed and (
                selection.post_run_source_state != "clean"
                or not selection.cleanup_confirmed
            ):
                raise ValueError(
                    "non-clean or unconfirmed disposable source cannot form a completed run"
                )
            if isinstance(self.plan_source, RepositoryPlanSource) and (
                self.plan_source.caller_source_revision != selection.caller_head_revision
                or self.plan_source.caller_dirty_worktree
                is not selection.caller_dirty_worktree
            ):
                raise ValueError(
                    "repository plan source must match disposable selection caller state"
                )
        elif isinstance(self.plan_source, RepositoryPlanSource) and (
            self.source_provenance.kind != "git"
            or self.source_provenance.revision != self.plan_source.caller_source_revision
            or self.source_provenance.dirty_worktree
            is not self.plan_source.caller_dirty_worktree
        ):
            raise ValueError(
                "current-worktree repository plan source must match source provenance"
            )
        return self


type SupportedProofReceipt = Annotated[
    ProofReceiptV1 | ProofReceiptV2 | ProofReceiptV3 | ProofReceiptV4,
    Field(discriminator="schema_version"),
]
_SUPPORTED_RECEIPT_ADAPTER: TypeAdapter[SupportedProofReceipt] = TypeAdapter(
    SupportedProofReceipt
)


def aggregate_receipt_verdict(
    verdicts: Sequence[Verdict],
    *,
    completed: bool,
) -> Verdict:
    """Apply run-completion semantics to M2's deterministic aggregation rule."""
    aggregate = aggregate_verdict(verdicts)
    if aggregate is Verdict.FAIL:
        return Verdict.FAIL
    if not completed:
        return Verdict.UNKNOWN
    return aggregate


def _index_results(
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
) -> dict[str, VerificationResult]:
    """Validate fixture result identity and completeness against a frozen plan."""
    if not results:
        raise ReceiptValidationError("at least one verification result is required")

    plan_ids = {criterion.id for criterion in plan.criteria}
    indexed: dict[str, VerificationResult] = {}
    for result in results:
        if result.criterion_id not in plan_ids:
            raise ReceiptValidationError(
                f"result refers to unknown criterion id: {result.criterion_id}"
            )
        if result.criterion_id in indexed:
            raise ReceiptValidationError(
                f"result criterion id must be unique: {result.criterion_id}"
            )
        indexed[result.criterion_id] = result

    missing_ids = [criterion.id for criterion in plan.criteria if criterion.id not in indexed]
    if missing_ids:
        raise ReceiptValidationError(
            f"missing result for criterion id: {', '.join(missing_ids)}"
        )
    return indexed


def build_receipt(
    *,
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
    completed: bool,
    environment: EnvironmentMetadata,
    limitations: Sequence[str],
) -> ProofReceipt:
    """Build a receipt without reading, executing, probing, or writing anything."""
    plan_criteria = cast(Sequence[_ReceiptPlanCriterion], plan.criteria)
    indexed_results = _index_results(plan, results)
    ordered_results = tuple(indexed_results[criterion.id] for criterion in plan_criteria)
    criteria = tuple(
        ReceiptCriterionResult(
            criterion_id=criterion.id,
            description=criterion.description,
            verdict=result.verdict,
            reason=result.reason,
            evidence_refs=result.evidence_refs,
        )
        for criterion, result in zip(plan_criteria, ordered_results, strict=True)
    )
    return ProofReceipt(
        task=plan.task,
        plan_digest=plan_digest(plan),
        overall_verdict=aggregate_receipt_verdict(
            [result.verdict for result in ordered_results],
            completed=completed,
        ),
        completed=completed,
        criteria=criteria,
        environment=environment,
        limitations=tuple(limitations),
    )


def build_receipt_v2(
    *,
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
    completed: bool,
    environment: EnvironmentMetadataV2,
    source_provenance: SourceProvenance,
    evidence_manifest_digest: str,
    limitations: Sequence[str],
) -> ProofReceiptV2:
    """Build an M7 receipt while retaining the existing verdict semantics."""
    plan_criteria = cast(Sequence[_ReceiptPlanCriterion], plan.criteria)
    indexed_results = _index_results(plan, results)
    ordered_results = tuple(indexed_results[criterion.id] for criterion in plan_criteria)
    criteria = tuple(
        ReceiptCriterionResult(
            criterion_id=criterion.id,
            description=criterion.description,
            verdict=result.verdict,
            reason=result.reason,
            evidence_refs=result.evidence_refs,
        )
        for criterion, result in zip(plan_criteria, ordered_results, strict=True)
    )
    return ProofReceiptV2(
        task=plan.task,
        plan_digest=plan_digest(plan),
        overall_verdict=aggregate_receipt_verdict(
            [result.verdict for result in ordered_results],
            completed=completed,
        ),
        completed=completed,
        criteria=criteria,
        environment=environment,
        source_provenance=source_provenance,
        evidence_manifest_digest=evidence_manifest_digest,
        limitations=tuple(limitations),
    )


def build_receipt_v3(
    *,
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
    completed: bool,
    environment: EnvironmentMetadataV2,
    source_provenance: SourceProvenance,
    evidence_manifest_digest: str,
    execution: ExecutionMetadata,
    limitations: Sequence[str],
) -> ProofReceiptV3:
    """Build an M8 receipt without changing historical receipt schemas."""
    plan_criteria = cast(Sequence[_ReceiptPlanCriterion], plan.criteria)
    indexed_results = _index_results(plan, results)
    ordered_results = tuple(indexed_results[criterion.id] for criterion in plan_criteria)
    criteria = tuple(
        ReceiptCriterionResult(
            criterion_id=criterion.id,
            description=criterion.description,
            verdict=result.verdict,
            reason=result.reason,
            evidence_refs=result.evidence_refs,
        )
        for criterion, result in zip(plan_criteria, ordered_results, strict=True)
    )
    return ProofReceiptV3(
        task=plan.task,
        plan_digest=plan_digest(plan),
        overall_verdict=aggregate_receipt_verdict(
            [result.verdict for result in ordered_results],
            completed=completed,
        ),
        completed=completed,
        criteria=criteria,
        environment=environment,
        source_provenance=source_provenance,
        evidence_manifest_digest=evidence_manifest_digest,
        execution=execution,
        limitations=tuple(limitations),
    )


def build_receipt_v4(
    *,
    plan: SupportedVerificationPlan,
    results: Sequence[VerificationResult],
    completed: bool,
    environment: EnvironmentMetadataV2,
    source_provenance: SourceProvenance,
    evidence_manifest_digest: str,
    execution: ExecutionMetadata,
    source_selection: SourceSelection,
    plan_source: PlanSource,
    limitations: Sequence[str],
) -> ProofReceiptV4:
    """Build an M9 receipt without changing historical receipt schemas."""
    plan_criteria = cast(Sequence[_ReceiptPlanCriterion], plan.criteria)
    indexed_results = _index_results(plan, results)
    ordered_results = tuple(indexed_results[criterion.id] for criterion in plan_criteria)
    criteria = tuple(
        ReceiptCriterionResult(
            criterion_id=criterion.id,
            description=criterion.description,
            verdict=result.verdict,
            reason=result.reason,
            evidence_refs=result.evidence_refs,
        )
        for criterion, result in zip(plan_criteria, ordered_results, strict=True)
    )
    return ProofReceiptV4(
        task=plan.task,
        plan_digest=plan_digest(plan),
        overall_verdict=aggregate_receipt_verdict(
            [result.verdict for result in ordered_results],
            completed=completed,
        ),
        completed=completed,
        criteria=criteria,
        environment=environment,
        source_provenance=source_provenance,
        evidence_manifest_digest=evidence_manifest_digest,
        execution=execution,
        source_selection=source_selection,
        plan_source=plan_source,
        limitations=tuple(limitations),
    )


def render_receipt_json(receipt: SupportedProofReceipt) -> str:
    """Render a receipt as canonical UTF-8-compatible JSON with one newline."""
    payload: dict[str, Any] = receipt.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


def render_receipt_text(receipt: SupportedProofReceipt) -> str:
    """Render a receipt as deterministic plain terminal text."""
    lines = [
        "AGENTVERIFY PROOF RECEIPT",
        "",
        f"Task: {receipt.task}",
        f"Plan: {receipt.plan_digest}",
        f"Verdict: {receipt.overall_verdict.value}",
        f"Run completed: {'yes' if receipt.completed else 'no'}",
        f"AgentVerify: {receipt.environment.agentverify_version}",
        f"Python: {receipt.environment.python_version}",
        f"Platform: {receipt.environment.platform}",
    ]
    if isinstance(receipt, (ProofReceiptV2, ProofReceiptV3, ProofReceiptV4)):
        provenance = receipt.source_provenance
        lines.extend(
            (
                f"Playwright: {receipt.environment.playwright_version}",
                f"Receipt schema: {receipt.schema_version}",
                f"Evidence manifest: {receipt.evidence_manifest_digest}",
                f"Source provenance: {provenance.kind}",
            )
        )
        if provenance.kind == "git":
            lines.extend(
                (
                    f"Source revision: {provenance.revision}",
                    f"Dirty worktree: {'yes' if provenance.dirty_worktree else 'no'}",
                )
            )
        else:
            lines.append(f"Source provenance reason: {provenance.reason}")
        if provenance.git_version is not None:
            lines.append(f"Git: {provenance.git_version}")
        if isinstance(receipt, (ProofReceiptV3, ProofReceiptV4)):
            lines.append(f"Isolation mode: {receipt.execution.isolation_mode}")
            if isinstance(receipt.execution, DockerExecutionMetadata):
                lines.extend(
                    (
                        f"Isolation profile: {receipt.execution.isolation_profile}",
                        f"Docker server: {receipt.execution.docker_server_version}",
                        f"Docker image reference: {receipt.execution.image_reference}",
                        f"Docker image ID: {receipt.execution.image_id}",
                    )
                )
        if isinstance(receipt, ProofReceiptV4):
            lines.append(f"Source selection: {receipt.source_selection.mode}")
            if isinstance(receipt.source_selection, GitWorktreeSourceSelection):
                selection = receipt.source_selection
                lines.extend(
                    (
                        f"Requested revision: {selection.requested_revision}",
                        f"Resolved revision: {selection.resolved_revision}",
                        f"Caller HEAD: {selection.caller_head_revision}",
                        "Caller dirty worktree: "
                        f"{'yes' if selection.caller_dirty_worktree else 'no'}",
                        f"Post-run source state: {selection.post_run_source_state}",
                        "Worktree cleanup confirmed: "
                        f"{'yes' if selection.cleanup_confirmed else 'no'}",
                    )
                )
            lines.append(f"Plan source: {receipt.plan_source.kind}")
            if isinstance(receipt.plan_source, RepositoryPlanSource):
                lines.append(
                    f"Plan repository path: {receipt.plan_source.repository_relative_path}"
                )
    lines.extend(("", "Criteria", ""))
    for criterion in receipt.criteria:
        evidence = ", ".join(criterion.evidence_refs) or "(none)"
        lines.extend(
            (
                f"{criterion.verdict.value:<7} {criterion.criterion_id}  {criterion.description}",
                f"        Reason: {criterion.reason}",
                f"        Evidence: {evidence}",
                "",
            )
        )

    lines.extend(("Limitations", ""))
    lines.extend(f"- {limitation}" for limitation in receipt.limitations)
    return "\n".join(lines) + "\n"


def load_receipt(path: Path) -> SupportedProofReceipt:
    """Load a strict supported receipt JSON document from disk."""
    try:
        if not path.exists():
            raise ReceiptLoadError("proof receipt does not exist")
        if not path.is_file():
            raise ReceiptLoadError("proof receipt must be a regular file")
        content = path.read_text(encoding="utf-8")
        return _SUPPORTED_RECEIPT_ADAPTER.validate_json(content)
    except ReceiptLoadError:
        raise
    except UnicodeDecodeError as error:
        raise ReceiptLoadError("proof receipt is not valid UTF-8") from error
    except ValidationError as error:
        raise ReceiptLoadError(f"proof receipt is malformed or unsupported: {error}") from error
    except OSError as error:
        raise ReceiptLoadError(f"proof receipt could not be read: {error}") from error
