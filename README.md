# agenteval

CLI-first agent regression testing. The smallest useful agent reliability layer:

```
run agent -> score behavior -> diff against baseline -> exit non-zero on regression
```

Deterministic-first and CI-friendly: the default path needs **zero** LLM/model
dependencies. No dashboard, no service, no database — just YAML scenarios, a JSON
baseline file on disk, and exit codes CI can trust.

## Usage

```sh
uv run agenteval.py run scenarios.yaml --agent myagent:agent
```

Options:

```sh
uv run agenteval.py run scenarios.yaml --agent myagent:agent --baseline .agenteval/baseline.json
uv run agenteval.py run scenarios.yaml --agent myagent:agent --json-out .agenteval/latest.json
uv run agenteval.py run scenarios.yaml --agent myagent:agent --runs 5
uv run agenteval.py run scenarios.yaml --agent myagent:agent --update-baseline
uv run agenteval.py run scenarios.yaml --agent myagent:agent --fail-on-missing
```

The agent is any Python callable `module:function` that takes the scenario input
string and returns either a string or a dict like
`{"output": "...", "tool_calls": ["..."], "steps": 2}`.

## Baseline behavior

- **First run** (no baseline file): current results are written as the baseline, exit 0.
- **Later runs**: results are diffed against the baseline.
  - Lower pass rate for a known scenario, or a previously-passing assertion now
    failing → `REGRESSED`, exit 1.
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
| 2 | invalid scenario file |
| 3 | agent import failure |
| 4 | internal unexpected error |

## Assertion types

Deterministic (no model dependencies): `contains`, `not_contains`, `used_tool`,
`not_used_tool`, `max_steps`, `min_steps`, `equals` (normalized, case-insensitive),
`regex`, `json_path_equals` / `json_path_exists` (simple `$.field` paths against the
raw result dict).

Optional stubs (never required, import-guarded): `semantic` (needs
`sentence-transformers` installed separately), `judge` (needs an optional judge
backend configured). Neither is used by the default smoke test.

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

## Development

```sh
uv run pytest
```
