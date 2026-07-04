# Publishing

Publishing to PyPI is **optional and off by default**. AgentEval's releases are
GitHub Releases with attached wheel/sdist; PyPI is an extra step you opt into.

When you do publish, use **Trusted Publishing (OIDC)** — never API tokens.

## Why avoid API tokens

A PyPI API token stored as a GitHub secret is a long-lived credential: if it
leaks, an attacker can publish malicious releases under your name. **Trusted
Publishing** replaces it with short-lived OIDC tokens minted per-run and scoped
to a specific repo + workflow + environment. There is nothing to store, rotate,
or leak. That is why this repo stores no PyPI secret and
[`publish.yml`](../.github/workflows/publish.yml) has no password field.

## Manual publish from local `dist/` (simplest)

Good for a first release or TestPyPI trial:

```sh
uv build
# TestPyPI first (recommended dry run):
uv publish --index testpypi dist/*
# then the real index once you're happy:
uv publish dist/*
```

`uv publish` will prompt for credentials; for a one-off you can use a token
scoped to this project, but prefer the Trusted Publishing flow below for
anything recurring.

## Test on TestPyPI first

Always validate on <https://test.pypi.org> before the real index:

1. Register the project name on TestPyPI.
2. Configure a TestPyPI Trusted Publisher (same steps as below, on the test site).
3. Publish there, then `pip install --index-url https://test.pypi.org/simple/ agenteval`
   in a clean venv to confirm it installs and `agenteval --version` works.

## PyPI Trusted Publishing with GitHub Actions

One-time setup, then `publish.yml` works with zero secrets:

1. Build/publish once (or reserve the name) so the project exists on PyPI.
2. On PyPI: *Your project → Settings → Publishing → Add a new publisher*.
   Choose **GitHub Actions** and enter:
   - Owner: `GeorgePr`
   - Repository: `agent-eval`
   - Workflow filename: `publish.yml`
   - Environment: `pypi`
3. In GitHub: *Settings → Environments → New environment* named **`pypi`**. Add
   **required reviewers** so each publish needs human approval.

### What GitHub environment protection is

A GitHub *environment* is a named deployment target with its own rules. Gating a
job on `environment: pypi` means the job pauses until a required reviewer
approves it, and it can only mint an OIDC token for that environment. This gives
you a manual approval step and a scoped credential in one — the reason
`publish.yml` sets `environment: pypi` and `id-token: write`.

## `publish.yml` is inactive until configured

The workflow is `workflow_dispatch` only (manual), requires typing `publish` to
confirm, and depends on the `pypi` environment plus a configured Trusted
Publisher. Until you complete the setup above, running it fails at the publish
step by design. It never runs on push or on a tag.

## Recommended release flow

1. Bump `__version__` in `agenteval.py`.
2. Update `CHANGELOG.md`.
3. Run local checks: `scripts/release-check.sh`.
4. Commit, then tag: `git tag vX.Y.Z`.
5. Push the tag: `git push origin vX.Y.Z`.
6. `release.yml` verifies the tag matches `__version__`, builds, and creates the
   GitHub Release with wheel + sdist attached.
7. *Optional:* once a Trusted Publisher is configured, run the **Publish**
   workflow manually (or `uv publish` locally) to push to TestPyPI/PyPI.
