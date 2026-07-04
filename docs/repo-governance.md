# Repository governance

Practical governance for a small open-source / solo-maintained CLI. Nothing here
requires paid GitHub features; some settings are configured once in the GitHub UI
(see the [manual checklist](#manual-github-settings-checklist)).

## Branch strategy

- **`main` is always releasable.** Every commit on `main` should pass CI.
- **Feature branches** for all changes (e.g. `fix/…`, `docs/…`, `ci/…`).
- Once the repo is public or shared, **require a PR before merge** — no direct
  pushes to `main`.
- Tags (`vX.Y.Z`) are cut from `main` after checks pass.

## Recommended branch protection

Configure on `main` (Settings → Branches → Add branch ruleset / protection rule):

- ✅ Require a pull request before merging.
- ✅ Require status checks to pass before merging.
  - Required check: **`test`** (from the `CI` workflow — shown as `CI / test`).
- ✅ Require branches to be up to date before merging (optional; adds rebase
  friction but guarantees checks ran against the latest base).
- ✅ Require conversation resolution before merging.
- ✅ Block force pushes.
- ✅ Block branch deletion.
- ⚙️ Require signed commits — optional. Good hygiene, but can be friction for a
  solo project; enable if you already sign commits.

Administrators can be included or exempted; for a solo maintainer, including
admins in the rules is a useful self-guardrail but you may relax it if it blocks
your own workflow.

## Required status checks

The check name comes from the **job id**, not the workflow name. In
`.github/workflows/ci.yml` the workflow is `CI` and the single job is `test`, so
the check to mark required is **`test`** (GitHub displays it as `CI / test`). If
you rename the job, update the required check in branch protection to match.

## Release protection

- Tags are created only **after `scripts/release-check.sh` passes locally**.
- `release.yml` re-verifies the tag matches `agenteval.__version__` before it
  builds or publishes anything (`scripts/check_version.py`).
- Release artifacts include a `SHA256SUMS` file; distributions are sanity-checked
  by `scripts/verify_dist.py` in CI and release.
- **PyPI publishing stays manual and environment-gated** (`publish.yml`,
  `workflow_dispatch` + `pypi` environment + OIDC). No API tokens. See
  [publishing.md](publishing.md).

## Dependency updates (Dependabot)

`.github/dependabot.yml` opens **weekly** PRs for two ecosystems:

- **`github-actions`** — action versions in `.github/workflows/*`.
- **`pip`** — the Python dependency surface declared in `pyproject.toml`.

Open-PR limits are capped (5 each) to avoid noise. How to handle these PRs:

- **Review like any other change.** They must pass CI (`test`) before merge.
- **Do not enable auto-merge initially.** A green check is necessary but a human
  should still glance at the diff, especially for Actions bumps that could change
  runner behavior.
- Prefer merging one ecosystem at a time so a regression is easy to bisect.

## Cost controls

- **Linux-only** runners (`ubuntu-latest`); no macOS/Windows matrix.
- **No large matrices** — a single Python (3.12) job.
- **Short artifact retention** (7 days CI, 30 days release).
- **No scheduled workflows** unless a real need appears (Dependabot's schedule is
  its own budget, not Actions minutes).
- **Job `timeout-minutes`** caps a hung run.
- For a **private repo**, set a **$0 spending limit** (Settings → Billing) so you
  can never be charged, and fall back to a self-hosted runner (homelab/Pi),
  Jenkins, or Forgejo/Gitea + Woodpecker if minutes run short. See
  [release-strategy.md](release-strategy.md).

## CODEOWNERS (optional)

A `CODEOWNERS` file can auto-request review from specific accounts. It's omitted
here because it needs a real GitHub username/team. To add it later, create
`.github/CODEOWNERS` with e.g. `* @your-username` and enable "Require review from
Code Owners" in branch protection.

## Manual GitHub settings checklist

These live in the GitHub UI and **cannot be enforced by files in the repo**:

- [ ] Branch protection / ruleset on `main` (see above).
- [ ] Mark **`test`** as a required status check.
- [ ] Enable **Private vulnerability reporting** (Settings → Security).
- [ ] Create the **`pypi`** environment with required reviewers (only needed if you
      publish to PyPI).
- [ ] (Private repos) set a **$0 billing spending limit**.
- [ ] (Optional) add `.github/CODEOWNERS` and require code-owner review.
- [ ] (Optional) require signed commits.
