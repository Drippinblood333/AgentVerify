# v0.1 Architecture

## Architectural goal

The smallest useful AgentVerify system is a local CLI that loads a frozen verification plan, coordinates deterministic verifiers, stores bounded evidence on disk, and emits a proof receipt. The architecture should make incorrect success difficult: domain verdict rules remain explicit, verifier failures remain visible, and no builder-specific SDK participates in the core.

M0 defined these boundaries and contracts. M6 composes versioned Plan v2 execution, a bounded local
application lifecycle, deterministic browser verification, durable evidence authority, real
verification results, and unchanged proof-receipt semantics into the minimum complete CLI flow.
Source provenance, isolation, worktrees, and release hardening remain later milestones.

## System context and trust boundary

The user supplies three things: a worktree to inspect, a command or URL for a locally runnable web application, and a reviewed acceptance plan. The application and its builder-produced tests are **untrusted inputs**. AgentVerify's own verifier code, frozen plan, run metadata, and evidence hashes form the verification side of the boundary.

Local execution is not strong isolation. Before the isolated-environment milestone, application commands run with the current user's permissions; this must be prominently disclosed. Evidence may also contain sensitive application data, so collection must be allowlisted, bounded, and redactable.

## Core concepts

These are the product's conceptual schemas. Plan v1 retains the criteria-only M2 contract. Plan v2
adds immutable `BrowserAcceptanceCriterion` and `BrowserProcedure` models. M3's evidence-requiring
`VerificationResult` and proof receipt remain unchanged. M5 adds immutable `EvidenceArtifact` and
`EvidenceManifest` models without adding evidence policy to either plan version.

### Task

The human-readable unit of requested work. It has a stable identifier, title, requirement text, optional source reference, and creation metadata. It describes intent but does not decide its own success.

### AcceptanceCriterion

One observable condition attached to a `Task`. It has a stable identifier, an unambiguous statement, provenance, and a reference to a declared verification procedure. Criteria are ordered, reviewed, and snapshotted before a run. Their content or identity must not mutate during that run.

### VerificationRun

An immutable record of one attempt to verify a task against a particular criteria snapshot and worktree state. It records run identity, timestamps, configuration, plan digest, source revision or dirty-state digest when available, environment facts, lifecycle status, and result/evidence references. Operational failure is recorded; it is never silently converted into a passing result.

### VerificationResult

The outcome for exactly one criterion. It contains the criterion identifier, `PASS`, `FAIL`, or `UNKNOWN`, a concise reason, verifier identity/version, timing, and evidence references. A verifier crash, unsupported procedure, unavailable application, or missing decisive evidence normally yields `UNKNOWN` with diagnostic evidence rather than `FAIL` or `PASS`.

### Evidence

Metadata for an observation produced during verification: a stable identifier, fixed kind, portable
relative artifact path, media type, UTC capture time, producing verifier, byte size, SHA-256 digest,
criterion association when applicable, and redaction status. M5 supports browser observations,
screenshots, Playwright traces, console errors, bounded network summaries, and caller-supplied
process logs. Evidence is an observation, not a verdict by itself, and its digest is an integrity
indicator rather than cryptographic attestation.

### ProofReceipt

The reviewable output for a completed run. It includes task and criteria snapshots or digests, worktree/run identity, per-criterion results, aggregate verdict, evidence manifest, tool versions, and explicit limitations. It is available in a stable machine-readable form plus a human-readable rendering.

The deterministic aggregate rule is:

- `FAIL` if any criterion is `FAIL`;
- otherwise `UNKNOWN` if any criterion is `UNKNOWN` or the run did not complete reliably;
- otherwise `PASS` only when every criterion is `PASS` and the criteria set is non-empty.

### Verifier

A future narrow internal protocol for multiple deterministic verification mechanisms. M4 does not
introduce this abstraction because only one concrete verifier exists. A verifier must not mutate
criteria, aggregate a run, render receipts, or decide whether an implementation should be merged.

### BrowserVerifier

The browser library executor backed by Playwright. It accepts a loopback HTTP(S) origin separately from a
frozen Plan v2, launches one headless Chromium browser with a fixed viewport, and gives each
criterion a fresh `BrowserContext` and page. It executes only `navigate`, `fill`, `click`, and
`assert_visible`, using bounded timeouts and Playwright auto-waiting. The original M4 path still
returns only internal `BrowserExecutionResult` objects and requires no filesystem. M5's explicit
evidence-enabled path reuses that same step engine, records only a minimal observation by default,
and requires opt-in for screenshots, traces, console errors, and network summaries.

Fresh contexts isolate cookies, local storage, session storage, and page state between criteria.
They do not isolate the browser or application from the host. The externally started application
runs with normal user permissions, and loaded pages may make third-party network requests.

### EvidenceStore and result bridge

`EvidenceStore` owns bounded, non-overwriting local artifact writes beneath a caller-supplied run
root. Finalized bytes are measured from disk, recorded with portable relative paths, and checked for
regular-file containment, size, and SHA-256 digest. The manifest can move with its complete run
directory. Default limits are 128 artifacts per run, eight per criterion, 16 MiB per artifact,
256 KiB per text artifact, 100 console entries, and 200 network entries.

Rich capture is privacy-sensitive. Network summaries omit headers, bodies, cookies, queries, and
fragments. Console/runtime errors and process-log text receive best-effort common-secret redaction;
screenshots and traces may still contain page data. Evidence retention is controlled by the caller.

The M5 result bridge requires exact Plan v2 criterion coverage. A browser `PASS` or `FAIL` becomes a
conclusive `VerificationResult` only when every referenced artifact is present in the supplied
manifest and passes integrity verification, and those references include an intact
`browser_observation`. Screenshots, traces, console errors, network summaries, and process logs are
supplemental and cannot substitute for that baseline observation. Missing, unsafe, or corrupt
evidence downgrades the outcome to `UNKNOWN`; evidence never upgrades an existing `UNKNOWN`. The
bridge stops at results and does not render a receipt or manage an application process.

### ManagedApplication

`ManagedApplication` starts one explicit argv with `shell=False`, inheriting the current directory
and environment. It immediately merges and continuously drains stdout/stderr while retaining a
bounded prefix. TCP readiness against the validated loopback origin is deadline-bound and checks
for early process exit. Shutdown requests termination, waits briefly, escalates to force-kill, and
waits for the direct child. POSIX launches use a separate session and signal its process group;
Windows guarantees the directly managed process only. This adapter never decides a verdict.

### Local verification orchestration

The M6 use case preflights a new or empty run directory before process startup. After readiness it
uses the existing evidence-enabled `BrowserVerifier`, stops the application, stores bounded process
output, verifies and writes the evidence manifest, reloads and verifies that durable manifest, and
only then calls the M5 result bridge. Operational failures before browser execution produce ordered
`UNKNOWN` results without fabricated browser observations. Application exit or interruption marks
the run incomplete while preserving any real `FAIL` already established.

Receipt construction remains pure and accepts either supported plan version through their shared
task and criterion snapshots. M6 invokes it with executable Plan v2 results, real Python/platform
metadata, and explicit limitations. `receipt.json` and `receipt.txt` are atomically created beside
the manifest. Plan v1 golden receipt bytes and schema version 1 remain unchanged.

### CLI

The only v0.1 user interface and the composition root. It accepts Plan v2, a loopback base URL, an
empty run directory, a bounded startup timeout, and a final application argv. It maps the completed
receipt to `0`/`1`/`3` for `PASS`/`FAIL`/`UNKNOWN`, and uses `2` for invalid invocation or input.
Business rules, browser code, lifecycle state, and receipt aggregation do not live in handlers.

## Proposed module boundaries

Once implementation begins, start with a single Python package and only the modules needed by the active milestone:

```text
agentverify/
  application.py     # bounded subprocess lifecycle, output drain, and TCP readiness
  domain.py          # core models and verdict rules
  browser_plan.py    # pure Plan v2 browser procedure models
  browser.py         # concrete Playwright execution and internal outcomes
  plan.py            # version dispatch, loading, validation, and snapshot digest
  run.py             # evidence-authoritative local verification orchestration
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

Unsupported procedure or step syntax is invalid plan input because Plan v2 is strict. For a valid
browser procedure, `PASS` means every step and declared assertion succeeded. `FAIL` means a supported
explicit assertion contradicted the criterion. `UNKNOWN` means execution could not establish the
criterion, including browser infrastructure failure, unreachable navigation, or a fill/click failure
before an assertion. Unexpected AgentVerify programming bugs still surface rather than being
silently converted to `UNKNOWN`. Evidence capture failure is not an application `FAIL`; insufficient
or invalid durable evidence prevents a conclusive outcome from crossing into the domain result.
Startup failure, early exit, readiness timeout, interruption, and unreliable cleanup are operational
uncertainty. They cannot create an application `FAIL`; an already established real `FAIL` remains
authoritative under receipt aggregation even when the run later becomes incomplete.

## Largest technical risks

1. **False confidence:** weak assertions or an incorrect aggregate rule can produce a credible but unjustified `PASS`.
2. **Acceptance integrity:** a builder can influence criteria or tests unless their provenance and pre-run snapshot are visible and protected.
3. **Nondeterministic web behavior:** timing, state leakage, animations, and third-party dependencies can cause flaky results.
4. **Unsafe local execution:** the application under test may be broken or malicious, and initial local execution is not isolation.
5. **Evidence quality and privacy:** too little evidence is not auditable; too much can leak secrets, become expensive, or obscure the relevant fact.

These risks should be addressed through explicit semantics, focused fixtures, artifact limits, redaction, reproducibility metadata, and later isolation—not through premature distributed infrastructure.

## Over-engineering traps

Avoid a universal agent abstraction before a real integration exists, plugin marketplaces, microservices, persistent metadata databases, event sourcing, remote workers, generalized workflow DSLs, self-healing LLM loops, and exhaustive artifact pipelines. A small typed plan, one orchestration path, one deterministic browser verifier, local artifact storage, and two receipt renderings are enough to test the v0.1 thesis.
