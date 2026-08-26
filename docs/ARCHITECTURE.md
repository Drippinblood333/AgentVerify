# v0.1 Architecture

## Architectural goal

The smallest useful AgentVerify system is a local CLI that loads a frozen verification plan, coordinates deterministic verifiers, stores bounded evidence on disk, and emits a proof receipt. The architecture should make incorrect success difficult: domain verdict rules remain explicit, verifier failures remain visible, and no builder-specific SDK participates in the core.

M0 defined these boundaries and contracts. M8 composes versioned Plan v2 execution, bounded direct
and opt-in Docker application lifecycles, deterministic browser verification, durable evidence
authority, real verification results, receipt v3 execution metadata, read-only source provenance,
and post-run integrity inspection into the minimum complete CLI flow. Worktrees and release
hardening remain later milestones.

## System context and trust boundary

The user supplies three things: a worktree to inspect, a command or URL for a locally runnable web application, and a reviewed acceptance plan. The application and its builder-produced tests are **untrusted inputs**. AgentVerify's own verifier code, frozen plan, run metadata, and evidence hashes form the verification side of the boundary.

Direct application commands run with the current user's permissions. M8 adds one optional Docker
isolation baseline that reduces host exposure but trusts Docker Engine or Docker Desktop, its Linux
VM/kernel/runtime, and the host. It is not a VM or a proof about arbitrary malicious code. Evidence
may also contain sensitive application data, so collection must be allowlisted, bounded, and
redactable.

## Core concepts

These are the product's conceptual schemas. Plan v1 retains the criteria-only M2 contract. Plan v2
adds immutable `BrowserAcceptanceCriterion` and `BrowserProcedure` models. M3's evidence-requiring
`VerificationResult` and receipt-v1 contract remain unchanged. M5 adds immutable
`EvidenceArtifact` and `EvidenceManifest` models without adding evidence policy to either plan
version. M7 adds receipt v2 around the same verdict semantics. M8 adds receipt v3 with explicit
direct or Docker execution metadata while preserving those semantics.

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

The reviewable output for a completed run. Receipt v1 remains byte-compatible with M3. Receipt v2
adds the Playwright package version, structured source provenance, and the SHA-256 digest of the
exact persisted manifest bytes. Receipt v3 preserves those fields and adds structured execution
metadata: every new run records `isolation_mode`, while Docker runs also record the fixed isolation
profile, Docker server version, supplied image reference, and resolved local image ID. All versions
retain the same criteria, completion, and aggregate verdict semantics. The image ID identifies the
actual local image used without claiming registry authenticity, signature verification, or
attestation.

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
runs with normal user permissions in direct mode; the optional Docker route applies the separate
baseline below. Browser pages themselves may make third-party network requests.

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
waits for owned processes to disappear. POSIX launches use a separate session and own that process
group even if its direct group leader exits before cleanup; the group receives bounded SIGTERM then
SIGKILL escalation when necessary. Windows continues to guarantee the directly managed process
only. This adapter never decides a verdict.

### Docker isolation adapter

`isolation.py` uses only stdlib subprocess calls, explicit argv, `shell=False`, and bounded
timeouts. Docker validation runs only when `--isolation docker` is selected. Preflight requires a
reachable Docker Engine server 28+ using Linux containers, an exact `127.0.0.1` base URL with an
explicit port, a local Unix-socket or named-pipe Docker endpoint, a safely representable non-root current working directory, an out-of-source run
directory, and an already-local image with a valid `sha256:` ID and no declared `VOLUME` paths. It
never silently falls back to direct execution, pulls an image, or accepts raw Docker arguments.

`DockerManagedApplication` creates one collision-resistant, labeled, internal bridge network and
starts one named container through an attached foreground Docker CLI process so combined output is
continuously drained with bounded retention. The image ID is pinned with `--pull never`; the
explicit application executable replaces image `ENTRYPOINT`. The source cwd is the only host bind
mount and is read-only at `/workspace`; the run directory, host home, credentials, devices, and
Docker socket are not mounted. The root filesystem is read-only and the only writable filesystem
is a private 64 MiB `/tmp` tmpfs with `HOME=/tmp`.

The fixed profile runs as `65534:65534`, drops all capabilities, sets `no-new-privileges`, disables
the image healthcheck, and limits memory to 512 MiB, CPU to 1.0, PIDs to 256, `/dev/shm` to 64 MiB,
and `/tmp` to 64 MiB. Only the verification TCP port is published to host `127.0.0.1`, with the
same container port; the application must therefore bind `0.0.0.0` internally. The container
receives only minimal AgentVerify-controlled environment values in addition to the selected
image's own declared environment, never arbitrary host variables.

Some Docker Engine configurations retain `HostConfig.PortBindings` but do not activate a host
mapping for a container attached only to an internal bridge. After proving the exact container
target accepts TCP, AgentVerify detects this state and creates a bounded stdlib TCP relay bound only
to `127.0.0.1:<verification-port>` and forwarding only to the exact managed container IP and same
port. This fallback adds no network, container, host bind mount, Docker-socket access, or external
listener. It is owned and cleaned by the Docker application lifecycle.

Cleanup first closes and confirms any loopback relay, then addresses only the exact names created for the run. It requests bounded stop, inspects the
container, escalates and force-removes if needed, confirms absence, removes and confirms the exact
network, and finalizes the attached client and output drain. Cleanup uncertainty makes a non-FAIL
run incomplete/`UNKNOWN`; a real browser assertion `FAIL` remains `FAIL` under the existing
aggregate rule.

The internal bridge is intended to remove normal external connectivity, but Docker-managed host or
gateway services may remain reachable depending on host/runtime configuration. M8 adds no
per-destination egress policy, firewall management, proxy allowlist, DNS filtering, metadata
firewall, sidecar, image signature, attestation, remote Docker host, or remote execution.

### Local verification orchestration

The local verification use case first validates the loopback origin and probes its TCP endpoint before creating
permanent run output or starting the command. An endpoint that is already accepting connections is
invalid run configuration; requiring a closed-to-accepting transition prevents obvious stale-service
attribution but does not prove PID-level port ownership. The use case then preflights a new or empty
run directory. After readiness it uses the existing evidence-enabled `BrowserVerifier`, stops the
application, stores bounded process output, verifies and writes the evidence manifest, reloads and
verifies that durable manifest, and only then calls the M5 result bridge. Operational failures before
browser execution produce ordered `UNKNOWN` results without fabricated browser observations.
Application exit or interruption marks the run incomplete while preserving any real `FAIL` already
established.

Receipt construction remains pure and accepts either supported plan version through their shared
task and criterion snapshots. The real CLI path emitted receipt v2 through M7; M8 changes new real
runs to receipt v3 with explicit direct/Docker execution metadata after hashing the exact persisted
manifest bytes. It then atomically creates `receipt.json` and `receipt.txt` and performs the same
read-only integrity inspection exposed by the CLI. Plan v1 golden receipt bytes/schema version 1
and focused receipt-v2 loading/rendering remain unchanged. A final canonical reload of the original
plan source warns about post-snapshot semantic drift without changing the frozen receipt or verdict.

### Source provenance and run inspection

The Git adapter uses bounded stdlib subprocess calls with explicit argv and `shell=False`. It reads
HEAD and porcelain worktree status from the current directory but never stores an absolute
repository path, diff, or status body. Git absence and non-repository directories are explicit
unavailable metadata and do not alter the verdict. A dirty worktree means HEAD alone does not
identify the verified filesystem bytes. The adapter does not create or switch worktrees and does
not verify a requested revision.

Run inspection is read-only. It loads receipt v2 or v3, compares its manifest digest to the current exact
manifest bytes, applies existing artifact path/size/SHA-256 checks, and validates receipt evidence
references and criterion associations. It does not rerun Chromium, replay criteria, repair files, or
rewrite a historical verdict. These unkeyed digests are integrity indicators, not authentication:
an attacker controlling the entire bundle can recompute them consistently.

### CLI

The only v0.1 user interface and the composition root. It accepts Plan v2, a loopback base URL, an
empty run directory, a bounded startup timeout, optional `--isolation {none,docker}` and local image
reference, and a final application argv. Direct mode is the default. It maps the completed
receipt to `0`/`1`/`3` for `PASS`/`FAIL`/`UNKNOWN`, and uses `2` for invalid invocation or input.
Business rules, browser code, lifecycle state, and receipt aggregation do not live in handlers.
`inspect --run-dir` returns `0` for an intact v2/v3 bundle, `2` for invalid input or an unsupported
receipt, and `3` for an integrity warning.

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
  provenance.py      # bounded read-only Git source metadata
  isolation.py       # optional Docker preflight, fixed profile, lifecycle, and cleanup
  inspection.py      # read-only receipt/manifest/artifact integrity checks
  receipt.py         # versioned receipt construction, rendering, and loading
  cli.py             # arguments, composition, user-facing exits
```

This is a boundary sketch, not a requirement to create every file at once. Split a module only when the milestone gives it real behavior and tests. Filesystem, subprocess, browser, Git, and Docker operations are outer adapters. The core never imports the CLI or a vendor SDK.

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

Equivalent maintained runs compare stable semantics rather than claiming byte-identical run roots.
Evidence capture timestamps, selected ports in process logs, their derived artifact hashes, and the
resulting manifest digest may differ while criterion outcomes and browser-observation content remain
stable.

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
4. **Unsafe local execution:** direct execution has normal user authority; Docker mode reduces
   exposure but still trusts the host, Docker runtime, and Linux kernel boundary.
5. **Evidence quality and privacy:** too little evidence is not auditable; too much can leak secrets, become expensive, or obscure the relevant fact.

These risks should be addressed through explicit semantics, focused fixtures, artifact limits, redaction, reproducibility metadata, and later isolation—not through premature distributed infrastructure.

## Over-engineering traps

Avoid a universal agent abstraction before a real integration exists, plugin marketplaces, microservices, persistent metadata databases, event sourcing, remote workers, generalized workflow DSLs, self-healing LLM loops, and exhaustive artifact pipelines. A small typed plan, one orchestration path, one deterministic browser verifier, local artifact storage, and two receipt renderings are enough to test the v0.1 thesis.
