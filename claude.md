# claude.md — AgentEval maintainer context

Concise, current maintainer notes. Keep useful, not a transcript.

## Current status

- **Docs cleanup done; v0.5.0 tag push STILL BLOCKED (this session).**
  Repo-visibility/cost comparisons removed from Markdown docs and the broken
  README packaging anchor fixed via PR #6 (squash-merged after CI → `ba56f11`).
  Two docs guardrail tests added (visibility phrases; relative-link/anchor
  checker) — 200 tests passing. The `v0.5.0` tag was recreated locally at
  `ba56f11` and the push retried **once**: same **HTTP 403** from the git proxy
  on tag refs. No remote tag, `release.yml` still 0 runs, no GitHub Release.
  No product behavior changes; `agenteval.py` untouched.
- **v0.5.0 release-prep (previous session).** Merged the 4 Dependabot PRs,
  prepped the release on `release/v0.5.0`, PR #5 squash-merged (`2e9e8eb`);
  `agenteval.py` edited only to bump `__version__` to `0.5.0`. Tag push was
  blocked (HTTP 403 on tag refs).
- **Branch topology note:** the repo's **default/integration branch is
  `claude/agent-regression-cli-mvp-5pkspw`** — there is **no `main`**. All PRs
  (Dependabot + release) target that branch; the task's "main" maps to it.
- **Dependabot: all 4 GitHub Actions PRs merged (squash).** #1 checkout v4→v7,
  #2 setup-uv v5→v7, #3 upload-artifact v4→v7, #4 setup-python v5→v6. All were
  green (`test`), diffs touched only `uses:` lines, none skipped. Merged base
  `c17a32d`; default-branch CI green after merge.
- Version: **0.5.0** (`__version__` in `agenteval.py`).
- Single-file implementation: `agenteval.py`.
- Runtime deps: **stdlib + pyyaml only**. Dev dep: **pytest only**. No mypy.
- Packaging: hatchling backend + `agenteval` console script; `uv build` works.
  `uv run agenteval.py ...` still works unchanged.
- Product boundary unchanged: deterministic agent regression testing —
  `run -> score -> diff -> artifact -> exit code`. Zero LLM by default.
- Tests: **198 passing** (test_agenteval 26, test_v02 30, test_v03 48,
  test_v04 35, test_release 25, test_devops 34). `uvx ruff check .` clean.
- Prior release: v0.4 at commit `4a8e808`. DevOps hardening at `e071dfa`.
- Claude chat/share links in docs: **none found** (searched README, docs/,
  CHANGELOG, CONTRIBUTING, SUPPORT, SECURITY, .github, claude.md); guardrail test
  added.

## CI/CD workflow summary

- `.github/workflows/ci.yml` — push/PR: pytest, ruff, `uv build`, `verify_dist`,
  `build_checksums`, script smoke (`--version`, `selftest`), installed-wheel smoke
  (wheel → temp venv → `agenteval --version`/`selftest`), upload
  wheel+sdist+SHA256SUMS (7-day). Linux-only, Python 3.12, concurrency-cancel,
  `contents: read`, `timeout-minutes: 15`, no secrets.
- `.github/workflows/release.yml` — `v*` tag: `scripts/check_version.py` guards
  tag==`__version__`, then pytest/ruff/selftest/build/verify/checksums, upload
  (30-day), `gh release create/upload` attaches wheel+sdist+SHA256SUMS.
  `contents: write`, `timeout-minutes: 15`. No PyPI publish.
- `.github/workflows/publish.yml` — `workflow_dispatch` only, `environment: pypi`,
  `id-token: write`, `timeout-minutes: 15`, `pypa/gh-action-pypi-publish` (OIDC, no
  token). Inert until a PyPI Trusted Publisher + `pypi` environment are configured.
- `.github/dependabot.yml` — weekly `github-actions` + `pip` updates, capped PRs.
- Required status check for branch protection: **`test`** (job id in `ci.yml`).

## Release strategy

- Branch/PR dev; CI green before merge. Releases are **tag-driven** (`vX.Y.Z`).
- Version single-sourced from `agenteval.__version__`; tag must match (enforced).
- GitHub Release with wheel+sdist is the deliverable. PyPI is **optional, manual,
  OIDC-only** — no API tokens anywhere. See `docs/publishing.md`.
- Pre-tag flow: bump `__version__` → update `CHANGELOG.md` →
  `scripts/release-check.sh` → tag → push tag.

## Cost assumptions

- Small Linux-only workflows on GitHub-hosted Ubuntu runners; short artifact
  retention; monitor Actions usage in GitHub settings. No-cost fallbacks
  documented: self-hosted runner, Jenkins, Forgejo/Gitea + Woodpecker, or local
  `scripts/release-check.sh` only. (Repo-visibility/cost comparisons were removed
  from user-facing docs this session per maintainer request.)

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
- `tests/` — `test_agenteval.py` (v0.1), `test_v02.py`, `test_v03.py`,
  `test_v04.py`, `test_release.py` (CI/CD guardrails + tag/version helper),
  `test_devops.py` (Dependabot, workflow hardening, checksums, verify_dist,
  community files).
- `scripts/` — `check_version.py` (tag==version), `release-check.sh` (local
  gates), `build_checksums.py` (SHA256SUMS), `verify_dist.py` (dist sanity).
- `.github/` — `workflows/{ci,release,publish}.yml`, `dependabot.yml`,
  `pull_request_template.md`, `ISSUE_TEMPLATE/{bug_report,feature_request,
  regression_report,config}.yml`.
- `examples/myagent.py`, `examples/scenarios.yaml`.
- `docs/` — `scenario-format.md`, `artifacts.md`, `ci.md`, `release-strategy.md`,
  `publishing.md`, `repo-governance.md`, `runbooks/{ci-failure,release,rollback}.md`.
- Root — `README.md`, `CHANGELOG.md`, `claude.md`, `SECURITY.md`,
  `CONTRIBUTING.md`, `SUPPORT.md`, `pyproject.toml`, `.gitignore`.

## Docs cleanup + tag retry outcome (latest session)

- **Docs cleanup PR #6 squash-merged** (`ba56f11`) after CI green: removed
  repo-visibility cost comparisons from `docs/release-strategy.md`,
  `docs/ci.md`, `docs/repo-governance.md`, `CHANGELOG.md`, `claude.md`;
  replaced with neutral wording (hosted Ubuntu runners, small workflows, short
  retention, monitor Actions usage, self-hosted/Jenkins/Woodpecker optional).
- **README packaging link fixed**: `[Packaging](#packaging)` → 
  `[Packaging & releases](#packaging--releases)` (heading had been renamed; no
  new doc file needed).
- **Guardrail tests added** in `tests/test_devops.py`: Markdown must not contain
  repo-visibility/cost phrases; relative `.md` links and same-file anchors in
  README/docs must resolve. Claude chat/share link guard preserved (still none).
- **⛔ Tag retry failed again**: local `v0.5.0` retagged at `ba56f11`
  (old local tag at `2e9e8eb` deleted locally; nothing force-pushed);
  `git push origin refs/tags/v0.5.0` → **HTTP 403** (git proxy blocks tag
  refs; branch pushes work). Tried once, then stopped per policy. No API/
  `gh release create` workaround attempted.

## v0.5.0 release outcome (release-prep session)

- **Dependabot: 4/4 merged (squash), none skipped** — PRs #1–#4 (checkout v7,
  setup-uv v7, upload-artifact v7, setup-python v6). All green, diffs touched only
  `uses:` lines. Default-branch CI green after merge (`c17a32d`).
- **Release PR #5 (`release: prepare v0.5.0`) squash-merged** into the default
  branch after CI passed → merge commit `2e9e8eb`. Base now has
  `__version__ = "0.5.0"`; version guard passes.
- **Claude chat/share links: none found**; guardrail test added.
- **⛔ Tag `v0.5.0` NOT pushed — BLOCKED.** `git push origin v0.5.0` returns
  **HTTP 403** from the git proxy. Branch pushes work this session, so the proxy
  policy specifically blocks pushing tag refs. Local tag `v0.5.0` → `2e9e8eb`
  exists but is not on the remote. Therefore **`release.yml` has NOT run and no
  GitHub Release / artifacts exist yet.** Not retried (403 = policy denial).

## Commands run this session (v0.5.0 release-prep)

- Inspected + squash-merged Dependabot PRs #1–#4 and release PR #5 (GitHub MCP).
- `uv run pytest` → 198 passed; `uvx ruff check .` → clean
- `uv build` → `agenteval-0.5.0` wheel + sdist
- `python scripts/build_checksums.py --dist-dir dist` → SHA256SUMS (0.5.0)
- `python scripts/verify_dist.py --dist-dir dist` → OK
- `sh scripts/release-check.sh` → OK end to end
- `python scripts/check_version.py v0.5.0` → OK (exit 0); `v9.9.9` → mismatch (exit 1, expected)

## Known limitations

- semantic/judge are still stubs (no real semantic/LLM-judge scoring).
- Only the tiny `$.field[.field]` JSON-path helper — no full JSONPath.
- No custom HTTP request-body templates (body is `{input_key: input}`).
- No distributed execution, dashboard, or persistent database.
- No mypy configuration.
- **`ci.yml` is verified green on GitHub** (PR + push runs), but **`release.yml`
  has never run** (tag push blocked so far) and OIDC publish is unexercised —
  confirm both the first time they actually run.
- `publish.yml` is inert until a PyPI Trusted Publisher + `pypi` environment
  are configured; PyPI publishing is otherwise manual.

## Manual GitHub settings still required (not enforceable by repo files)

- Branch protection on the default branch
  (`claude/agent-regression-cli-mvp-5pkspw`; no `main` exists); mark **`test`**
  as a required status check.
- Enable Private vulnerability reporting (for SECURITY.md flow).
- Create the `pypi` environment with required reviewers (only if publishing).
- Optional: review Actions usage/limits in Settings.
- Optional: `.github/CODEOWNERS`, required signed commits.
- See `docs/repo-governance.md` for the full checklist.

## Manual follow-up still required

1. **Push the `v0.5.0` tag from an environment allowed to push tags** (the
   in-session git proxy blocks tag pushes with 403; retried once per session, same
   result). Tag the current default-branch tip:
   `git tag v0.5.0 ba56f11 && git push origin v0.5.0` (or the latest default tip
   with `__version__ = "0.5.0"`). That triggers `release.yml`, whose tag/version
   guard will pass.
2. After the tag is pushed, verify the `release.yml` run is green and the GitHub
   Release has wheel + sdist + `SHA256SUMS` attached (see `docs/runbooks/release.md`).
3. Enable branch protection in the GitHub UI (required check: `test`).
4. `publish.yml` stays inert until a PyPI Trusted Publisher + `pypi` environment
   are configured; do not enable automatic PyPI publishing.

## Next recommended step

Complete the tag push above to finish the v0.5.0 release, then **do not add
product features** until that first real release workflow has been exercised;
after that, dogfood against a real agent and capture the first regression case study.
