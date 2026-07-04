# claude.md — AgentEval maintainer context

Concise, current maintainer notes. Keep useful, not a transcript.

## Current status

- **v0.5.0 CI/CD & release hardening — implemented (Unreleased).** GitHub Actions
  CI + tag-driven release workflow + optional manual OIDC publish workflow +
  release/publishing docs + local release-check helper. Product code
  (`agenteval.py`) unchanged; `__version__` still `0.4.0` until v0.5.0 is cut.
- Version: **0.4.0** (`__version__` in `agenteval.py`).
- Single-file implementation: `agenteval.py`.
- Runtime deps: **stdlib + pyyaml only**. Dev dep: **pytest only**. No mypy.
- Packaging: hatchling backend + `agenteval` console script; `uv build` works.
  `uv run agenteval.py ...` still works unchanged.
- Product boundary unchanged: deterministic agent regression testing —
  `run -> score -> diff -> artifact -> exit code`. Zero LLM by default.
- Tests: **159 passing** (test_agenteval 26, test_v02 30, test_v03 48,
  test_v04 35, test_release 20). `uvx ruff check .` clean.
- v0.4 released at commit `4a8e808`.

## CI/CD workflow summary

- `.github/workflows/ci.yml` — push/PR: pytest, ruff, `uv build`, script smoke
  (`--version`, `selftest`), installed-wheel smoke (wheel → temp venv →
  `agenteval --version`/`selftest`), upload dist (7-day retention). Linux-only,
  Python 3.12, concurrency-cancel, no secrets.
- `.github/workflows/release.yml` — `v*` tag: `scripts/check_version.py` guards
  tag==`__version__`, then pytest/ruff/selftest/build, upload dist (30-day),
  `gh release create/upload` attaches wheel+sdist. `permissions: contents: write`.
  No PyPI publish.
- `.github/workflows/publish.yml` — `workflow_dispatch` only, `environment: pypi`,
  `id-token: write`, `pypa/gh-action-pypi-publish` (OIDC, no token). Inert until a
  PyPI Trusted Publisher + `pypi` environment are configured.

## Release strategy

- Branch/PR dev; CI green before merge. Releases are **tag-driven** (`vX.Y.Z`).
- Version single-sourced from `agenteval.__version__`; tag must match (enforced).
- GitHub Release with wheel+sdist is the deliverable. PyPI is **optional, manual,
  OIDC-only** — no API tokens anywhere. See `docs/publishing.md`.
- Pre-tag flow: bump `__version__` → update `CHANGELOG.md` →
  `scripts/release-check.sh` → tag → push tag.

## Cost assumptions

- Public repo on standard GitHub-hosted Linux runners = free (assumed acceptable).
- Private repo: set a **$0 spending cap**; workflows kept small, Linux-only, short
  retention. No-cost fallbacks documented: self-hosted runner, Jenkins,
  Forgejo/Gitea + Woodpecker, or local `scripts/release-check.sh` only.

## v0.4 summary

- `--version` + single-source `__version__`; `pyproject.toml` packaging
  (hatchling dynamic version, `agenteval` console script). Direct script
  execution preserved.
- `doctor`: sanity-checks Python/pyyaml/config/scenarios/baseline/artifact-dir
  writability without importing an agent. Exit 0/2.
- `selftest`: proves the core loop in a throwaway temp workspace (no examples,
  network, or services). `--keep-dir`, `--tmp-dir`. Outcome-checking factored
  into `evaluate_selftest(run_fn=...)` for non-brittle failure-path tests.
- CLI contract/help-stability tests.
- Docs: `docs/scenario-format.md`, `docs/artifacts.md`, `docs/ci.md`.
  README tightened to a landing page linking them. `CHANGELOG.md` added.

## Architecture summary

One module, `agenteval.py`, organized top-to-bottom:

- Constants / exit codes / assertion-type sets.
- Errors: `ScenarioError`, `AgentLoadError`, `ConfigError` (all → exit 2 except
  agent load → 3).
- Scenario loading + schema validation (`load_scenarios`).
- Filtering / selection (`select_scenarios`, `effective_runs`).
- Agent loading: `load_agent` (module:function via importlib) and
  `make_http_agent` (stdlib urllib; POST/PUT/PATCH JSON body, GET query param).
- Result extraction (`output_text`, `tool_names`, `resolve_json_path`).
- Assertions: `evaluate_assertion` + `_evaluate` (pure, structured results).
- Runner: `run_scenario` (K runs, captures output/tools/steps/duration/error).
- Baseline: `build/write/load_baseline`, `load_artifact`, `diff_against_baseline`.
- Reporting: `format_event`, `print_report`, `write_junit`, `render/write_markdown`.
- Config: `load_config` (strict key whitelist), `resolve_settings`,
  `_resolve_http_settings`, `parse_http_headers`.
- Subcommands: `cmd_run`, `cmd_init`, `cmd_validate`,
  `cmd_baseline_show/promote/diff`.
- CLI: `build_parser`, `main` (catches typed errors → clean exit codes).

## Supported commands

- `--version` (top level)
- `run SCENARIOS --agent m:f | --target URL [many flags]`
- `init [--force --scenario-file --config-file --agent --target --http-input-key]`
- `validate SCENARIOS [--config --scenario --include-tag --exclude-tag]`
- `baseline show|promote|diff`
- `doctor [--config --scenario-file --baseline]`
- `selftest [--keep-dir --tmp-dir]`

## Exit code contract

- 0 pass / baseline created / baseline updated / promote OK / diff no-regression
- 1 regression (or missing scenarios with `--fail-on-missing`); `baseline diff` regression
- 2 invalid scenario / invalid config / missing required baseline / bad filter
  selection / missing-or-invalid baseline or artifact / bad HTTP option
- 3 agent import failure
- 4 internal unexpected error (last-resort; prints traceback)

## Important design decisions

- Deterministic assertions are the core; `semantic`/`judge` are import-guarded
  stubs, never required, never auto-installed.
- Baseline is a plain JSON file; never silently overwritten (needs
  `--update-baseline` or `baseline promote --force`).
- Filtered first run refuses to create a partial baseline unless
  `--allow-partial-baseline`.
- HTTP mode is stdlib urllib only — no requests/httpx. Non-2xx → scenario
  failure, never a crash.
- Config file has a strict key whitelist; unknown keys exit 2. CLI overrides config.
- Artifacts carry `artifact_version`; promote/diff tolerate versionless older
  artifacts, reject unsupported future versions.

## Assertion types

Deterministic: contains, not_contains, used_tool, not_used_tool, max_steps,
min_steps, equals, regex, json_path_equals, json_path_exists, status_code,
max_duration_ms, tool_call_count, max_tool_calls, tool_sequence,
json_path_contains, json_path_regex. Optional stubs: semantic, judge.

## File map

- `agenteval.py` — the tool (single file).
- `tests/test_agenteval.py` (v0.1), `test_v02.py`, `test_v03.py`, `test_v04.py`,
  `test_release.py` (CI/CD guardrails + tag/version helper).
- `scripts/check_version.py` (tag==version), `scripts/release-check.sh` (local gates).
- `.github/workflows/ci.yml`, `release.yml`, `publish.yml`.
- `examples/myagent.py`, `examples/scenarios.yaml`.
- `docs/scenario-format.md`, `docs/artifacts.md`, `docs/ci.md`,
  `docs/release-strategy.md`, `docs/publishing.md`.
- `README.md`, `CHANGELOG.md`, `claude.md`, `pyproject.toml`, `.gitignore`.

## Commands run this session (v0.5 CI/CD)

- `uv run pytest` → 159 passed
- `uvx ruff check .` → clean
- `uv build` → `agenteval-0.4.0` wheel + sdist
- `sh scripts/release-check.sh` → OK (incl. installed-wheel smoke test)
- `uv run python scripts/check_version.py v0.4.0` → OK; `v9.0.0` → mismatch exit 1
- Validated all workflow YAML parses via `yaml.safe_load`.

## Known limitations

- semantic/judge are still stubs (no real semantic/LLM-judge scoring).
- Only the tiny `$.field[.field]` JSON-path helper — no full JSONPath.
- No custom HTTP request-body templates (body is `{input_key: input}`).
- No distributed execution, dashboard, or persistent database.
- No mypy configuration.
- **CI/CD not yet verified on GitHub** — workflows are YAML-valid and the
  commands they run are proven locally, but actual runner execution (Actions
  triggers, `gh release create`, OIDC publish) must be confirmed after push.
- `publish.yml` is inert until a PyPI Trusted Publisher + `pypi` environment
  are configured; PyPI publishing is otherwise manual.

## Next recommended step

Push and confirm the CI workflow runs green on GitHub, then cut **v0.5.0**
(bump `__version__`, finalize CHANGELOG, tag `v0.5.0`) to exercise the release
workflow once. After that, dogfood AgentEval against a real agent project and
capture the first real regression case study.
