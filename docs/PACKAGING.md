# Packaging and artifact smoke tests

AgentVerify remains at `0.1.0.dev0` during M10. Build exactly one pure-Python wheel and one source
distribution from the current source tree:

```console
python -m pip install -e ".[dev]"
python -m build
python scripts/check_distribution.py dist
```

Expected files are `agentverify-0.1.0.dev0-py3-none-any.whl` and
`agentverify-0.1.0.dev0.tar.gz`. The content check requires package source, distribution metadata,
the console entry point, the backend-produced license, and buildable sdist source while rejecting
obvious checkout, evidence, environment, secret-named, and temporary-worktree debris.

Run isolated artifact verification with:

```console
python scripts/smoke_distribution.py dist/agentverify-0.1.0.dev0-py3-none-any.whl
python scripts/smoke_distribution.py dist/agentverify-0.1.0.dev0.tar.gz
```

The helper creates its own temporary virtual environment and out-of-checkout workspace, installs
the artifact without the dev extra, runs `pip check`, verifies the import path/version, installs
Chromium, executes the maintained direct sample with JSON output, and checks the resulting receipt
v4 bundle with `agentverify inspect`. Use `--cli-only` for the Windows sdist minimum smoke.

Playwright Chromium, Docker images, Docker itself, and the Python interpreter are not bundled. The
user installs Chromium separately; Docker is an optional external capability; Git is optional
unless `--revision` is used. Distribution files contain the AgentVerify Python package, not run
evidence. M10 builds and retains short-lived CI review artifacts only—it does not publish. Version,
tag, and publication decisions belong to M11.
