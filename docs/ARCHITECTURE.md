# v0.1 Architecture

## Architectural goal

The smallest useful AgentVerify system is a local CLI that loads a frozen verification plan, coordinates deterministic verifiers, stores bounded evidence on disk, and emits a proof receipt. The architecture should make incorrect success difficult: domain verdict rules remain explicit, verifier failures remain visible, and no builder-specific SDK participates in the core.

M0 defines boundaries and contracts only. It does not implement these modules.

## System context and trust boundary

The user supplies three things: a worktree to inspect, a command or URL for a locally runnable web application, and a reviewed acceptance plan. The application and its builder-produced tests are **untrusted inputs**. AgentVerify's own verifier code, frozen plan, run metadata, and evidence hashes form the verification side of the boundary.

Local execution is not strong isolation. Before the isolated-environment milestone, application commands run with the current user's permissions; this must be prominently disclosed. Evidence may also contain sensitive application data, so collection must be allowlisted, bounded, and redactable.

## Core concepts

These are conceptual schemas. Exact Pydantic fields and serialization formats belong to M2.

### Task

The human-readable unit of requested work. It has a stable identifier, title, requirement text, optional source reference, and creation metadata. It describes intent but does not decide its own success.

### AcceptanceCriterion

One observable condition attached to a `Task`. It has a stable identifier, an unambiguous statement, provenance, and a reference to a declared verification procedure. Criteria are ordered, reviewed, and snapshotted before a run. Their content or identity must not mutate during that run.

### VerificationRun

An immutable record of one attempt to verify a task against a particular criteria snapshot and worktree state. It records run identity, timestamps, configuration, plan digest, source revision or dirty-state digest when available, environment facts, lifecycle status, and result/evidence references. Operational failure is recorded; it is never silently converted into a passing result.

### VerificationResult

The outcome for exactly one criterion. It contains the criterion identifier, `PASS`, `FAIL`, or `UNKNOWN`, a concise reason, verifier identity/version, timing, and evidence references. A verifier crash, unsupported procedure, unavailable application, or missing decisive evidence normally yields `UNKNOWN` with diagnostic evidence rather than `FAIL` or `PASS`.

### Evidence

Metadata for an observation produced during verification: a stable identifier, kind, relative artifact path or inline bounded value, media type, capture time, producing verifier, size, cryptographic digest, and redaction status. Examples include command output, test reports, screenshots, traces, console errors, network summaries, and server logs. Evidence is an observation, not a verdict by itself.

### ProofReceipt

The reviewable output for a completed run. It includes task and criteria snapshots or digests, worktree/run identity, per-criterion results, aggregate verdict, evidence manifest, tool versions, and explicit limitations. It is available in a stable machine-readable form plus a human-readable rendering.

The deterministic aggregate rule is:

- `FAIL` if any criterion is `FAIL`;
- otherwise `UNKNOWN` if any criterion is `UNKNOWN` or the run did not complete reliably;
- otherwise `PASS` only when every criterion is `PASS` and the criteria set is non-empty.

### Verifier

A narrow internal protocol implemented by each deterministic verification mechanism. It declares the procedure types it supports and receives a frozen criterion, validated procedure configuration, and a run context. It returns one result and captured evidence references. It does not mutate criteria, aggregate the run, render receipts, or decide whether the implementation should be merged.

### BrowserVerifier

The first specialized verifier, planned for M4, backed by Playwright. It executes explicit browser steps and assertions against a configured local base URL while capturing relevant page, console, network, and trace observations. v0.1 does not ask an LLM to improvise navigation. Browser lifecycle and Playwright details stay behind the `Verifier` boundary.

### CLI

The only v0.1 user interface and the composition root. It parses arguments, loads configuration, invokes application use cases, reports progress and exit status, and renders concise errors. Business rules, browser code, and receipt aggregation do not live in command handlers.

## Proposed module boundaries

Once implementation begins, start with a single Python package and only the modules needed by the active milestone:

```text
agentverify/
  domain.py          # core models and verdict rules
  plan.py            # plan loading, validation, and snapshot digest
  run.py             # verification orchestration use case
  verifiers/         # deterministic verifier implementations
  evidence.py        # artifact capture and manifest operations
  receipt.py         # receipt construction and rendering
  cli.py             # arguments, composition, user-facing exits
```

This is a boundary sketch, not a requirement to create every file at once. Split a module only when the milestone gives it real behavior and tests. Filesystem, subprocess, browser, Git, and future container operations are outer adapters. The core never imports the CLI or a vendor SDK.

## Dependency direction

Dependencies point inward:

```text
CLI / browser / filesystem / subprocess / future Git adapters
                         ↓
             application orchestration
                         ↓
         domain models and verdict policies
```

- Domain models know nothing about Playwright, Typer/argparse, Git, Docker, CI, or LLM providers.
- Orchestration depends on small verifier and evidence interfaces, not concrete browser internals.
- Adapters translate external behavior into domain results and evidence metadata.
- Receipt rendering consumes completed domain records; it does not recalculate verification through hidden heuristics.

Use direct function calls and local files in v0.1. There is no need for services, a database, an event bus, dependency-injection framework, or distributed protocol.

## Deterministic versus LLM-assisted behavior

The v0.1 deterministic path includes schema validation, plan hashing, application readiness checks, declared browser/command procedures, explicit assertions, evidence capture, result aggregation, receipt rendering, and exit codes. Given equivalent application state and environment, these operations should be replayable and explain discrepancies.

LLMs may later help propose acceptance criteria, translate natural language into a draft plan, or explore an interface for candidate failures. Their output must remain reviewable input: it cannot rewrite frozen criteria, suppress evidence, or directly turn uncertainty into a passing verdict. Provider adapters, if added, stay outside the core and use a provider-neutral request/response boundary. AgentVerify must remain fully useful without any LLM API.

## Vendor neutrality

AgentVerify operates on repository state, commands, URLs, plans, observations, and artifacts—not on a coding agent's private API or conversation format. Builder provenance may be optional receipt metadata, but it cannot affect verdict semantics. Integrations for Codex, Claude Code, Cursor, or future agents should only translate their outputs into the same generic task/worktree inputs. No vendor name appears in core identifiers, schemas, or required configuration.

## Failure semantics

`FAIL` means the verifier obtained valid evidence that contradicts a criterion. `UNKNOWN` means it could not establish the criterion either way, including unsupported procedures, timeouts without a decisive assertion, environment failures, verifier defects, or incomplete evidence. Product bugs and application bugs must be distinguishable in diagnostics. A process exit code will summarize the receipt for automation, while the receipt retains per-criterion detail.

## Largest technical risks

1. **False confidence:** weak assertions or an incorrect aggregate rule can produce a credible but unjustified `PASS`.
2. **Acceptance integrity:** a builder can influence criteria or tests unless their provenance and pre-run snapshot are visible and protected.
3. **Nondeterministic web behavior:** timing, state leakage, animations, and third-party dependencies can cause flaky results.
4. **Unsafe local execution:** the application under test may be broken or malicious, and initial local execution is not isolation.
5. **Evidence quality and privacy:** too little evidence is not auditable; too much can leak secrets, become expensive, or obscure the relevant fact.

These risks should be addressed through explicit semantics, focused fixtures, artifact limits, redaction, reproducibility metadata, and later isolation—not through premature distributed infrastructure.

## Over-engineering traps

Avoid a universal agent abstraction before a real integration exists, plugin marketplaces, microservices, persistent metadata databases, event sourcing, remote workers, generalized workflow DSLs, self-healing LLM loops, and exhaustive artifact pipelines. A small typed plan, one orchestration path, one deterministic browser verifier, local artifact storage, and two receipt renderings are enough to test the v0.1 thesis.
