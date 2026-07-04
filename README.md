# AgentEval

CLI-first agent regression testing. The smallest useful agent reliability layer:

```
run agent -> score behavior -> diff against baseline -> exit non-zero on regression
```

No dashboard, no service, no database — YAML scenarios, a JSON baseline file on
disk, and exit codes CI can trust. Runtime dependency: **pyyaml only**.

## Why deterministic assertions are the core

Regression testing has to be trustworthy and cheap to run on every commit.
AgentEval scores agent behavior with **deterministic assertions** — string,
tool, step, timing, and JSON-path checks that need zero model dependencies and
give the same answer every time. Optional `semantic`/`judge` assertions exist
only as import-guarded stubs; they are never required, never installed
automatically, and never used in the default path.

## Quickstart

An agent is any Python callable `module:function` taking the scenario input
string and returning a string or a dict like
`{"output": "...", "tool_calls": ["..."], "steps": 2}`.

```sh
uv run agenteval.py run examples/scenarios.yaml --agent examples.myagent:agent
# first run creates the baseline (exit 0); run again to diff against it
```

Installed as a command (see [Packaging](#packaging)), the same works as:

```sh
agenteval run scenarios.yaml --agent myagent:agent
```

## Fastest proof it works

```sh
uv run agenteval.py selftest
```

Builds a throwaway agent + scenarios in a temp dir and proves the core loop:
baseline creation → passing run → regression exits 1. No setup, network, LLM, or
examples required. Use `--keep-dir` to inspect the workspace.

## Sanity-check your setup

```sh
uv run agenteval.py doctor --scenario-file scenarios.yaml
```

Checks Python, pyyaml, config, scenario file, baseline, and artifact-directory
writability — without importing or calling your agent. Exit 0 healthy, 2 on
problems.

## Local bootstrap

```sh
uv run agenteval.py init          # scaffold .agenteval/, config, scenarios.yaml
uv run agenteval.py validate scenarios.yaml    # validate without running an agent
uv run agenteval.py run scenarios.yaml --agent myagent:agent
```

`init` never overwrites existing files without `--force`.

## CI-safe run

```sh
uv run agenteval.py run scenarios.yaml \
  --agent myagent:agent \
  --require-baseline \
  --json-out .agenteval/latest.json \
  --junit-out .agenteval/junit.xml \
  --markdown-out .agenteval/report.md
```

`--require-baseline` makes a missing baseline exit 2 instead of silently
bootstrapping one — the key CI safety switch. Commit `.agenteval/baseline.json`
so CI has something to diff against. Full CI recipes (Jenkins, GitHub Actions,
Azure DevOps) are in [docs/ci.md](docs/ci.md).

## HTTP target

Test an already-running agent over HTTP instead of importing it
(`--agent` and `--target` are mutually exclusive):

```sh
uv run agenteval.py run scenarios.yaml \
  --target http://localhost:8000/agent \
  --http-input-key message \
  --http-header "X-Environment: ci" \
  --require-baseline
```

Uses stdlib `urllib` — no requests/httpx. JSON responses with
`output`/`tool_calls`/`steps` are scored by the normal assertions; non-2xx
responses become scenario failures, never crashes. Bearer tokens
(`--http-bearer-token-env`), custom methods (`--http-method GET|POST|PUT|PATCH`),
and timeouts (`--http-timeout`) are documented in [docs/ci.md](docs/ci.md).

## Baseline promotion

The baseline is never silently overwritten. Update it explicitly and review it
like code:

```sh
uv run agenteval.py run scenarios.yaml --agent myagent:agent \
  --json-out .agenteval/latest.json
uv run agenteval.py baseline diff --from .agenteval/latest.json \
  --baseline .agenteval/baseline.json          # exit 1 on regression
uv run agenteval.py baseline promote --from .agenteval/latest.json \
  --baseline .agenteval/baseline.json --force  # accept as new reference
```

`baseline show` prints the current reference. Commit the promoted baseline in the
same PR as the change that caused it.

## Packaging

`pyproject.toml` uses a hatchling build backend and exposes an `agenteval`
console script (`agenteval = "agenteval:main"`), with the version sourced from
`__version__` in `agenteval.py`. Build with `uv build`; check with
`uv run agenteval.py --version`. Direct script execution
(`uv run agenteval.py ...`) is always supported.

## Exit codes

| code | meaning |
|------|---------|
| 0 | pass, baseline created, or baseline updated |
| 1 | regression (or missing scenarios with `--fail-on-missing`) |
| 2 | invalid scenario / config / missing required baseline / bad filter / bad artifact |
| 3 | agent import failure |
| 4 | internal unexpected error |

## Docs

- [Scenario format](docs/scenario-format.md) — YAML schema and every assertion type.
- [Artifacts](docs/artifacts.md) — JSON run artifact, baseline, JUnit, Markdown.
- [CI guide](docs/ci.md) — Jenkins / GitHub Actions / Azure DevOps, HTTP targets.

## Design boundary

- **No hosted service required** — state is a JSON baseline and run artifacts on
  the local filesystem.
- **No LLM required** — deterministic assertions are the whole default path.
- **Semantic/judge remain optional future add-ons** behind friendly import
  guards; never required, never auto-installed.
- **Baseline updates are explicit and reviewed** — the tool refuses silent
  overwrites and filtered partial baselines.

## Development

```sh
uv run pytest
```
