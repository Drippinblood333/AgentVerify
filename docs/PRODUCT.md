# Product Definition

## Problem

AI coding agents often report implementation success using their own reasoning, tests, or summaries. Those claims are useful progress signals, but they are not independent proof that the original requirement works in a real application. A user still has to determine whether the right behavior was built, edge cases work, protected tests were weakened, and the browser, network, and server remain free of runtime failures.

The core problem is therefore a **verification gap** between an agent's completion claim and observable software behavior. AgentVerify closes that gap by running checks against acceptance criteria and producing a traceable verdict supported by captured evidence. It does not attempt to prove software correct in the formal sense.

## Target user

The primary v0.1 user is an individual developer or technical reviewer who:

- uses any AI coding agent to change a local web application;
- can state or review concrete acceptance criteria before verification;
- wants a fast, repeatable second opinion based on execution rather than another code-reading opinion; and
- is comfortable invoking a local CLI and reviewing artifacts.

AgentVerify verifies the resulting worktree, not the identity or transcript of the builder. A human, Codex, Claude Code, Cursor, or another tool can produce the code under test.

## Product principles

1. **Independent verification:** builder claims and builder-generated results are inputs, not trusted conclusions.
2. **Acceptance criteria first:** criteria are reviewed and frozen for a run before checks execute.
3. **Evidence first:** every conclusive result points to evidence that a reviewer can inspect.
4. **Three-state verdicts:** `PASS`, `FAIL`, and `UNKNOWN` distinguish success, observed violation, and insufficient verification.
5. **Deterministic core:** replayable procedures, explicit assertions, and stable aggregation decide v0.1 verdicts.
6. **Minimal local product:** solve one useful workflow before adding hosting, autonomy, or enterprise infrastructure.
7. **Safe evidence:** collection is bounded and avoids exposing secrets or unrelated user data by default.

## v0.1 scope

v0.1 will support one local repository containing a web application that can be started by a user-supplied command. The user provides a versioned verification plan with explicit acceptance criteria and deterministic browser checks. AgentVerify runs the checks locally, records per-criterion results, captures bounded evidence, and renders a human-readable and machine-readable proof receipt through a CLI.

The minimum user flow is:

1. The user checks out the implementation to verify.
2. The user authors or reviews a verification plan and freezes its acceptance criteria.
3. The user invokes AgentVerify with the plan and application start command.
4. AgentVerify validates the inputs, starts or connects to the local application, and runs declared checks.
5. Each criterion receives `PASS`, `FAIL`, or `UNKNOWN` plus evidence references.
6. AgentVerify aggregates the run and writes a proof receipt.
7. The user reviews the receipt and artifacts, then independently decides whether to accept the implementation.

## Non-goals

For v0.1, AgentVerify will not provide:

- mobile, native desktop, Kubernetes, or remote cloud application verification;
- a SaaS service, user accounts, billing, team dashboard, or enterprise authorization;
- multi-agent orchestration or control of Codex, Claude Code, Cursor, or other builders;
- a guarantee of correctness, security, accessibility, or complete test coverage;
- autonomous LLM judgment in the deterministic verdict path;
- automatically generated acceptance criteria as a prerequisite for use;
- a database, queue, distributed worker fleet, vector database, or RAG system;
- a universal test framework replacement.

Container isolation, Git worktrees, and CI integration are later hardening and integration milestones, not foundation requirements.

## Example workflow

For “implement password reset,” a reviewer defines criteria for requesting an email, rejecting invalid and expired tokens, changing the password, invalidating the old password, and preventing token reuse. A deterministic plan exercises those behaviors against the local application. If token reuse succeeds, the related criterion is `FAIL`, the overall receipt is `FAIL`, and the receipt links to the browser trace, relevant request/response summary, and server output. If the email flow cannot be observed in the configured environment, that criterion is `UNKNOWN`, not a fabricated success.

## Success criteria

The v0.1 product succeeds when a new user can:

- install it and understand the local-only security boundary;
- express and validate a small acceptance plan without vendor-specific metadata;
- verify a representative local web application using reproducible checks;
- receive stable `PASS`/`FAIL`/`UNKNOWN` results for each criterion;
- trace every conclusive result to useful, bounded evidence;
- rerun the same plan and distinguish application failures from verifier/infrastructure failures;
- produce a proof receipt suitable for human review and basic CI consumption; and
- complete the workflow without an LLM API, hosted AgentVerify service, or builder-specific integration.

Success is not measured by how confidently AgentVerify describes code. It is measured by whether another person can inspect what ran, what was observed, and why the verdict follows.
