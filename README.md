# AgentVerify

[![CI](https://github.com/Drippinblood333/AgentVerify/actions/workflows/ci.yml/badge.svg)](https://github.com/Drippinblood333/AgentVerify/actions/workflows/ci.yml)

> **Status: early development.** AgentVerify validates Verification Plan v1 and v2 files, constructs deterministic proof receipts from supplied fixture results, and can execute explicit browser procedures with durable, integrity-checked evidence against an already-running local web application through its Python library. Complete CLI verification is not implemented yet.

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
  evidence-gated conversion from browser outcomes to `VerificationResult` objects.

Browser execution is currently a library-level capability. It assumes the application is already
running and gives every criterion a fresh browser context. The explicit evidence-enabled path stores
artifacts beneath a caller-supplied run root using portable relative paths. It can convert browser
outcomes into real `VerificationResult` objects only after referenced artifacts pass path, size, and
SHA-256 checks. Browser results remain separate from proof receipts until M6; fixture receipts are
not evidence that an application was run. AgentVerify still does not start or manage applications.

Install the package and development tools from a local checkout, then install
Playwright-managed Chromium for browser verification:

```console
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Current CLI behavior:

```console
$ agentverify --version
AgentVerify 0.1.0.dev0

$ agentverify verify --plan examples/password-reset.plan.json
Verification plan is valid.

Task: Implement password reset
Criteria: 2
Schema version: 1
Plan digest: sha256:...

Verification execution is not implemented yet.
```

The exit-code policy remains intentionally small: `0` means the command completed successfully, while `2` means invalid command usage or input. Verification verdict exit codes do not exist yet.

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

The library accepts only loopback `http` or `https` base URLs, launches Playwright-managed Chromium
headlessly with a fixed 1280×720 viewport, and creates a fresh `BrowserContext` for every criterion.
This is browser-state isolation only, not a sandbox: the already-running application has normal user
permissions, and a loaded page may still make third-party network requests.

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

## Intended workflow

The future CLI is expected to support a workflow like this (the interface is illustrative, not yet available):

```console
$ agentverify verify --plan acceptance.json --app-command "python -m myapp"

AGENTVERIFY PROOF RECEIPT
Task: Implement password reset
Verdict: FAIL

PASS  Reset email can be requested
PASS  Invalid token is rejected
FAIL  Reset token cannot be reused

Evidence saved to .agentverify/runs/...
```

The first release is deliberately limited to **locally runnable web applications** and a CLI-first experience. It will not be a hosted platform, coding-agent orchestrator, team dashboard, or general-purpose mobile and desktop testing system.

## Project documents

- [Product definition](docs/PRODUCT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributor and coding-agent rules](AGENTS.md)

No public release or stable API exists yet. See the roadmap for what each milestone will actually deliver.
