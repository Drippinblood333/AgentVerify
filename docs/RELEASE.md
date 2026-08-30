# Release runbook

This runbook separates release preparation from the explicit public release decision. PyPI names
are not reserved by repository configuration or pending Trusted Publisher records.

## Owner configuration before release

- Establish the private security reporting channel required by [SECURITY.md](../SECURITY.md).
- Confirm `agentverify-evidence` remains available on PyPI.
- Configure a PyPI project or pending Trusted Publisher for distribution `agentverify-evidence`,
  GitHub owner `Drippinblood333`, repository `AgentVerify`, workflow `release.yml`, environment
  `pypi`.
- Configure the GitHub `pypi` Environment with manual approval where practical.
- The workflow explicitly disables the publishing action's optional digital attestations; release
  `SHA256SUMS` remain ordinary download-integrity checksums, not signatures or attestation.
- All external actions in the release workflow are pinned to reviewed immutable commit SHAs; resolve
  and review upstream release refs before changing those pins.

## Human release ceremony

1. Verify the independently reviewed release-prep head and merge its PR.
2. Require the first post-merge CI run for the exact new `main` commit to pass.
3. Confirm the private security reporting channel and PyPI namespace/publisher configuration.
4. Make the explicit release decision.
5. Create annotated tag `v0.1.0` at the exact reviewed release commit and push only that tag.
6. Manually dispatch `.github/workflows/release.yml` from that commit with input `v0.1.0`.
7. Verify Linux/Windows artifact smoke, PyPI project/version/files, GitHub Release notes/files, and
   `SHA256SUMS`.
8. In a clean machine or venv run:

   ```console
   python -m pip install agentverify-evidence==0.1.0
   python -m playwright install chromium
   agentverify --version
   ```

9. Run the maintained greeting sample and inspect the resulting receipt.

## Failure and rollback semantics

- Build or verification failure: nothing publishes.
- PyPI publication failure: the GitHub Release job does not run.
- PyPI success followed by GitHub Release failure: report a partial release immediately. Do not
  rebuild or attempt to overwrite the immutable PyPI version. Retry only GitHub Release creation
  with the same verified artifact bytes when their checksums and provenance are intact.
- A wrong public artifact/version requires a new corrective version; published PyPI files cannot be
  replaced.
