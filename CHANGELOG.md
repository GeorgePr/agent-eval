# Changelog

All notable changes to AgentEval. Dates are omitted; releases are tracked by
version. The product boundary is unchanged throughout: deterministic agent
regression testing — `run -> score -> diff -> artifact -> exit code`, zero LLM by
default, stdlib + pyyaml only.

## v0.4.0

Release/adoption hardening — easier to install, sanity-check, demo, and maintain.

- **Version & packaging.** Single source of truth `__version__ = "0.4.0"`;
  `agenteval --version`. `pyproject.toml` gains a hatchling build backend
  (dynamic version) and an `agenteval` console script, so the tool installs as a
  command while `uv run agenteval.py ...` keeps working.
- **`doctor`** command: sanity-checks Python, pyyaml, config, scenario file,
  baseline, and artifact-directory writability without importing or calling an
  agent. Exit 0 healthy, 2 on problems.
- **`selftest`** command: proves the core loop (create baseline → pass → regress)
  in a throwaway workspace with no examples, network, or external services.
  `--keep-dir` and `--tmp-dir` supported.
- **CLI contract tests** locking down command names, `--help`/`--version`
  stability, and clean exit codes with no tracebacks on user errors.
- **Docs**: `docs/scenario-format.md`, `docs/artifacts.md`, `docs/ci.md`.
  README tightened to a concise landing page that links to them.
- **`claude.md`** maintainer context added and kept current.

## v0.3.0

CI ergonomics and safer baselines without platform complexity.

- `init` scaffolds `.agenteval/`, config, and a starter scenario file.
- `validate` checks scenarios/config/filters without importing an agent.
- `baseline show` / `promote` / `diff` for reviewed, offline baseline workflows.
- Filtered first runs refuse to create a partial baseline unless
  `--allow-partial-baseline` is passed.
- HTTP hardening: `--http-timeout`, repeatable `--http-header`,
  `--http-bearer-token-env`, and `--http-method` (GET/POST/PUT/PATCH); all
  configurable in `.agenteval.yaml` with CLI override.
- Seven new deterministic assertions: `status_code`, `max_duration_ms`,
  `tool_call_count`, `max_tool_calls`, `tool_sequence`, `json_path_contains`,
  `json_path_regex`.
- Run artifacts carry `artifact_version`; promote/diff tolerate versionless older
  artifacts and reject unsupported future versions.
- Corrupt/missing baseline files now exit 2 (config error) instead of 4.

## v0.2.0

CI-grade artifacts and real-agent reach.

- `--junit-out` (JUnit XML via stdlib) and `--markdown-out` reports.
- `--require-baseline` so a fresh CI runner cannot mint a green baseline.
- HTTP target mode (`--target`, `--http-input-key`) via stdlib urllib.
- Scenario `tags`, `skip`, `skip_reason`; `--scenario`/`--include-tag`/
  `--exclude-tag` filtering.
- Per-scenario `runs` and `min_pass_rate` thresholds.
- Config file support via `--config` or auto-discovered `.agenteval.yaml`.

## v0.1.0

The MVP loop.

- Load YAML scenarios, import an agent via `module:function`, run K times, score
  deterministic assertions, diff against a JSON baseline, exit non-zero on
  regression.
- Assertions: `contains`, `not_contains`, `used_tool`, `not_used_tool`,
  `max_steps`, `min_steps`, `equals`, `regex`, `json_path_equals`,
  `json_path_exists`.
- Baseline auto-created on first run; never overwritten without
  `--update-baseline`. `--json-out` run artifact. Import-guarded `semantic`/
  `judge` stubs.
