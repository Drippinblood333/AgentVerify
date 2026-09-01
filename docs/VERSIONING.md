# Versioning policy

AgentVerify package versions follow a SemVer-style `MAJOR.MINOR.PATCH` scheme. The project is
pre-1.0: minor releases may change product behavior and unsupported Python internals. Permanent
stability is not promised before 1.0.

The documented CLI exit codes and versioned Plan, Receipt, Evidence Manifest, and machine-output
formats are stronger compatibility contracts than undocumented Python classes or functions. Their
schema versions evolve explicitly and independently from the package version.

Patch releases should preserve documented v0.1 contracts. If correcting unsafe or incorrect
behavior requires a documented contract change, the release notes must disclose the break and its
reason. Breaking documented behavior in any pre-1.0 release requires explicit release-note and
upgrade guidance.
