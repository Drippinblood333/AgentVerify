# Security Policy

## Supported versions

| Version | Support |
| --- | --- |
| 0.1.x | Supported |
| Older or unreleased development snapshots | Best effort / unsupported |

No response or remediation SLA is promised.

## Scope

Security-sensitive areas include direct application execution, the optional Docker isolation
baseline, Git revision and disposable-worktree handling, evidence privacy and integrity, path
containment, and process/container cleanup. Review
[docs/SECURITY_AND_PRIVACY.md](docs/SECURITY_AND_PRIVACY.md) before running untrusted code.

## Reporting a vulnerability

Do not disclose exploit details, secrets, or sensitive evidence in a public GitHub issue.

**OWNER ACTION REQUIRED — private security reporting channel:** GitHub Private Vulnerability
Reporting is not currently enabled for this repository, and no approved private contact is recorded.
The owner must enable a real private reporting path and replace this action item with exact reporting
instructions before approving the public v0.1.0 release. Until then, the public release is blocked;
this document intentionally does not invent an email address or direct reporters to public disclosure.
