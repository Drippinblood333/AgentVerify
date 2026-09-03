# DoneWitness

[![CI](https://github.com/Drippinblood333/DoneWitness/actions/workflows/ci.yml/badge.svg)](https://github.com/Drippinblood333/DoneWitness/actions/workflows/ci.yml)

> **v0.1.0 scope.** DoneWitness is an early CLI-first release
> for locally runnable web applications. It can run one complete local verification flow, record
> source provenance when Git is available, bind versioned receipts to persisted evidence, inspect
> an existing run for integrity mismatches, and optionally run an application through a controlled
> local Docker isolation baseline or from one disposable selected-revision Git worktree.

Licensed under [Apache-2.0](LICENSE).

DoneWitness is a deterministic acceptance verifier for AI-written web software. It runs a frozen,
human-reviewed verification plan against the actual application and produces evidence-backed
`PASS`, `FAIL`, or `UNKNOWN` receipts. No judge model is required for its deterministic verdict
path.

Coding agents can produce convincing code and confident completion reports while missing an edge case, weakening a test, or leaving a browser flow broken at runtime. DoneWitness is designed to evaluate a frozen set of acceptance criteria by executing observable checks and collecting evidence such as command output, test results, screenshots, browser logs, and HTTP responses.

Its guiding ideas are:

- **Do not trust “done.” Verify it.**
- **Execution over opinion.**
- **Evidence over confidence.**
- **Use `UNKNOWN` when the evidence cannot justify `PASS` or `FAIL`.**

## Development status

v0.1.0 includes:

- an installable Python 3.12–3.14 package;
- `donewitness --help` and `donewitness --version`; and
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
  digest, post-snapshot plan-drift warnings, and read-only run-directory inspection; and
- an opt-in Docker isolation baseline with fixed filesystem, environment, privilege, network,
  resource, and cleanup controls, plus receipt v3 execution metadata for both direct and Docker
  runs; and
- optional local-revision verification in an exact detached disposable Git worktree, with caller
  state preservation, source-mutation detection, exact cleanup confirmation, and receipt v4 source
  selection and plan-source metadata.

The CLI executes the supplied application argv locally, without a shell, and gives every criterion a
fresh browser context. Artifacts live beneath a caller-supplied run directory using portable
relative paths. A browser outcome becomes conclusive only after its referenced evidence, including
the baseline browser observation, passes path, size, and SHA-256 checks.

Install the public distribution, then install Playwright-managed Chromium for browser verification:

```console
python -m pip install donewitness
python -m playwright install chromium
donewitness --version
```

The product is DoneWitness. Its PyPI distribution, Python import package, console command, and
`python -m` package name all use the single lowercase identity `donewitness`. The equivalent module
entry point is `python -m donewitness`.

For contributor/source installation, clone the repository and use:

```console
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

## Clean-checkout demo

From PowerShell, after installation, run the maintained Plan v2 and standard-library sample app:

```console
donewitness verify `
  --plan examples/greeting.plan.json `
  --base-url http://127.0.0.1:8765 `
  --run-dir .donewitness/demo-run `
  --app-command python examples/greeting_app.py
```

For POSIX shells, replace the PowerShell backticks with `\`. `--app-command` consumes the remaining
argv and must be the final DoneWitness option. A custom port therefore looks like:

```console
donewitness verify --plan examples/greeting.plan.json --base-url http://127.0.0.1:9000 --run-dir .donewitness/demo-run-9000 --app-command python examples/greeting_app.py --port 9000
```

The successful demo prints paths to `receipt.txt`, `receipt.json`, and
`evidence-manifest.json`, then exits after terminating the sample application. The run directory
must be nonexistent or empty; DoneWitness never overwrites a prior run.

For automation, place `--output-format json` before the final `--app-command`. Stdout then contains
one compact JSON object for a finalized trustworthy bundle, while warnings remain on stderr:

```console
donewitness verify --plan examples/greeting.plan.json --base-url http://127.0.0.1:8765 --run-dir .donewitness/json-demo --output-format json --app-command python examples/greeting_app.py
```

The summary reports the verdict, completion state, exit code, receipt schema, and resolved receipt
and manifest paths. See the generic CI contract for safe handling of nonzero exits.

Direct execution remains the default: omitting `--isolation` is equivalent to
`--isolation none`. It has the same M7 lifecycle and endpoint-attribution behavior and does not
require Docker. New real verification runs emit receipt v4 with `isolation_mode: "none"` and
`source_selection.mode: "current_worktree"`.

## Optional selected-revision verification

Use `--revision` before the final `--app-command` to verify one commit already present in the local
repository without switching or cleaning the caller worktree:

```console
donewitness verify \
  --plan examples/greeting.plan.json \
  --base-url http://127.0.0.1:8765 \
  --run-dir ../donewitness-runs/greeting-revision \
  --revision HEAD~1 \
  --app-command python examples/greeting_app.py --port 8765
```

DoneWitness resolves the selector once to a lowercase 40-character commit ID, rejects revisions
containing submodules/gitlinks, and creates a detached worktree under the system temporary
directory with checkout hooks redirected to an empty hooks directory. This necessarily creates a
temporary administrative worktree registration in the caller repository. The command runs with
that worktree as its explicit working directory; direct mode can write that disposable source,
while Docker mode binds the same selected root read-only. The plan is still loaded and frozen
from the original caller path before worktree creation, and the run directory must remain outside
the disposable source.

After application cleanup DoneWitness checks whether the disposable source became dirty, then
removes and confirms only that exact worktree before finalizing evidence and receipt v4. Source
mutation or unconfirmed worktree cleanup makes a non-FAIL run incomplete/`UNKNOWN` with exit `3`;
a real browser assertion `FAIL` remains `FAIL`. Git is required only when `--revision` is supplied.
No fetch, pull, clone, global prune, checkout, reset, or caller-worktree mutation is performed.
The original HEAD, index, staged changes, unstaged changes, untracked files, and working files are
excluded from the selected source and preserved. The local Git executable and local repository/user
configuration remain trusted: disabling hooks is not a sandbox for arbitrary filters or other Git
configuration, and DoneWitness does not manage Git LFS hydration.

## Optional Docker isolation baseline

Docker mode is explicit and requires Docker Engine server 28 or newer in Linux-container mode over
a local Unix socket or named pipe; remote Docker endpoints are rejected. The selected image must already exist locally; DoneWitness inspects it, records its concrete local
`sha256:` image ID, runs that ID with `--pull never`, and never pulls or builds an image. If needed,
the user or CI can make the maintained image available first:

```console
docker pull python:3.12-slim
```

Choose a run directory outside the current source root, then run the same plan and application:

```console
donewitness verify \
  --plan examples/greeting.plan.json \
  --base-url http://127.0.0.1:8765 \
  --run-dir ../donewitness-runs/greeting-docker \
  --isolation docker \
  --isolation-image python:3.12-slim \
  --app-command python examples/greeting_app.py --host 0.0.0.0 --port 8765
```

`--app-command` remains explicit argv and must remain the final DoneWitness option. Docker mode
requires an exact `127.0.0.1` base-url host and explicit TCP port. The application inside the
container must listen on `0.0.0.0` at that same port; DoneWitness publishes only that port to the
host's `127.0.0.1`.

The fixed `donewitness-docker-baseline-v1` profile:

- mounts the explicit selected execution root at `/workspace` read-only, disables recursive
  propagation of nested host submounts, and uses `/workspace` as the container working directory;
- rejects a filesystem/drive-root source, an unsafe comma-delimited mount path, an in-source run
  directory, and images declaring Docker `VOLUME` paths;
- uses a read-only image/root filesystem and read-only source bind, with explicit ephemeral writable
  storage at a private 64 MiB `/tmp` tmpfs and Docker's 64 MiB `/dev/shm`; sets `HOME=/tmp`, creates
  no writable host bind, and accepts no Docker volume;
- runs as numeric user `65534:65534`, drops all Linux capabilities, enables
  `no-new-privileges`, disables the image healthcheck, and explicitly replaces the image entrypoint
  with the reviewed application executable;
- forwards no arbitrary host environment variables or secrets and mounts neither host home paths
  nor the Docker socket;
- applies 512 MiB memory, 1.0 CPU, 256 PID, 64 MiB `/dev/shm`, and 64 MiB `/tmp` limits; and
- creates one uniquely named internal bridge network and one container; if Docker retains but does
  not activate the requested mapping for an internal-only bridge, a bounded stdlib TCP relay binds
  only the exact host-loopback port and forwards only to that container IP/port; and
- stops the relay/container, inspects, force-removes when necessary, and confirms removal of the
  exact managed resources.

This is an optional Docker isolation baseline that reduces host exposure. It is not a VM or proof
that arbitrary malicious code cannot affect trusted infrastructure. Docker Engine or Docker
Desktop, the Linux VM/kernel/runtime, and the host remain trusted. An internal Docker bridge is
intended to remove normal external connectivity, but runtime-specific Docker host/gateway services
may remain reachable. M8 provides no per-destination egress rules, host-firewall management, proxy
allowlists, DNS filtering, cloud-metadata firewall, image signatures, registry authenticity,
attestation, or remote execution. The internal network constrains the application container, not the
host-side Playwright Chromium process; untrusted page content may still initiate browser-side
third-party network requests. Docker/runtime also owns standard virtual and special filesystem
mounts, so this profile does not claim complete filesystem immutability or VM-grade isolation.

Invalid or unsupported Docker requests fail with exit `2` before application startup and never
downgrade to direct execution. Failures after a valid preflight, including early container exit,
readiness timeout, Docker runtime loss, or unconfirmed cleanup, produce reviewable incomplete
output and exit `3` unless a real browser assertion `FAIL` was already established.

Inspect the completed bundle without rerunning the application or Chromium:

```console
donewitness inspect --run-dir .donewitness/demo-run
```

A valid receipt-v2, receipt-v3, or receipt-v4 bundle reports its historical verdict and `Integrity: OK`. Invalid inspection
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

Receipt v2, v3, and v4 record the SHA-256 of the exact persisted manifest bytes. This detects accidental
modification, stale or partially copied files, and unsynchronized artifact replacement when the
receipt is trusted. It does not establish authenticity: an attacker able to modify the receipt,
manifest, and artifacts can recompute all unkeyed hashes consistently.

Without `--revision`, Git provenance is captured read-only from the invocation directory using
bounded Git commands. DoneWitness remains usable when Git is absent or the directory is outside a
repository. When `dirty_worktree` is true, the recorded HEAD revision does not uniquely identify
the current-worktree source bytes. With `--revision`, provenance instead identifies the exact clean
disposable source commit while receipt v4 separately records caller HEAD/dirty state, the explicit
post-run source state (`clean`, `dirty`, or `unknown`), and cleanup confirmation. An unavailable
status inspection is recorded as `unknown`; it is not falsely reported as an application mutation.

Receipt v3 preserves receipt-v2 environment, provenance, manifest binding, criteria, and verdict
semantics while adding structured execution metadata. Direct runs record `isolation_mode: "none"`.
Docker runs additionally record the fixed profile name, Docker server version, supplied mutable
image reference, and resolved local image ID. The ID identifies the exact local image used; it is
not a registry signature, provenance attestation, or authenticity claim. Historical receipt v1 and
v2 files remain loadable. Receipt v4 preserves all v3 fields and adds discriminated source
selection plus repository-relative-or-external plan-source metadata. Historical v2/v3/v4 bundles
remain inspectable; external plans never expose an absolute plan path in the receipt.

The plan object loaded before execution remains the authoritative frozen snapshot. After run
finalization DoneWitness canonically reloads the original plan path; a semantic change, deletion, or
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
  same value in `--base-url` and after the final `--app-command ... --port` argument. DoneWitness
  refuses to start the command when it cannot safely attribute an already-listening endpoint.
- **Application never becomes ready:** check the configured host/port and inspect the process-log
  artifact; increase `--startup-timeout-ms` only when startup legitimately needs more time.
- **Run directory already contains files:** choose a new directory or explicitly empty the intended
  directory before starting. DoneWitness will not overwrite it.
- **Docker is unavailable or too old:** Docker mode requires a reachable Docker Engine 28+ server
  using Linux containers. Direct mode remains available without Docker.
- **Docker image is missing:** pull or build the selected image explicitly before verification;
  DoneWitness does not pull it.
- **Docker run directory is rejected:** choose a new or empty path outside the current source root.
- **Docker application never becomes ready:** ensure it binds `0.0.0.0` at the exact port used in
  the `127.0.0.1` base URL.
- **Revision is rejected:** ensure the selector identifies one commit already available locally and
  that the selected tree contains no submodule/gitlink entries.
- **Revision cleanup is unconfirmed:** treat exit `3` as incomplete, inspect the receipt limitation,
  and use `git worktree list` to review the exact remaining registration; DoneWitness does not run a
  repository-wide prune.

The first release is deliberately limited to **locally runnable web applications** and a CLI-first experience. It will not be a hosted platform, coding-agent orchestrator, team dashboard, or general-purpose mobile and desktop testing system.

## Project documents

- [Product definition](docs/PRODUCT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Generic CI contract](docs/CI.md)
- [Security and privacy boundaries](docs/SECURITY_AND_PRIVACY.md)
- [Failure modes](docs/FAILURE_MODES.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Packaging](docs/PACKAGING.md)
- [Versioning policy](docs/VERSIONING.md)
- [Release runbook](docs/RELEASE.md)
- [v0.1.0 release notes](docs/releases/v0.1.0.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Contributor and coding-agent rules](AGENTS.md)

The documented CLI exit codes and versioned plan, receipt, evidence-manifest, and machine-output
formats are the supported compatibility surfaces. Undocumented Python internals are not a stable
API before 1.0.
