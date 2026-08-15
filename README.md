# AgentVerify

> **Status: early development.** AgentVerify currently validates Verification Plan v1 files. Application verification, browser automation, evidence capture, and proof receipts are not implemented yet.

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
- the minimal `PASS`/`FAIL`/`UNKNOWN` domain aggregation rule.

AgentVerify currently validates verification plans. It does **not** yet execute application verification or produce criterion verdicts.

Install the package and development tools from a local checkout:

```console
python -m pip install -e ".[dev]"
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

## Verification Plan v1

M2 supports one strict JSON format. Unknown fields are rejected, every string must contain non-whitespace text, and each criterion ID must be unique within its plan.

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
