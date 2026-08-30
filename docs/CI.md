# Generic CI contract

AgentVerify's CI interface is the command line, process exit status, and finalized run directory.
It does not depend on a CI vendor. Install the package, install Playwright Chromium, run
`agentverify verify --output-format json ...`, capture stdout and the exit status, and retain the
referenced run directory only when the CI owner's privacy policy permits it.

The stable exit meanings are:

| Exit | Meaning | Gate result |
| ---: | --- | --- |
| 0 | verified `PASS` | pass |
| 1 | deterministic verification `FAIL` | fail the product gate |
| 2 | invalid invocation, input, or CI/user configuration | fix the job configuration |
| 3 | `UNKNOWN`, incomplete execution, integrity uncertainty, or infrastructure failure | fail or quarantine; never treat as pass |

A finalized `PASS`, `FAIL`, or reviewable `UNKNOWN` writes one compact JSON object plus a trailing
newline to stdout. Preflight/configuration errors, operational failures that cannot finalize a
bundle, and final integrity failures emit no normal JSON summary. Diagnostics and plan-drift
warnings go to stderr.

## POSIX shell

This example intentionally captures nonzero statuses before restoring fail-fast behavior:

```sh
python -m pip install agentverify-evidence
python -m playwright install chromium

set +e
agentverify verify \
  --plan verification.plan.json \
  --base-url http://127.0.0.1:8765 \
  --run-dir agentverify-run \
  --output-format json \
  --app-command python app.py > agentverify-summary.json
agentverify_status=$?
set -e

if [ -s agentverify-summary.json ]; then
  python -c 'import json; d=json.load(open("agentverify-summary.json", encoding="utf-8")); print(d["receipt_json_path"])'
fi

case "$agentverify_status" in
  0) echo "AgentVerify PASS" ;;
  1) echo "AgentVerify deterministic FAIL" >&2 ;;
  2) echo "AgentVerify configuration/input error" >&2 ;;
  3) echo "AgentVerify inconclusive, incomplete, or integrity uncertainty" >&2 ;;
  *) echo "Unexpected AgentVerify exit: $agentverify_status" >&2 ;;
esac
exit "$agentverify_status"
```

## PowerShell

```powershell
python -m pip install agentverify-evidence
python -m playwright install chromium

$oldPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
agentverify verify `
  --plan verification.plan.json `
  --base-url http://127.0.0.1:8765 `
  --run-dir agentverify-run `
  --output-format json `
  --app-command python app.py > agentverify-summary.json
$agentverifyStatus = $LASTEXITCODE
$ErrorActionPreference = $oldPreference

if ((Test-Path agentverify-summary.json) -and
    (Get-Item agentverify-summary.json).Length -gt 0) {
  $summary = Get-Content -Raw agentverify-summary.json | ConvertFrom-Json
  Write-Host $summary.receipt_json_path
}

switch ($agentverifyStatus) {
  0 { Write-Host 'AgentVerify PASS' }
  1 { Write-Error 'AgentVerify deterministic FAIL' }
  2 { Write-Error 'AgentVerify configuration/input error' }
  3 { Write-Error 'AgentVerify inconclusive, incomplete, or integrity uncertainty' }
  default { Write-Error "Unexpected AgentVerify exit: $agentverifyStatus" }
}
exit $agentverifyStatus
```

`output_schema_version` is `1`. Its required fields are `output_schema_version`, `verdict`,
`completed`, `exit_code`, `receipt_schema_version`, `receipt_json_path`, `receipt_text_path`, and
`evidence_manifest_path`. Paths are resolved native absolute paths. Consumers must ignore unknown
future fields and must use schema versions rather than internal Python classes. CLI-output schema
versioning is independent from receipt schema versioning.

For untrusted application code, use a disposable worker without unrelated credentials, grant the
job minimum permissions, and treat retained evidence as sensitive. Prefer Docker isolation where
release-qualified, but do not treat it as a perfect hostile-code sandbox or assume that AgentVerify
makes a shared privileged runner safe. This repository uploads only wheel and sdist review artifacts;
it does not automatically upload verification evidence.
