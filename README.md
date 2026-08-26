# AgentVerify

[![CI](https://github.com/Drippinblood333/AgentVerify/actions/workflows/ci.yml/badge.svg)](https://github.com/Drippinblood333/AgentVerify/actions/workflows/ci.yml)

> **Status: early development.** AgentVerify can run one complete local verification flow, record
> source provenance when Git is available, bind receipt v2 to persisted evidence, and inspect an
> existing run for integrity mismatches.

Licensed under [Apache-2.0](LICENSE).

AgentVerify is an independent software verification layer for AI coding agents. It is intended to answer a practical question after Codex, Claude Code, Cursor, or another builder says a task is done: **does the software actually satisfy the agreed requirements when it runs?**

Coding agents can produce convincing code and confident completion reports while missing an edge case, weakening a test, or leaving a browser flow broken at runtime. AgentVerify is designed to evaluate a frozen set of acceptance criteria by executing observable checks and collecting evidence such as command output, test results, screenshots, browser logs, and HTTP responses.

Its guiding ideas are:

- **Do not trust “done.” Verify it.**
- **Execution over opinion.**
- **Evidence over confidence.**
- **Use `UNKNOWN` when the evidence cannot justify `PASS` or `FAIL`.**

## Development status

Currently implemented:

- an installable Python 3.12+ package;
- `agentverify --help` and `agentverify --version`; and
- strict validation of versioned JSON verification plans;
- deterministic plan digests; and
- the minimal `PASS`/`FAIL`/`UNKNOWN` domain aggregation rule; and
- deterministic JSON and plain-text proof receipts from validated fixture results; and
- headless Chromium execution of strict `navigate`, `fill`, `click`, and
  `assert_visible` procedures against a loopback HTTP(S) application; and
- bounded local evidence artifacts, a portable manifest, integrity verification, and an
  evidence-gated conversion from browser outcomes to `VerificationResult` objects; and
- a complete Plan v2 CLI flow with bounded application lifecycle, TCP readiness, real environment
  metadata, JSON/text receipts, deterministic exit codes, and direct-child cleanup; and
- receipt v2 with Playwright and read-only Git provenance metadata, an exact-byte evidence-manifest
  digest, post-snapshot plan-drift warnings, and read-only run-directory inspection.

The CLI executes the supplied application argv locally, without a shell, and gives every criterion a
fresh browser context. Artifacts live beneath a caller-supplied run directory using portable
relative paths. A browser outcome becomes conclusive only after its referenced evidence, including
the baseline browser observation, passes path, size, and SHA-256 checks.

Install the package and development tools from a local checkout, then install
Playwright-managed Chromium for browser verification:

```console
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

## Clean-checkout demo

From PowerShell, after installation, run the maintained Plan v2 and standard-library sample app:

```console
agentverify verify `
  --plan examples/greeting.plan.json `
  --base-url http://127.0.0.1:8765 `
  --run-dir .agentverify/demo-run `
  --app-command python examples/greeting_app.py
```

For POSIX shells, replace the PowerShell backticks with `\`. `--app-command` consumes the remaining
argv and must be the final AgentVerify option. A custom port therefore looks like:

```console
agentverify verify --plan examples/greeting.plan.json --base-url http://127.0.0.1:9000 --run-dir .agentverify/demo-run-9000 --app-command python examples/greeting_app.py --port 9000
```

The successful demo prints paths to `receipt.txt`, `receipt.json`, and
`evidence-manifest.json`, then exits after terminating the sample application. The run directory
must be nonexistent or empty; AgentVerify never overwrites a prior run.

Inspect the completed bundle without rerunning the application or Chromium:

```console
agentverify inspect --run-dir .agentverify/demo-run
```

A valid receipt-v2 bundle reports its historical verdict and `Integrity: OK`. Invalid inspection
input exits `2`; a manifest-binding mismatch or missing/corrupt evidence emits an integrity warning
and exits `3`. Inspection never rewrites evidence or converts an integrity problem into an
application `FAIL`.

Exit codes are stable: `0` is `PASS`, `1` is verification `FAIL`, `2` is invalid invocation/input,
and `3` is `UNKNOWN` or incomplete verification. The readiness timeout defaults to 10,000 ms and
can be set from 100 to 60,000 ms with `--startup-timeout-ms`.

## Verification Plan versions

Plan v1 remains the published criteria-only format from M2. Its schema and canonical password-reset
digest are unchanged:

```json
{
  "schema_version": 1,
  "task": "Implement password reset",
  "criteria": [
    {
      "id": "AC-001",
      "description": "A user can request a password reset"
    },
    {
      "id": "AC-002",
      "description": "Invalid reset tokens are rejected"
    }
  ]
}
```

See [`examples/password-reset.plan.json`](examples/password-reset.plan.json) for a valid plan. The displayed SHA-256 digest fingerprints the validated plan's canonical content; it helps detect changes but is not a security or authenticity guarantee.
Plan v1 remains loadable and digest-compatible, but executable local verification requires Plan v2
because v1 contains no frozen procedure.

Plan v2 adds one strict browser procedure per criterion:

```json
{
  "schema_version": 2,
  "task": "Verify greeting flow",
  "criteria": [
    {
      "id": "AC-001",
      "description": "A greeting becomes visible after entering a name",
      "procedure": {
        "type": "browser",
        "timeout_ms": 5000,
        "steps": [
          {"type": "navigate", "path": "/"},
          {"type": "fill", "selector": "#name", "value": "Ada"},
          {"type": "click", "selector": "#greet"},
          {"type": "assert_visible", "selector": "#message"}
        ]
      }
    }
  ]
}
```

See [`examples/greeting.plan.json`](examples/greeting.plan.json). Procedures must start with
`navigate`, contain an `assert_visible`, and use a timeout from 100 to 30,000 milliseconds. Unknown
procedure or step syntax is invalid input. Navigation paths cannot leave the supplied local origin.

The CLI and library accept only loopback `http` or `https` base URLs, launch Playwright-managed
Chromium headlessly with a fixed 1280×720 viewport, and create a fresh `BrowserContext` for every
criterion. TCP readiness only establishes that the configured endpoint accepts connections. This
is browser-state isolation, not a sandbox: the application command executes with the current user's
working directory, environment, and permissions, and a loaded page may make third-party requests.

Before starting the managed command, the CLI requires the configured endpoint to be closed. It then
waits for the endpoint to begin accepting connections after startup. An already-listening endpoint
is rejected with exit code `2` so an obvious stale or unrelated service cannot be attributed to the
managed command. This closed-to-accepting check is an attribution guard, not cryptographic process
identity or proof of port ownership.

## Evidence capture

The default evidence policy stores one small JSON browser-observation summary per criterion. This is
the baseline durable observation required for browser `PASS` or `FAIL` to become a conclusive
`VerificationResult`; screenshots, traces, console errors, and network summaries are supplemental
and cannot authorize a conclusive result by themselves. Rich captures require explicit run-level
opt-in and are not fields in Plan v2. Defaults are bounded to 128 artifacts per run, eight per
criterion, 16 MiB per artifact, and 256 KiB per textual artifact; console and network collection are
capped at 100 and 200 entries respectively.

Network summaries contain method, scheme, host, path, status, resource type, and success state. They
exclude bodies, cookies, headers, URL query strings, and fragments. Console/runtime error and
caller-supplied process-log text passes through small best-effort redaction for common authorization,
password, token, API-key, and secret forms. This is not a guarantee that arbitrary application
content contains no secrets. Explicit screenshots and traces may contain sensitive visible page
data or browser state.

Artifacts and `evidence-manifest.json` are retained under the caller-supplied run root; retention and
deletion are currently the caller's responsibility. Manifest SHA-256 values detect byte changes but
are integrity indicators, not signatures, authentication, or cryptographic attestation.

Receipt v2 records the SHA-256 of the exact persisted manifest bytes. This detects accidental
modification, stale or partially copied files, and unsynchronized artifact replacement when the
receipt is trusted. It does not establish authenticity: an attacker able to modify the receipt,
manifest, and artifacts can recompute all unkeyed hashes consistently.

Git provenance is captured read-only from the run's current working directory using bounded Git
commands. AgentVerify remains usable when Git is absent or the directory is outside a repository.
When `dirty_worktree` is true, the recorded HEAD revision does not uniquely identify the verified
source bytes. M7 neither creates/switches worktrees nor verifies an arbitrary requested revision.

The plan object loaded before execution remains the authoritative frozen snapshot. After run
finalization AgentVerify canonically reloads the original plan path; a semantic change, deletion, or
invalid replacement produces a warning while preserving the receipt digest and verdict for the
frozen snapshot. Formatting-only equivalent rewrites do not warn.

Equivalent maintained runs are expected to agree on deterministic verification semantics such as
plan digest, criterion order/verdict/reason, tool/source metadata, and browser-observation content.
Complete directories need not be byte-identical: UTC capture times, runtime-selected ports in
process logs, their artifact digests, and the resulting manifest digest are intentionally dynamic.

## Run directory

Each completed or reviewable incomplete run is self-contained:

```text
<run-dir>/
  evidence-manifest.json
  receipt.json
  receipt.txt
  artifacts/
    ...
```

Application stdout and stderr are continuously drained into one bounded, best-effort-redacted
process-log artifact. The retained log is explicitly marked when truncated. Screenshots and traces
remain opt-in library captures and may contain sensitive application data. Run retention and
deletion remain the user's responsibility.

## Troubleshooting

- **Chromium is missing:** run `python -m playwright install chromium`.
- **Port already in use or already accepting connections:** choose another free port and pass the
  same value in `--base-url` and after the final `--app-command ... --port` argument. AgentVerify
  refuses to start the command when it cannot safely attribute an already-listening endpoint.
- **Application never becomes ready:** check the configured host/port and inspect the process-log
  artifact; increase `--startup-timeout-ms` only when startup legitimately needs more time.
- **Run directory already contains files:** choose a new directory or explicitly empty the intended
  directory before starting. AgentVerify will not overwrite it.

The first release is deliberately limited to **locally runnable web applications** and a CLI-first experience. It will not be a hosted platform, coding-agent orchestrator, team dashboard, or general-purpose mobile and desktop testing system.

## Project documents

- [Product definition](docs/PRODUCT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributor and coding-agent rules](AGENTS.md)

No public release or stable API exists yet. See the roadmap for what each milestone will actually deliver.
