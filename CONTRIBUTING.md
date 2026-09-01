# Contributing to DoneWitness

DoneWitness accepts focused changes that preserve its evidence-first verification boundary. Human
contributors should use this guide; coding agents must also follow [AGENTS.md](AGENTS.md).

## Development setup

DoneWitness supports Python 3.12, 3.13, and 3.14. From a source checkout:

```console
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Run the development checks from the repository root:

```console
python -m pytest
python -m ruff check .
python -m mypy src tests
git diff --check
```

Linux CI requires the real Docker suites with `DONEWITNESS_REQUIRE_DOCKER=1`, Docker Engine in
Linux-container mode, and the maintained `python:3.12-slim` fixture image. Windows Docker is not
release-qualified for v0.1.

## Pull requests

Keep each pull request small, explain its user-visible contract, and add or update focused tests.
Freeze acceptance criteria before verification. Tests and acceptance criteria are protected assets:
never remove, reinterpret, or weaken them merely to manufacture a `PASS`. Operational uncertainty
must remain visible as `UNKNOWN`, not be converted into success.

Before requesting review, run the relevant checks, exercise affected behavior where practical, and
inspect `git diff` and `git status` for generated files, secrets, or unrelated changes. Documentation-
only changes should still be checked for links, command accuracy, scope, and consistency with actual
behavior; state clearly when runtime tests were not needed.

## Security issues

Do not post exploit details or sensitive vulnerability reports in a public issue. Follow
[SECURITY.md](SECURITY.md) and use this repository's GitHub Private Vulnerability Reporting flow.
No security email address is inferred or invented here.
