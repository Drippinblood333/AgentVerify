# Changelog

All notable user-visible changes to DoneWitness are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses the pre-1.0 policy in
[docs/VERSIONING.md](docs/VERSIONING.md).

## [Unreleased]

## [0.1.0] - 2026-09-01

### Added

- Versioned acceptance Plan v1/v2 loading and deterministic browser procedures.
- Playwright Chromium verification with `PASS`, `FAIL`, and `UNKNOWN` verdicts.
- Bounded evidence manifests, proof receipts v1–v4, and read-only integrity inspection.
- Git provenance and requested-revision verification in disposable worktrees.
- An optional Linux-qualified Docker isolation baseline with explicit boundaries and cleanup.
- Stable CLI exit codes and machine-output schema v1 for CI consumption.
- Clean wheel/sdist verification on qualified Linux and Windows/Python matrices.

### Limitations

- Locally runnable web applications and explicit deterministic procedures only.
- No LLM-generated criteria or autonomous browser exploration in the verdict path.
- Docker is not VM-grade isolation; no cryptographic attestation is provided.
- macOS and Windows Docker are not release-qualified for v0.1.
