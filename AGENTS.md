# DoneWitness Contributor Instructions

These instructions apply to every coding agent and human contributor working in this repository.

## Product boundary

DoneWitness is an independent, evidence-first verification layer for work produced by AI coding agents. The v0.1 product verifies **locally runnable web applications** through a CLI. It is not an agent orchestrator, hosted service, dashboard, mobile or desktop verifier, or enterprise platform.

Do not expand the v0.1 scope without an explicit product decision recorded in the documentation. In particular, do not add cloud services, accounts, payments, distributed workers, Kubernetes, queues, vector databases, or a general plugin platform because they may be useful later.

## Engineering principles

1. Prefer execution over opinion and evidence over confidence.
2. Keep implementation and verification concerns independent. Never trust a builder's success claim as proof.
3. Freeze acceptance criteria before a verification run. Never edit, remove, reinterpret, or weaken criteria or tests merely to make a change pass.
4. Use `PASS`, `FAIL`, and `UNKNOWN`. Report `UNKNOWN` when the available procedure or evidence cannot support a reliable conclusion.
5. A verdict must be traceable to evidence. Preserve relevant command output, browser artifacts, HTTP observations, and runtime errors.
6. Prefer deterministic checks. Any future LLM-assisted feature must be optional, declared, and separated from the deterministic verdict path.
7. Keep the architecture and public interfaces vendor-neutral. Codex, Claude Code, Cursor, or a human may produce the implementation under test; core models must not depend on any one vendor's SDK or transcript format.
8. Build only what the current milestone requires. Avoid speculative abstractions and premature infrastructure.

## Change workflow

Before changing code or documentation:

- Read the relevant implementation, tests, and design documents.
- Check the current worktree and preserve unrelated user changes.
- Identify the acceptance criteria and the smallest in-scope change.

While changing it:

- Keep behavior explicit and interfaces small.
- Add a dependency only when the standard library or an existing dependency cannot reasonably satisfy a current requirement. Document the reason.
- Do not add placeholder modules, unused extension points, or pseudocode for later milestones.
- Treat tests and acceptance criteria as protected verification assets. Changes to them require a stated product reason and review separate from implementation convenience.

Before claiming completion:

- Run the tests and checks relevant to the files changed.
- Exercise the affected behavior when practical; a static code review alone is not runtime verification.
- Inspect `git diff` and `git status` for accidental, generated, secret, or out-of-scope changes.
- Reconcile documentation with actual behavior.
- Report exactly what was verified and how. Clearly label anything not run, not observed, or otherwise unverified.

## Quality expectations

- Target Python 3.12+ once implementation begins.
- Use type hints and focused, readable modules.
- Keep domain rules independent from CLI, browser automation, filesystem layout, and vendor APIs.
- Tests should cover public behavior and failure paths without depending on network access by default.
- Failures must be actionable and must not silently become passes.
- Security-sensitive values and application secrets must never be captured by default in evidence artifacts.

Run the development checks from the repository root after installing the development extra with `python -m pip install -e ".[dev]"`:

```console
pytest
ruff check .
mypy src tests
```

The current milestone and its completion criteria are defined in `docs/ROADMAP.md`. When instructions conflict, preserve acceptance integrity and the declared v0.1 scope, then surface the conflict rather than guessing.
