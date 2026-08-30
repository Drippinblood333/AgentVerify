# Roadmap

## Roadmap policy

Each milestone adds one independently verifiable capability. Work from a later milestone must not be pulled forward merely to create extension points. Completion means the stated checks pass and the documented behavior matches the repository; it does not mean adjacent milestones have started.

The sequence differs from the initial product sketch in one important way: AI-generated criteria and autonomous browser exploration move to **post-v0.1 experiments**. The public v0.1 should first prove that deterministic, user-reviewed verification and evidence receipts are useful. This reduces scope, keeps the product usable without provider credentials, and prevents probabilistic planning from weakening verdict trust.

## M0 — Foundation and architecture

**Outcome:** Establish the product boundary, engineering rules, core concept design, and incremental delivery plan.

**Done when:**

- README accurately labels the project as early development.
- Product scope, target user, non-goals, workflow, and success criteria are documented.
- Core models, module boundaries, dependency direction, verdict semantics, risks, and vendor neutrality are designed.
- Contributor instructions protect acceptance integrity and require real verification.
- No core product code or speculative dependency is added.

## M1 — Minimal CLI shell

**Outcome:** Provide an installable Python 3.12+ package with a deliberately small CLI surface, without performing verification.

**Done when:**

- `agentverify --help` and `agentverify --version` work in a clean environment.
- A placeholder-free `agentverify verify` command accepts a plan path, validates that the file exists, and clearly reports that execution is not yet available.
- CLI exit-code and error-message conventions are documented and tested.
- Packaging, linting, type checking, and unit-test commands run locally.
- No Pydantic domain model, browser automation, or receipt generator is implemented early.

## M2 — Verification data models and plan validation

**Outcome:** Define Pydantic models and a versioned local plan format for a task and its frozen acceptance criteria, plus minimal verdict semantics.

**Done when:**

- Valid plans round-trip through a documented, versioned serialization format.
- Invalid, empty, duplicate, or internally inconsistent criteria fail with actionable errors.
- `PASS`/`FAIL`/`UNKNOWN` and aggregate verdict rules have exhaustive unit tests.
- Criteria and plan snapshot digests are stable for canonical equivalent input.
- Models contain no run persistence, evidence, receipt, Playwright, Git, LLM-provider, or CLI dependencies.

## M3 — Proof receipt generation

**Outcome:** Generate auditable receipts from known run/result fixtures before executing real verifiers.

**Done when:**

- The same completed run produces stable machine-readable and human-readable receipts.
- Receipts include plan identity, environment/tool metadata, per-criterion reasons, evidence references, aggregate verdict, and limitations.
- Missing or inconsistent result/evidence references are rejected rather than hidden.
- Golden-file and aggregate-verdict tests cover pass, fail, unknown, empty, and interrupted cases.
- Receipt rendering performs no browser execution or heuristic re-judgment.

## M4 — Deterministic Playwright verification

**Outcome:** Run one explicit, documented browser procedure format against a user-started local web application.

**Done when:**

- Verification Plan v1 remains unchanged while strict Plan v2 freezes explicit browser procedures.
- Unsupported procedure or step syntax is rejected as invalid input; it is not a runtime verdict.
- A maintained standard-library application fixture demonstrates navigation, fill, click,
  visible-state assertion, context isolation, and an intentional assertion failure in real Chromium.
- A supported assertion contradiction maps to `FAIL`; browser/action/readiness failures map to
  `UNKNOWN`; only complete successful procedures map to `PASS`.
- Runs use one headless Chromium browser, bounded timeouts, a fixed viewport, and a fresh browser
  context per criterion.
- Browser execution returns an internal outcome and does not fabricate evidence references or feed
  M3 proof receipts before M5 evidence capture exists.
- No autonomous or LLM-generated navigation is present.

## M5 — Evidence capture and artifact manifest

**Outcome:** Attach useful, integrity-checked browser and process evidence to real verification results.

**Implemented boundary:** M5 provides caller-rooted artifact storage, a portable v1 manifest,
secret-aware browser capture configuration, real opt-in Chromium artifacts, integrity verification,
and the browser-outcome result bridge. It does not start applications, drive the CLI verification
flow, or build browser-backed proof receipts.

**Done when:**

- Configured screenshots, Playwright traces, console errors, bounded network summaries, and server/process logs can be captured.
- Every artifact has type, size, timestamp, producer, relative path, and digest metadata.
- Artifact size/count limits, secret-aware defaults, redaction behavior, and retention expectations are documented and tested.
- A reviewer can move a run directory and still resolve receipt evidence through relative paths.
- Missing/corrupt artifacts are detected and cannot support a conclusive result silently.
- Network summaries omit bodies, headers, cookies, queries, and fragments; text redaction is
  best-effort, while explicitly enabled screenshots and traces may contain sensitive page data.
- Artifact retention remains caller controlled, and SHA-256 metadata is an integrity indicator
  rather than cryptographic attestation.

## M6 — End-to-end local verification

**Outcome:** Connect CLI, plan validation, application lifecycle, deterministic browser verification, evidence, and receipts into the minimum complete user flow.

**Implemented boundary:** M6 provides one local Plan v2 CLI path with explicit argv process startup,
bounded TCP readiness and output capture, deterministic Chromium execution, durable manifest
authority, proof-receipt persistence, four exit codes, interruption handling, and bounded cleanup.
It does not record Git provenance, claim isolation, or introduce later verifier abstractions.

**Done when:**

- One command verifies the maintained sample application from a clean checkout.
- Startup, readiness, shutdown, interrupt, and timeout behavior are tested without leaking child processes.
- CLI exit codes distinguish pass, verification fail, unknown/incomplete, and invalid invocation.
- An end-to-end fixture deliberately produces each verdict and a reviewable receipt.
- Setup and troubleshooting documentation is sufficient for a new local user.

## M7 — Reproducibility and anti-tampering metadata

**Outcome:** Make run provenance and acceptance-plan changes obvious without claiming a security guarantee.

**Implemented boundary:** M7 preserves receipt v1 and manifest v1 while making the real verification
path emit receipt v2 with Playwright version, read-only optional Git provenance, and a digest of the
exact persisted manifest. It adds final self-inspection, read-only `inspect --run-dir`, canonical
post-snapshot plan-drift warnings, and semantic repeat-run coverage. It does not add signatures,
sandboxing, source mutation, worktree management, or requested-revision verification.

**Done when:**

- Receipts record plan digest, relevant tool versions, source revision, and dirty-worktree state when Git is available.
- The CLI warns on post-snapshot plan changes and mismatched evidence manifests.
- Repeat-run tests explain or eliminate known nondeterminism in maintained fixtures.
- Threat model and limitations distinguish integrity indicators from cryptographic attestation.
- AgentVerify remains usable outside Git.

## M8 — Optional isolated execution baseline

**Outcome:** Add one documented opt-in isolation route for untrusted local application commands, without building a remote execution platform.

**Implemented boundary:** M8 retains direct execution as the default and adds one optional,
locally preflighted Docker CLI route for Linux containers. It pins an already-local image ID,
applies the fixed `agentverify-docker-baseline-v1` filesystem/network/environment/privilege/resource
profile, cleans exact managed resources, and records direct or Docker execution in receipt v3. It
does not claim resistance to Docker/runtime/kernel escape, pull/build images, mutate source, create
worktrees, select revisions, or provide remote execution.

**Done when:**

- The selected local container strategy has explicit filesystem, network, environment, resource, and cleanup boundaries.
- Escape-prone inputs and unsupported host setups fail safely with actionable diagnostics.
- Equivalent maintained verification fixtures run both directly and in isolation.
- Isolation is optional and its guarantees and gaps are documented.
- No scheduler, remote worker, Kubernetes integration, or image registry service is introduced.

## M9 — Git and worktree integration

**Outcome:** Verify a selected revision in a disposable worktree while preserving the user's working directory.

**Implemented boundary:** M9 preserves the caller-loaded frozen plan and M8 execution routes while
adding optional local commit selection, one detached system-temporary worktree, explicit direct and
Docker source roots, post-run source-mutation detection, exact worktree cleanup confirmation, and
receipt v4 source-selection/plan-source metadata. Git remains optional without `--revision`; M9
does not fetch, clone, pull, prune globally, support submodules, or start M10 release hardening.

**Done when:**

- The CLI can create, identify, use, and clean a temporary worktree for a requested local revision.
- Dirty state, submodule limitations, cleanup failure, and interruption have tested behavior.
- The original worktree is never modified by the verification run.
- Receipt provenance identifies the verified revision and relevant plan source.
- Git remains an adapter rather than a requirement of domain models.

## M10 — CI contract and release hardening

**Outcome:** Stabilize the local CLI for generic CI use and prepare a trustworthy release candidate.

**Implemented boundary on this branch:** M10 adds a separate machine-output schema v1 for finalized
receipt-v4 bundles, keeps the text/exit/verdict contracts intact, qualifies the selected Linux and
Windows/Python matrices, builds and clean-installs wheel/sdist artifacts, and documents CI,
security/privacy, failure, compatibility, and packaging boundaries. It adds no verifier, receipt
schema, verdict semantics, publishing path, tag, or public release claim. M10 remains awaiting
independent review and is not marked globally complete here.

**Done when:**

- A documented non-interactive command emits stable exit codes and machine-readable receipt paths.
- The test matrix covers supported Python versions and major host platforms selected for v0.1.
- Security, privacy, failure-mode, packaging, upgrade, and compatibility documentation are reviewed.
- A generic CI example consumes the CLI without embedding CI-vendor behavior in core code.
- Release candidate installation and the full sample verification are reproduced from built artifacts.

## M11 — Public v0.1 release

**Outcome:** Publish the smallest credible evidence-first verifier for local web applications.

**Done when:**

- All v0.1 scope and product success criteria have recorded release evidence.
- Package metadata, license, contribution guide, security policy, changelog, and versioning policy are complete and reviewed.
- Documentation makes implemented features, limitations, local-execution risks, and non-goals unmistakable.
- A clean-machine smoke test installs the release artifact and completes the sample workflow.
- Release artifacts and notes are published only after an explicit release decision.

## Post-v0.1 experiments — not release blockers

### AI-assisted acceptance-criteria drafting

An optional provider-neutral adapter may propose criteria or a draft plan. Completion requires human review before freezing, recorded provenance, adversarial tests for omitted/softened criteria, and proof that deterministic verification works unchanged when the feature is absent.

### Autonomous browser exploration

An experimental verifier may explore a UI and suggest candidate failures or regression procedures. Completion requires strict budgets, replayable generated steps, evidence for every claim, clear `UNKNOWN` behavior, and isolation from the deterministic receipt authority. Exploration must never silently substitute for reviewed acceptance criteria.

### Hosted and ecosystem integrations

GitHub-specific automation, richer CI adapters, remote execution, dashboards, and team workflows should be considered only after real v0.1 use identifies concrete needs. Each requires a separate product and threat-model decision.
