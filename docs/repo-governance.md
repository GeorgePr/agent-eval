# Repository governance

Practical governance for a small open-source / solo-maintained CLI. Nothing here
requires paid GitHub features; some settings are configured once in the GitHub UI
(see the [manual checklist](#manual-github-settings-checklist)).

## Branch strategy

- **The default branch is always releasable.** Every commit on it should pass CI.
  The desired steady-state name for the default/integration branch is **`main`**;
  see [Default branch normalization](#default-branch-normalization) if the repo
  still uses an older generated default-branch name.
- **Feature branches** for all changes (e.g. `fix/…`, `docs/…`, `ci/…`).
- Once the repo is public or shared, **require a PR before merge** — no direct
  pushes to the default branch.
- Tags (`vX.Y.Z`) are cut from the default branch after checks pass.

## Branch lifecycle

- **Short-lived branches** — feature, `docs/…`, `ci/…`/devops, and
  `release/vX.Y.Z` branches — are **deleted after their PR is squash-merged**.
- **Dependabot branches** are deleted after merge (Dependabot does this itself;
  otherwise delete them once merged).
- **Release branches are not kept as archives.** A `release/vX.Y.Z` branch is
  temporary scaffolding for the release PR; delete it once the PR is merged and
  the tag is created.
- **Historical versions live in tags and GitHub Releases, not branches.** Tags
  (`vX.Y.Z`) and their Releases (wheel + sdist + `SHA256SUMS`) are the permanent
  record; keep them indefinitely. Do **not** keep per-version branches.
- **Maintenance branches** (e.g. `maintenance/v0.5`) are created **only** when an
  older release line is actively supported with backports. None exists today.

## Default branch normalization

If the repo's default branch still uses an older generated name (this repo began
on `claude/agent-regression-cli-mvp-5pkspw`), normalize it to `main` after a
successful release:

- Prefer GitHub's **Settings → Branches → rename** on the default branch — it
  preserves history, updates the default pointer, retargets open PRs, and
  redirects old refs in one step.
- Then re-point branch protection at `main` (required check: `test`).
- Only delete the old branch name once `main` is confirmed as the default and no
  open PR targets the old name.

This is a GitHub-UI action and cannot be performed from repo files.

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
- Monitor Actions usage in your GitHub settings. A self-hosted runner
  (homelab/Pi), Jenkins, or Forgejo/Gitea + Woodpecker remain optional
  alternatives if you'd rather not use hosted runners. See
  [release-strategy.md](release-strategy.md).

## CODEOWNERS (optional)

A `CODEOWNERS` file can auto-request review from specific accounts. It's omitted
here because it needs a real GitHub username/team. To add it later, create
`.github/CODEOWNERS` with e.g. `* @your-username` and enable "Require review from
Code Owners" in branch protection.

## Manual GitHub settings checklist

These live in the GitHub UI and **cannot be enforced by files in the repo**:

- [ ] Normalize the default branch to `main` if it still uses an older generated
      name (Settings → Branches → rename); see above.
- [ ] Branch protection / ruleset on the default branch (`main`; see above).
- [ ] Mark **`test`** as a required status check.
- [ ] Enable **Private vulnerability reporting** (Settings → Security).
- [ ] Create the **`pypi`** environment with required reviewers (only needed if you
      publish to PyPI).
- [ ] (Optional) review Actions usage/limits in Settings if you want tighter
      control over runner minutes.
- [ ] (Optional) add `.github/CODEOWNERS` and require code-owner review.
- [ ] (Optional) require signed commits.
