# v0.1 compatibility

This is the public v0.1 qualification contract. See [VERSIONING.md](VERSIONING.md) for the
pre-1.0 package and schema versioning policy.

| Area | v0.1 qualification |
| --- | --- |
| Python | 3.12, 3.13, and 3.14; distribution metadata `>=3.12,<3.15` |
| Linux host | direct, `--revision`, and Docker isolation release-tested |
| Windows host | direct and `--revision` release-tested; Docker isolation not release-qualified |
| macOS host | not release-qualified for v0.1 |
| Plans | v1 load/digest supported; v2 loadable and executable |
| Receipts | historical v1 loadable; v2/v3/v4 loadable; new runs emit v4 |
| Inspect | manifest-bound receipt v2/v3/v4 bundles |
| Evidence manifest | schema v1 |
| CLI | stable exit codes, retained text output, machine-output schema v1 |

Upgrading AgentVerify does not rewrite old run directories. Supported historical receipts remain
readable, and durable evidence bundles should be retained independently of the installed package
version. Consumers should branch on receipt and CLI-output schema versions and ignore unknown
future machine-summary fields. Undocumented Python classes and internals are not a stable API;
pre-1.0 versions may change unsupported internals.

Docker Desktop/named-pipe support code remains present, but v0.1 CI does not release-qualify Docker
execution on Windows. “Not release-qualified” is a testing/support statement, not a claim that the
platform is broken.
