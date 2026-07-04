# Artifacts

AgentEval writes four kinds of files. The exit code is always the contract;
artifacts are for reporting, debugging, and reviewed baseline updates.

| Artifact | Flag / source | Format | Primary audience |
|----------|---------------|--------|------------------|
| Run artifact | `--json-out` | JSON | CI, `baseline promote`/`diff`, debugging |
| Baseline | `--baseline` (auto-created) | JSON | the tool; reviewed in PRs |
| JUnit report | `--junit-out` | XML | CI test reporters |
| Markdown report | `--markdown-out` | Markdown | humans |

## Run artifact (`--json-out`)

The full machine-readable record of one run. Top-level fields include
`artifact_version`, `started_at`, `completed_at`, `duration_ms`, `agent`,
`target`, `scenarios_file`, `scenario_count`, `runs_per_scenario`, `total_runs`,
`overall_pass_rate`, `scenarios` (per-scenario results with per-run records and
assertion results), `skipped`, `baseline` (path/existed/created/updated),
`comparison.events`, `status`, and `exit_code`.

### `artifact_version`

Run artifacts carry `"artifact_version": 1`. `baseline promote` and
`baseline diff` read artifacts and:

- **Tolerate** artifacts with no `artifact_version` (pre-v0.3) when the rest of
  the shape is recognizable (a `scenarios` list of results with `id` and
  `pass_rate`).
- **Reject** an unsupported future `artifact_version` with exit 2.

Use the run artifact when you want to promote or diff a result **without
rerunning the agent**:

```sh
agenteval run scenarios.yaml --agent m:f --json-out .agenteval/latest.json
agenteval baseline diff --from .agenteval/latest.json --baseline .agenteval/baseline.json
agenteval baseline promote --from .agenteval/latest.json --baseline .agenteval/baseline.json --force
```

## Baseline (JSON)

The reference the current run is diffed against. Includes `version`,
`created_at`, and a `scenarios` map of `{pass_rate, min_pass_rate, passed, runs,
assertions}` per scenario id. It is a plain JSON file — commit it to your repo so
CI has something to diff against.

The baseline is **never silently overwritten**. It changes only via
`run --update-baseline`, `baseline promote`, or a first run with no baseline
present. Inspect it with `agenteval baseline show`.

## JUnit XML (`--junit-out`)

Standard JUnit: one `<testsuite>`, one `<testcase>` per scenario. Assertion
failures become `<failure>` (with expected/observed/reason text), agent
exceptions become `<error>`, and `skip:` scenarios become `<skipped>`. Point
your CI's test reporter at this file.

## Markdown report (`--markdown-out`)

A human-readable summary: overall status, agent/target, timings, a per-scenario
result table, comparison events, and failed-assertion details. Good for PR
comments and build summaries.
