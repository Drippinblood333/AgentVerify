# Release runbook

This runbook separates release preparation from the explicit public release decision. PyPI names
are not reserved by repository configuration or pending Trusted Publisher records.

## Hosted release prerequisites

The release-preparation GitHub owner gates have been configured and verified:

- GitHub Private Vulnerability Reporting is enabled. Researchers must use the private reporting
  path documented in [SECURITY.md](../SECURITY.md).
- The GitHub Environment named `pypi` exists. Required reviewers are not configured, prevent
  self-review is not configured, the wait timer is `0`, and deployment restrictions are not
  configured. Consequently, no manual environment reviewer approval is enforced; this is an owner
  decision and release risk.
- The final PyPI Trusted Publisher is not yet configured or verified for the DoneWitness identity.
  After the GitHub repository is separately renamed, replace the existing pending publisher and
  positively verify this exact mapping in authenticated PyPI hosted evidence: distribution
  `donewitness`, GitHub owner `Drippinblood333`, repository `DoneWitness`, workflow `release.yml`,
  environment `pypi`. Repository configuration or a pending publisher does not reserve a PyPI
  project name.
- Immediately before publication, reconfirm both that `donewitness` remains available under
  normalized PyPI project-name semantics and that the final publisher has the exact mapping above.
- The workflow explicitly disables the publishing action's optional digital attestations; release
  `SHA256SUMS` remain ordinary download-integrity checksums, not signatures or attestation.
- All external actions in the release workflow are pinned to reviewed immutable commit SHAs; resolve
  and review upstream release refs before changing those pins.

## Human release ceremony

1. Verify the independently reviewed release-prep head and merge its PR.
2. Require the first post-merge CI run for the exact new `main` commit to pass.
3. Complete the separately authorized GitHub repository rename, then replace and positively verify
   the final Trusted Publisher mapping described above.
4. Reconfirm the private security reporting channel, PyPI namespace, and exact publisher
   configuration.
5. Make the explicit release decision.
6. Create annotated tag `v0.1.0` at the exact reviewed release commit and push only that tag.
7. Manually dispatch `.github/workflows/release.yml` from that commit with input `v0.1.0`.
8. Verify Linux/Windows artifact smoke, PyPI project/version/files, GitHub Release notes/files, and
   `SHA256SUMS`.
9. In a clean machine or venv run:

   ```console
   python -m pip install donewitness==0.1.0
   python -m playwright install chromium
   donewitness --version
   ```

10. Run the maintained greeting sample and inspect the resulting receipt.

## Failure and rollback semantics

- Build or verification failure: nothing publishes.
- PyPI publication failure: the GitHub Release job does not run.
- PyPI success followed by GitHub Release failure: report a partial release immediately. Do not
  rebuild or attempt to overwrite the immutable PyPI version. Retry only GitHub Release creation
  with the same verified artifact bytes when their checksums and provenance are intact.
- A wrong public artifact/version requires a new corrective version; published PyPI files cannot be
  replaced.
