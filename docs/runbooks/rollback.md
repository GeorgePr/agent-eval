# Runbook: rollback

## What rollback means for a CLI package

There's no running service to revert. "Rollback" means making sure users don't
install or rely on a known-bad version, and shipping a corrected one. The
mechanics differ for a GitHub Release vs. a PyPI release.

## Bad GitHub Release

The Release is just a tag plus attached artifacts, so it's recoverable:

- **No consumers yet:** mark the Release as **pre-release** (or delete it), delete
  the bad tag, fix the problem, and re-tag:

  ```sh
  git push --delete origin vX.Y.Z
  git tag -d vX.Y.Z
  # fix, commit, then re-tag as documented in release.md
  ```

- **Consumers may have pulled it:** do **not** silently reuse the tag. Mark the
  Release as pre-release, edit its notes to say it's known-bad, and ship a new
  patch (`vX.Y.Z+1`).

## Bad PyPI release

PyPI is unforgiving by design:

- **You cannot overwrite or re-upload a version**, and a yanked/deleted version's
  number can never be reused.
- You **can yank** a release (PyPI project page → Manage → yank). Yanking hides it
  from normal resolution while leaving it installable by exact pin — good for
  "don't pick this up by default."
- The fix is always **forward**: bump to the next patch, fix, and publish
  `vX.Y.Z+1`. Never try to "replace" the bad version.

## Revert vs. forward-fix

- **Forward-fix** (preferred): commit the fix, bump the patch version, cut a new
  release. Clean history, clear intent.
- **Revert the commit** when the bad change hasn't been released yet, or when the
  fastest safe path is to restore the previous known-good state before
  investigating.

Either way, the released version number moves forward — you never re-point an
existing release at new bits.

## Communicating known-bad versions

- Edit the bad GitHub Release notes to state it's known-bad and point to the fixed
  version.
- Yank the bad version on PyPI if it was published.
- Note it in `CHANGELOG.md` under the fixed version ("fixes a regression in
  vX.Y.Z").
- If you have a security angle, follow [SECURITY.md](../../SECURITY.md).
