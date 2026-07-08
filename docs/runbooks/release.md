# Runbook: cutting a release

Releases are tag-driven. Pushing a `vX.Y.Z` tag triggers
`.github/workflows/release.yml`, which verifies the tag, builds, and creates a
GitHub Release with wheel + sdist + `SHA256SUMS`.

## Pre-release checklist

- [ ] `main` is green in CI.
- [ ] Working tree is clean and on the commit you intend to release.

## Version bump

- [ ] Update `__version__` in `agenteval.py` to the new `X.Y.Z`.
- [ ] This is the single source of truth; the tag must match it exactly (the
      release workflow enforces this via `scripts/check_version.py`).

## Changelog

- [ ] Move the `Unreleased` items in `CHANGELOG.md` under a `vX.Y.Z` heading (or
      add one) describing what shipped.

## Local release check

- [ ] Run the full local gate:

  ```sh
  sh scripts/release-check.sh
  ```

  This runs pytest, ruff, `uv build`, dist verification, checksum generation, and
  the installed-wheel smoke test. Fix anything red before tagging.

## Tag and push

```sh
git commit -am "vX.Y.Z"          # version bump + changelog
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

## Verify the GitHub Release

- [ ] The `Release` workflow run is green.
- [ ] The tag/version check passed (no mismatch).
- [ ] The Release page has three assets: `*.whl`, `*.tar.gz`, `SHA256SUMS`.
- [ ] Verify checksums locally after downloading:

  ```sh
  sha256sum -c SHA256SUMS
  ```

## Optional: publish to PyPI / TestPyPI

Only after a PyPI Trusted Publisher and the `pypi` environment are configured
(see [publishing.md](../publishing.md)):

- [ ] TestPyPI first, then PyPI — either via the manual **Publish** workflow
      (`workflow_dispatch`, type `publish` to confirm) or `uv publish` locally.
- [ ] Install from the index in a clean venv and confirm `agenteval --version`.

## Post-release checklist

- [ ] Delete the merged `release/vX.Y.Z` branch and any other merged short-lived
      branches (the tag + GitHub Release are the permanent record). See
      [repo-governance.md](../repo-governance.md#branch-lifecycle).
- [ ] Add a fresh `Unreleased` section to `CHANGELOG.md`.
- [ ] Update `claude.md` (current version, last released commit).
- [ ] Announce if you have users. If something is wrong, see
      [rollback.md](rollback.md).
