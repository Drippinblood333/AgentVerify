# Failure modes

`FAIL` is reserved for a deterministic assertion contradiction. Configuration defects,
infrastructure failures, incomplete cleanup, and integrity uncertainty are not converted to
application failures.

| Condition | Classification | Receipt normally exists | Verdict | Exit | Reviewer action |
| --- | --- | --- | --- | ---: | --- |
| Invalid Plan | invalid input | no | none | 2 | fix schema/content before retrying |
| Plan v1 execution request | unsupported executable input | no | none | 2 | supply executable Plan v2 |
| Stale/pre-existing endpoint | attribution preflight failure | no | none | 2 | select a closed loopback port |
| App start failure | operational | yes | `UNKNOWN` | 3 | inspect process evidence and command |
| Readiness timeout | operational | yes | `UNKNOWN` | 3 | inspect logs, endpoint, and timeout |
| Browser infrastructure error | operational | yes | `UNKNOWN` | 3 | repair Chromium/runtime and retry |
| Deterministic assertion contradiction | verification result | yes | `FAIL` | 1 | fix the application or acceptance criteria through review |
| Application early exit | operational | yes | `UNKNOWN` | 3 | inspect process log and lifecycle |
| Evidence persistence/final integrity failure | integrity/operational uncertainty | no trusted result; files may exist | none trusted | 3 | preserve for diagnosis; do not consume as a normal result |
| Cleanup failure | incomplete lifecycle | yes when finalizable | `UNKNOWN`, or existing `FAIL` remains dominant | 3 or 1 | inspect exact managed resources before cleanup |
| Plan drift warning | post-snapshot warning | yes | frozen verdict unchanged | 0, 1, or 3 | compare frozen digest with current plan |
| Docker preflight failure | invalid/unsupported configuration | no | none | 2 | fix daemon, image, URL, source, or limits |
| Docker runtime failure | operational | yes when finalizable | `UNKNOWN` | 3 | inspect Docker diagnostics and managed resources |
| Revision resolution failure | invalid local revision input | no | none | 2 | use an existing local commit selector |
| Gitlink/submodule rejection | unsupported source input | no | none | 2 | remove gitlinks or verify a supported tree |
| Disposable worktree source dirty | source-integrity uncertainty | yes | `UNKNOWN`, or `FAIL` remains dominant | 3 or 1 | review source mutation evidence |
| Worktree source state unknown | source-integrity uncertainty | yes | `UNKNOWN`, or `FAIL` remains dominant | 3 or 1 | inspect repository/worktree manually |
| Worktree cleanup uncertainty | cleanup uncertainty | yes when finalizable | `UNKNOWN`, or `FAIL` remains dominant | 3 or 1 | inspect `git worktree list`; avoid global prune |
| Interrupt | incomplete execution | usually yes if finalization succeeds | `UNKNOWN` | 3 | inspect bundle and confirm cleanup |
| Inspect input error | invalid inspection input | existing target is not accepted | none | 2 | provide a supported complete run directory |
| Inspect integrity mismatch | integrity uncertainty | existing receipt exists but is untrusted | historical verdict not reclassified | 3 | compare receipt, manifest, and artifacts |

Exit `3` is never a pass. A finalized reviewable `UNKNOWN` may provide machine JSON, while a failure
that prevents trusted finalization or final self-inspection provides diagnostics only.
