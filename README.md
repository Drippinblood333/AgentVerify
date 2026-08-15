# AgentVerify

> **Status: early development.** The M1 CLI foundation is implemented. Verification execution, plan parsing, browser automation, and proof receipts are not implemented yet.

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
- `agentverify verify --plan FILE`, which validates that `FILE` exists and is a regular file.

The `verify` command deliberately does not read the plan or execute verification in M1. Those capabilities remain planned for later milestones.

Install the package and development tools from a local checkout:

```console
python -m pip install -e ".[dev]"
```

Current CLI behavior:

```console
$ agentverify --version
AgentVerify 0.1.0.dev0

$ agentverify verify --plan README.md
Verification execution is not implemented yet.
Plan: /absolute/path/to/AgentVerify/README.md
```

The M1 exit-code policy is intentionally small: `0` means the command completed successfully, while `2` means invalid command usage or input. Verification verdict exit codes do not exist yet.

## Intended workflow

The future CLI is expected to support a workflow like this (the interface is illustrative, not yet available):

```console
$ agentverify verify --plan acceptance.yaml --app-command "python -m myapp"

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
