# Packaging and artifact smoke tests

DoneWitness uses the PyPI distribution name `donewitness`, the Python import package
`donewitness`, and the `donewitness` console command. Build exactly one pure-Python wheel and one
source distribution from the current source tree:

```console
python -m pip install -e ".[dev]"
python -m build
python scripts/check_distribution.py dist
```

Expected backend-produced files are `donewitness-0.1.0-py3-none-any.whl` and
`donewitness-0.1.0.tar.gz`. The content check requires the `donewitness` name,
version `0.1.0`, Apache-2.0 license expression/file, project URLs, `donewitness` package
source, absence of both superseded import packages, distribution metadata, exactly one console
entry point, the backend-produced license, and buildable sdist source while rejecting checkout,
evidence, environment, secret-named, and temporary-worktree debris.

Run isolated artifact verification with:

```console
python scripts/smoke_distribution.py dist/donewitness-0.1.0-py3-none-any.whl
python scripts/smoke_distribution.py dist/donewitness-0.1.0.tar.gz
```

The helper creates its own temporary virtual environment and out-of-checkout workspace, installs
the artifact without the dev extra, runs `pip check`, verifies the `donewitness` import
path/version/metadata, rejects both superseded import packages, confirms the installed entry-point
metadata exposes only `donewitness = donewitness.cli:main`, and invokes that installed console
entrypoint for `--version`, `--help`,
the maintained direct Chromium sample with JSON output, and receipt-v4 inspection. It does not use
a repository-local console script. Use `--cli-only` for the Windows sdist minimum smoke.

Playwright Chromium, Docker images, Docker itself, and the Python interpreter are not bundled. The
user installs Chromium separately; Docker is an optional external capability; Git is optional
unless `--revision` is used. Distribution files contain the DoneWitness Python package, not run
evidence. Normal CI builds and retains short-lived review artifacts only; it does not publish.
Publication is isolated to the manually dispatched release workflow and explicit release ceremony.
Public installation is:

```console
python -m pip install donewitness
```
