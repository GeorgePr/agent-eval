# Release strategy (free-tier CI/CD)

How AgentEval is tested, built, and released using GitHub Actions on the free
path — with no paid SaaS and no API tokens.

## CI strategy

On every push and pull request, [`ci.yml`](../.github/workflows/ci.yml):

1. Runs the test suite (`uv run pytest`).
2. Runs the linter (`uvx ruff check .`).
3. Builds the wheel and sdist (`uv build`).
4. Smoke-tests **direct script** usage (`uv run agenteval.py --version` and
   `selftest`).
5. Smoke-tests the **installed console script** by installing the built wheel
   into a throwaway venv and running `agenteval --version` and `agenteval selftest`.
6. Uploads `dist/*.whl` and `dist/*.tar.gz` as build artifacts with **7-day**
   retention.

## Free usage boundary

- **Public repo:** GitHub Actions on standard Linux hosted runners is free, so
  this setup runs at no cost.
- **Private repo:** included minutes and storage are finite. Keep workflows
  small (this one is a single Linux job), keep artifact retention short, and set
  a spending limit of **$0** in *Settings → Billing → Spending limits* so you
  can never be charged unexpectedly.
- **Linux only, single Python (3.12).** No macOS/Windows matrix — those runners
  cost several times more minutes and add little for a pure-Python, stdlib+pyyaml
  tool. Expand the matrix later only if you actually support more environments.
- **Short artifact retention** (7 days for CI, 30 for releases) keeps storage
  usage low.

## Release strategy

- Normal development happens on branches and PRs; **CI must pass before merge**.
- Releases are **tag-driven**: pushing a `vX.Y.Z` tag triggers
  [`release.yml`](../.github/workflows/release.yml), which builds artifacts from
  the tagged commit and attaches the wheel + sdist to a **GitHub Release**.
- **PyPI publishing is optional and never automatic.** It lives in a separate,
  manual [`publish.yml`](../.github/workflows/publish.yml) that uses **Trusted
  Publishing / OIDC** only — no API tokens are stored as secrets. See
  [publishing.md](publishing.md).

## Versioning

- The package version is single-sourced from `agenteval.__version__` (hatchling
  reads it dynamically for `uv build`).
- A release tag must match that version. `release.yml` runs
  [`scripts/check_version.py`](../scripts/check_version.py) first and **fails the
  release if the tag and `__version__` disagree** — so a `v0.4.0` tag can only
  ship artifacts built from `__version__ == "0.4.0"`.

## Local pre-tag check

Run the same gates locally before tagging:

```sh
scripts/release-check.sh
```

It runs pytest, ruff, `uv build`, the script selftest, and an installed-wheel
smoke test in a temp venv. Fail-fast; if it's green, you're safe to tag.

## No-cost fallback tools

If GitHub Actions is not free enough for your situation (e.g. a private repo
with heavy usage), the same commands run anywhere:

- **Local only:** `scripts/release-check.sh` before every tag, then build and
  attach artifacts by hand. No CI service required at all.
- **GitHub Actions self-hosted runner** on a homelab box or Raspberry Pi: free
  minutes, you supply the hardware.
- **Jenkins** on a homelab: a freestyle/pipeline job running the same commands.
- **Forgejo/Gitea + Woodpecker CI:** fully self-hosted, open-source, no SaaS.

All of these run the identical `uv`/`ruff`/`pytest` steps — nothing in the
pipeline is GitHub-specific except the Release-creation step.
