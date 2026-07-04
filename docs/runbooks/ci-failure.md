# Runbook: CI failure triage

CI (`.github/workflows/ci.yml`) runs one Linux job named `test`. When it's red,
open the failed run (Actions tab), expand the failed step, and match it below.
Almost everything reproduces locally with one command:

```sh
sh scripts/release-check.sh
```

## `pytest` failed

- Read the assertion/traceback in the step log — pytest prints the exact test,
  file, and line.
- Reproduce: `uv run pytest -q` (or `uv run pytest tests/test_x.py::test_name`).
- If it's a real regression, fix the code; if the test is wrong, fix the test in
  the same PR. Don't skip a failing test to go green.

## `ruff` failed

- The log lists `file:line: RULE message`.
- Reproduce: `uvx ruff check .`. Auto-fixable issues: `uvx ruff check . --fix`
  (review the diff before committing).

## `uv build` failed

- Usually a `pyproject.toml` or version issue. Reproduce: `uv build`.
- If hatchling can't determine the version, check `[tool.hatch.version] path` and
  the `__version__` line in `agenteval.py`.

## `Verify distributions` failed

- `scripts/verify_dist.py` found a missing expected file or unwanted junk.
  Reproduce: `uv build && uv run python scripts/verify_dist.py --dist-dir dist`.
- Missing `agenteval.py`/`README.md`/`pyproject.toml` in the sdist, or a stray
  `.github/`/cache path: adjust `[tool.hatch.build.targets.sdist]` include list in
  `pyproject.toml`.

## `Smoke-test installed wheel` failed (but script smoke passed)

- This is a **packaging** problem, not a logic bug: the source runs but the
  installed console script doesn't. Check `[project.scripts]` and the wheel
  `include`. Reproduce with `sh scripts/release-check.sh` (it installs the wheel
  into a temp venv and runs `agenteval --version` / `agenteval selftest`).

## When to inspect artifacts

CI uploads `dist/*.whl`, `dist/*.tar.gz`, and `dist/SHA256SUMS` (7-day retention)
under the run's **Artifacts**. Download them when a failure is packaging-related
and you want to inspect the exact built files, or to compare checksums.

## Infra flakes

A timeout (`timeout-minutes: 15`) or a runner/network hiccup is not a code
failure — re-run the job. If it recurs, it's real; investigate.
