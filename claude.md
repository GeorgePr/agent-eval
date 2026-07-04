# claude.md — AgentEval maintainer context

Concise, current maintainer notes. Keep useful, not a transcript.

## Current status

- Version: **0.4.0** (`__version__` in `agenteval.py`) — release/adoption
  hardening on top of v0.3. Complete and verified.
- Single-file implementation: `agenteval.py`.
- Runtime deps: **stdlib + pyyaml only**. Dev dep: **pytest only**. No mypy.
- Packaging: hatchling backend + `agenteval` console script; `uv build` works.
  `uv run agenteval.py ...` still works unchanged.
- Product boundary unchanged: deterministic agent regression testing —
  `run -> score -> diff -> artifact -> exit code`. Zero LLM by default.
- Tests: **139 passing** (test_agenteval 26, test_v02 30, test_v03 48,
  test_v04 35). `uvx ruff check .` clean.
- v0.3 released at commit `7cc06e6`.

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
- `tests/test_agenteval.py` (v0.1), `test_v02.py`, `test_v03.py`, `test_v04.py`.
- `examples/myagent.py`, `examples/scenarios.yaml`.
- `docs/scenario-format.md`, `docs/artifacts.md`, `docs/ci.md`.
- `README.md`, `CHANGELOG.md`, `claude.md`, `pyproject.toml`, `.gitignore`.

## Commands run this session (v0.4)

- `uv run pytest` → 139 passed
- `uvx ruff check .` → clean
- `uv build` → builds `agenteval-0.4.0` wheel + sdist
- `uv run agenteval.py --version` / `agenteval --version` → `agenteval 0.4.0`
- Manual smoke of `init`, `validate`, `doctor`, `selftest`, `baseline *`

## Known limitations

- semantic/judge are still stubs (no real semantic/LLM-judge scoring).
- Only the tiny `$.field[.field]` JSON-path helper — no full JSONPath.
- No custom HTTP request-body templates (body is `{input_key: input}`).
- No distributed execution, dashboard, or persistent database.
- No mypy configuration.

## Next recommended step

Not more feature growth. Dogfood AgentEval against a real agent project and
capture the first real regression case study — that will validate the assertion
set and baseline workflow against real drift before expanding scope.
