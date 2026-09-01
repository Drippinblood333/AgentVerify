# Packaging and artifact smoke tests

AgentVerify uses the PyPI distribution name `agentverify-evidence` while retaining the
`agentverify` import package and console command. Build exactly one pure-Python wheel and one source
distribution from the current source tree:

```console
python -m pip install -e ".[dev]"
python -m build
python scripts/check_distribution.py dist
```

Expected backend-produced files are `agentverify_evidence-0.1.0-py3-none-any.whl` and
`agentverify_evidence-0.1.0.tar.gz`. The content check requires the `agentverify-evidence` name,
version `0.1.0`, Apache-2.0 license expression/file, project URLs, package source, distribution metadata,
the console entry point, the backend-produced license, and buildable sdist source while rejecting
obvious checkout, evidence, environment, secret-named, and temporary-worktree debris.

Run isolated artifact verification with:

```console
python scripts/smoke_distribution.py dist/agentverify_evidence-0.1.0-py3-none-any.whl
python scripts/smoke_distribution.py dist/agentverify_evidence-0.1.0.tar.gz
```

The helper creates its own temporary virtual environment and out-of-checkout workspace, installs
the artifact without the dev extra, runs `pip check`, verifies the import path/version/metadata,
and invokes the installed environment's `agentverify` console entrypoint for `--version`, `--help`,
the maintained direct Chromium sample with JSON output, and receipt-v4 inspection. It does not use
a repository-local console script. Use `--cli-only` for the Windows sdist minimum smoke.

Playwright Chromium, Docker images, Docker itself, and the Python interpreter are not bundled. The
user installs Chromium separately; Docker is an optional external capability; Git is optional
unless `--revision` is used. Distribution files contain the AgentVerify Python package, not run
evidence. Normal CI builds and retains short-lived review artifacts only; it does not publish.
Publication is isolated to the manually dispatched release workflow and explicit release ceremony.

The unrelated `agentverify` PyPI distribution is not this project. Public installation is:

```console
python -m pip install agentverify-evidence
```
