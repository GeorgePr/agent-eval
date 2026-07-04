# Contributing to AgentEval

Thanks for helping! AgentEval is a small, single-file CLI with an intentionally
thin dependency surface. Contributions that keep it that way are easiest to merge.

## Local setup

Requires [uv](https://docs.astral.sh/uv/). No other global tooling needed.

```sh
git clone https://github.com/GeorgePr/agent-eval
cd agent-eval
uv run pytest          # installs deps into a managed venv on first run
```

## Commands

```sh
uv run pytest                                   # tests
uvx ruff check .                                # lint
uv build                                        # build wheel + sdist
uv run python scripts/verify_dist.py --dist-dir dist    # dist sanity check
uv run python scripts/build_checksums.py --dist-dir dist  # SHA256SUMS
sh scripts/release-check.sh                     # everything CI runs, locally
```

## Development principles

- **Deterministic assertions first.** The core must work with zero model
  dependencies. LLM/semantic features stay optional and import-guarded.
- **Thin dependencies.** Runtime deps are stdlib + `pyyaml` only. A new runtime
  dependency needs a strong, discussed justification.
- **No product-behavior changes hidden inside DevOps/docs PRs.** Keep scoring,
  diffing, exit codes, and CLI surface changes in clearly product-scoped PRs with
  tests.
- **Update docs and `claude.md`.** Significant changes should update the relevant
  `docs/` file, `CHANGELOG.md`, and the maintainer notes in `claude.md`.
- **Single-file implementation.** Keep the tool in `agenteval.py` unless splitting
  is clearly necessary.

## Pull requests

- Fill in the PR template.
- Make sure `uv run pytest` and `uvx ruff check .` pass. Run
  `sh scripts/release-check.sh` if you touched packaging or the release path.
- Note whether product behavior or the dependency surface changed.
- Keep diffs surgical; avoid unrelated refactors.

See [docs/repo-governance.md](docs/repo-governance.md) for branch/release
governance and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.
