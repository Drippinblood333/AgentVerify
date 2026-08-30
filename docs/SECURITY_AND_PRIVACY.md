# Security and privacy boundaries

This document describes runtime behavior for the v0.1 line. The public vulnerability-reporting
policy and its current owner-action blocker are in the repository root [SECURITY.md](../SECURITY.md).

## Execution modes

In direct mode, the application command runs with the caller user's permissions. It can access the
caller's filesystem, environment, and network. Do not execute hostile code this way on a valuable
persistent host.

Docker isolation reduces host exposure but is not VM-grade isolation. AgentVerify trusts Docker,
its kernel/runtime or Linux VM, and the host. Chromium remains host-side, and loaded pages can make
third-party network requests. Docker-managed host or gateway endpoints may remain reachable on
some runtimes. Docker isolation is release-qualified only on Linux for v0.1.

For selected revisions, the local Git executable and local configuration, filters, and repository
metadata remain trusted. Checkout hooks are redirected, but the worktree is not a Git sandbox.
Submodules/gitlinks are rejected, and AgentVerify never fetches, pulls, or clones.

## Evidence and privacy

Evidence can contain sensitive application content. Screenshots and traces are especially
privacy-sensitive. Text redaction is best effort, while bounded network summaries intentionally
omit bodies, headers, cookies, queries, and fragments. The caller controls run-directory retention;
AgentVerify does not automatically upload verification evidence to CI artifacts.

SHA-256 values indicate byte integrity, not signatures, authentication, or attestation. An attacker
who can rewrite the receipt, manifest, and artifacts can recompute their unkeyed hashes.

## CI guidance

For untrusted application code:

- prefer disposable or ephemeral CI workers;
- place no unrelated credentials on the worker;
- keep CI permissions minimal;
- prefer Docker isolation where supported without treating it as a hostile-code sandbox; and
- treat retained evidence as potentially sensitive and upload it only under an explicit privacy
  policy.

AgentVerify does not make a shared privileged runner safe.
