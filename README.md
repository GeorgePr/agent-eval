# agenteval

CLI-first agent regression testing. The smallest useful agent reliability layer:

```
run agent -> score behavior -> diff against baseline -> exit non-zero on regression
```

**Deterministic assertions are the core. LLM/semantic assertions are optional and
not required.** No dashboard, no service, no database — YAML scenarios, a JSON
baseline file on disk, and exit codes CI can trust. Runtime dependency: pyyaml only.

## Local bootstrap

```sh
uv run agenteval.py init
uv run agenteval.py run scenarios.yaml --agent examples.myagent:agent
```

`init` scaffolds `.agenteval/`, a starter `.agenteval.yaml`, and a
`scenarios.yaml` with deterministic assertions. It never overwrites existing
files unless you pass `--force`. Options: `--scenario-file`, `--config-file`,
`--agent`/`--target` (mutually exclusive, used for the printed next-step
command), and `--http-input-key` (written into the generated config).

## Usage

```sh
uv run agenteval.py run scenarios.yaml --agent myagent:agent
```

The agent is any Python callable `module:function` that takes the scenario input
string and returns either a string or a dict like
`{"output": "...", "tool_calls": ["..."], "steps": 2}`.

Validate scenarios, config, and filters without importing or calling any agent:

```sh
uv run agenteval.py validate scenarios.yaml
```

Exit 0 if valid, 2 with a clear error if not — handy as a fast pre-commit or
first CI step. Accepts `--config`, `--scenario`, `--include-tag`, `--exclude-tag`.

Common options:

```sh
uv run agenteval.py run scenarios.yaml --agent myagent:agent --baseline .agenteval/baseline.json
uv run agenteval.py run scenarios.yaml --agent myagent:agent --json-out .agenteval/latest.json
uv run agenteval.py run scenarios.yaml --agent myagent:agent --junit-out .agenteval/junit.xml
uv run agenteval.py run scenarios.yaml --agent myagent:agent --markdown-out .agenteval/report.md
uv run agenteval.py run scenarios.yaml --agent myagent:agent --runs 5
uv run agenteval.py run scenarios.yaml --agent myagent:agent --require-baseline
uv run agenteval.py run scenarios.yaml --agent myagent:agent --update-baseline
uv run agenteval.py run scenarios.yaml --agent myagent:agent --fail-on-missing
uv run agenteval.py run scenarios.yaml --agent myagent:agent --scenario refund_happy_path
uv run agenteval.py run scenarios.yaml --agent myagent:agent --include-tag smoke --exclude-tag expensive
```

## Recommended CI command

```sh
uv run agenteval.py run scenarios.yaml \
  --agent myagent:agent \
  --require-baseline \
  --json-out .agenteval/latest.json \
  --junit-out .agenteval/junit.xml \
  --markdown-out .agenteval/report.md
```

`--require-baseline` is the CI safety switch: locally, a first run bootstraps a
baseline automatically, but a fresh CI runner has no baseline and would otherwise
mint a green one and pass. With the flag, a missing baseline exits 2:

```
ERROR: Baseline required but not found: .agenteval/baseline.json
```

Commit your baseline file to the repo so CI diffs against it.

## HTTP target mode

Test an already-running HTTP agent instead of importing a module
(`--agent` and `--target` are mutually exclusive; exactly one is required):

```sh
uv run agenteval.py run scenarios.yaml \
  --target http://localhost:8000/agent \
  --http-input-key message \
  --http-header "X-Environment: ci" \
  --require-baseline
```

Each scenario run POSTs `{"input": "<scenario input>"}` (or `{"message": ...}`
with `--http-input-key message`). JSON responses with `output` / `tool_calls` /
`steps` keys are scored by the normal assertions; plain-text responses become the
output; `http_status` is attached to the result. Non-2xx responses and connection
errors are captured as scenario failures with the status and body in the reason —
never an internal crash. Uses stdlib `urllib`; no requests/httpx dependency.

Bearer token from the environment (the token itself never appears on the command
line or in config):

```sh
AGENT_TOKEN=... uv run agenteval.py run scenarios.yaml \
  --target http://localhost:8000/agent \
  --http-bearer-token-env AGENT_TOKEN \
  --require-baseline
```

GET-style agents receive the input as a query parameter instead of a JSON body:

```sh
uv run agenteval.py run scenarios.yaml \
  --target "http://localhost:8000/agent" \
  --http-method GET \
  --http-input-key q        # sends GET /agent?q=<scenario input>
```

HTTP controls (all exit 2 on invalid values, before anything runs):

- `--http-timeout SECONDS` — positive number; default 30.
- `--http-header "Name: Value"` — repeatable; exact `Name: Value` syntax.
- `--http-bearer-token-env ENV_VAR` — reads the token from the environment and
  sends `Authorization: Bearer <token>`; missing/empty var exits 2; conflicts
  with an explicit `Authorization` header.
- `--http-method GET|POST|PUT|PATCH` — default POST; POST/PUT/PATCH send the
  JSON body, GET sends the query parameter.

## Scenario file

```yaml
- id: refund_happy_path
  tags: [smoke, refund]        # optional; used by --include-tag/--exclude-tag
  input: "I want a refund for order 123"
  assert:
    - type: contains
      value: refund
    - type: used_tool
      value: lookup_order
    - type: max_steps
      value: 5

- id: flaky_but_acceptable
  runs: 5                      # optional per-scenario run count
  min_pass_rate: 0.8           # optional; scenario passes if pass_rate >= threshold
  input: "..."
  assert:
    - type: contains
      value: ok

- id: not_ready_yet
  skip: true                   # reported as SKIP, never executed, never fails
  skip_reason: "waiting for fixture"
  input: "..."
  assert:
    - type: contains
      value: anything
```

**Runs precedence:** explicit `--runs` (CLI or config) overrides scenario-level
`runs` for all scenarios; otherwise scenario `runs`; otherwise 1.

**min_pass_rate:** applies after all runs (default 1.0; must be between 0 and 1).
For thresholded scenarios, regression means "met the threshold in the baseline,
misses it now" — pass-rate dips that stay at or above the threshold are tolerated
flakiness, not regressions.

## Filtering

- `--scenario ID` (repeatable): run only the named scenario ids.
- `--include-tag TAG` (repeatable): run scenarios with at least one included tag.
- `--exclude-tag TAG` (repeatable): drop scenarios with any excluded tag.
- Zero runnable scenarios after filtering exits 2.
- Filtered-out scenarios are **not** reported as MISSING and are untouched by
  `--update-baseline` (updates merge; only scenarios deleted from the file are
  dropped from the baseline).

## Config file

Put shared CI settings in `.agenteval.yaml` (auto-discovered in the working
directory) or pass `--config path.yaml`. CLI flags always override config values.
Unknown keys and malformed files fail loudly with exit 2.

```yaml
baseline: .agenteval/baseline.json
require_baseline: true
fail_on_missing: false
runs: 5
json_out: .agenteval/latest.json
junit_out: .agenteval/junit.xml
markdown_out: .agenteval/report.md
include_tags:
  - smoke
exclude_tags:
  - expensive
http_input_key: message
http_timeout: 30
http_headers:
  - "X-Test: true"
http_bearer_token_env: AGENT_TOKEN
http_method: POST
```

(`agent`/`target` are intentionally CLI-only. A CLI `--http-header` list
replaces the config `http_headers` list entirely.)

## Baseline commands

Inspect, promote, and diff baselines without rerunning the agent:

```sh
# Record a run artifact
uv run agenteval.py run scenarios.yaml \
  --agent myagent:agent \
  --json-out .agenteval/latest.json

# See what changed vs the baseline (exit 1 on regression, 0 otherwise)
uv run agenteval.py baseline diff \
  --from .agenteval/latest.json \
  --baseline .agenteval/baseline.json

# Accept the run as the new baseline (reviewed, explicit)
uv run agenteval.py baseline promote \
  --from .agenteval/latest.json \
  --baseline .agenteval/baseline.json \
  --force

# Inspect a baseline
uv run agenteval.py baseline show --baseline .agenteval/baseline.json
```

`promote` refuses to overwrite an existing baseline and refuses REGRESSED
artifacts unless `--force` is passed, and rejects files that aren't agenteval
run artifacts. This is the recommended flow for reviewed baseline updates:
commit the promoted baseline in the same PR as the agent change.

## Baseline behavior

- **First run** (no baseline file): current results are written as the baseline, exit 0.
  If any filter (`--scenario`/`--include-tag`/`--exclude-tag`) is active, the run
  refuses to create a partial baseline and exits 2 unless you pass
  `--allow-partial-baseline`.
- **Later runs**: results are diffed against the baseline.
  - Lower pass rate for a known scenario, or a previously-passing assertion now
    failing → `REGRESSED`, exit 1 (thresholded scenarios use the `min_pass_rate`
    rule above).
  - Scenarios not in the baseline are reported as `NEW` (not added until you pass
    `--update-baseline`).
  - Baseline scenarios missing from the file are reported as `MISSING` (exit 1 only
    with `--fail-on-missing`).
- An existing baseline is **never overwritten** unless you pass `--update-baseline`.

## Exit codes

| code | meaning |
|------|---------|
| 0 | pass, baseline created, or baseline explicitly updated |
| 1 | regression vs baseline (or missing scenarios with `--fail-on-missing`) |
| 2 | invalid scenario file / invalid config / missing required baseline / empty filter selection |
| 3 | agent import failure |
| 4 | internal unexpected error |

## Assertion types

Deterministic (no model dependencies): `contains`, `not_contains`, `used_tool`,
`not_used_tool`, `max_steps`, `min_steps`, `equals` (normalized, case-insensitive),
`regex`, `json_path_equals` / `json_path_exists` (simple `$.field` paths against the
raw result dict), plus:

- `status_code` — HTTP status equals value (fails with a clear reason outside
  `--target` mode).
- `max_duration_ms` — run duration ≤ value (non-negative number).
- `tool_call_count` — exactly N tool calls.
- `max_tool_calls` — at most N tool calls.
- `tool_sequence` — tool calls exactly match an ordered list.
- `json_path_contains` — `$.field` substring match for strings, membership for lists.
- `json_path_regex` — regex against the `$.field` value as a string.

Optional stubs (never required, import-guarded): `semantic` (needs
`sentence-transformers` installed separately), `judge` (needs an optional judge
backend configured). Neither is used by the default smoke test.

## CI integration

The exit code is the contract; the artifacts are the reporting.

**Jenkins**: publish `.agenteval/junit.xml` as a JUnit test report
(`junit '.agenteval/junit.xml'` in a post step) and archive
`.agenteval/latest.json` and `.agenteval/report.md` with `archiveArtifacts`.

**GitHub Actions**:

```yaml
- run: |
    uv run agenteval.py run scenarios.yaml \
      --agent myagent:agent --require-baseline \
      --json-out .agenteval/latest.json \
      --junit-out .agenteval/junit.xml \
      --markdown-out .agenteval/report.md
- uses: actions/upload-artifact@v4
  if: always()
  with: { name: agenteval, path: .agenteval/ }
```

**Azure DevOps**: run the same command, then use the `PublishTestResults@2`
task with `testResultsFormat: JUnit` and `testResultsFiles: .agenteval/junit.xml`,
and `PublishBuildArtifacts@1` for `.agenteval/`.

## Artifacts

- `--json-out`: full machine-readable run artifact (timestamps, per-run records,
  assertion results, baseline comparison events, status, exit code, and
  `artifact_version` for forward compatibility; `baseline promote`/`diff` accept
  older artifacts without a version when the shape is recognizable and reject
  unsupported future versions).
- `--junit-out`: JUnit XML (stdlib ElementTree) — one testsuite, one testcase per
  scenario; assertion failures become `<failure>`, agent exceptions `<error>`,
  `skip:` scenarios `<skipped>`. Works with GitHub/GitLab/Jenkins test reporters.
- `--markdown-out`: human-readable report with status, summary table, comparison
  events, and failed assertion details.

## Smoke test

```sh
# 1) First run creates the baseline and exits 0
uv run agenteval.py run examples/scenarios.yaml --agent examples.myagent:agent

# 2) Second identical run passes against the baseline and exits 0
uv run agenteval.py run examples/scenarios.yaml --agent examples.myagent:agent
```

To trigger a regression, edit `examples/myagent.py` and remove the refund branch
(delete the `if "refund" in text:` block so the agent always returns
`"How can I help?"`). Then:

```sh
# 3) Reports the regression and exits 1
uv run agenteval.py run examples/scenarios.yaml --agent examples.myagent:agent
```

Expected output includes:

```
FAIL refund_happy_path 0/1 runs passed
REGRESSED refund_happy_path 100% -> 0%
Overall: REGRESSED
Exit: 1
```

Restore the refund branch and the run goes green again. Delete
`.agenteval/baseline.json` (or pass `--update-baseline`) to reset the baseline.

## v0.3 design boundary

- Deterministic assertions remain the core product; every default-path feature
  works with zero model dependencies.
- Semantic/judge assertions remain optional future add-ons behind friendly
  import guards — never required, never installed automatically.
- No hosted service is required: state is a JSON baseline file and run
  artifacts on the local filesystem.
- Baseline updates should be explicit and reviewed — `baseline promote` /
  `--update-baseline`, committed alongside the change that caused them; the
  tool refuses silent overwrites and filtered partial baselines.
- CI should use `--require-baseline` so a fresh runner can never mint a green
  baseline by accident.

## Development

```sh
uv run pytest
```
