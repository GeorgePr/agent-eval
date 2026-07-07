# CI guide

AgentEval is built for CI: the process exit code is the contract, and the
artifacts are the reporting. This guide has copy-paste examples and the baseline
policy that keeps CI honest.

## CI-safe baseline policy

- **Always pass `--require-baseline` in CI.** Without a baseline file a first run
  bootstraps one and exits 0 — fine locally, unsafe in CI because a fresh runner
  would mint a green baseline and pass. With the flag, a missing baseline exits 2.
- **Commit your baseline** (`.agenteval/baseline.json`) so CI has something to
  diff against.
- **Do not use `--update-baseline` automatically in CI.** Baseline changes should
  be reviewed. Promote them through a normal pull request:

  ```sh
  agenteval run scenarios.yaml --agent m:f --json-out .agenteval/latest.json
  agenteval baseline diff --from .agenteval/latest.json --baseline .agenteval/baseline.json
  agenteval baseline promote --from .agenteval/latest.json \
    --baseline .agenteval/baseline.json --force
  # commit the updated baseline in the same PR as the agent change
  ```

## Exit codes

| code | meaning |
|------|---------|
| 0 | pass / baseline created / baseline updated |
| 1 | regression (or missing scenarios with `--fail-on-missing`) |
| 2 | invalid scenario / config / missing required baseline / bad filter / bad artifact |
| 3 | agent import failure |
| 4 | internal unexpected error |

## Generic shell CI

```sh
agenteval run scenarios.yaml \
  --agent myagent:agent \
  --require-baseline \
  --json-out .agenteval/latest.json \
  --junit-out .agenteval/junit.xml \
  --markdown-out .agenteval/report.md
# non-zero exit fails the job
```

## Jenkins

Publish the JUnit report and archive the JSON/Markdown artifacts:

```groovy
sh '''
  agenteval run scenarios.yaml --agent myagent:agent --require-baseline \
    --json-out .agenteval/latest.json \
    --junit-out .agenteval/junit.xml \
    --markdown-out .agenteval/report.md
'''
junit '.agenteval/junit.xml'
archiveArtifacts artifacts: '.agenteval/latest.json,.agenteval/report.md', allowEmptyArchive: true
```

## GitHub Actions

```yaml
- run: |
    uv run agenteval.py run scenarios.yaml \
      --agent myagent:agent --require-baseline \
      --json-out .agenteval/latest.json \
      --junit-out .agenteval/junit.xml \
      --markdown-out .agenteval/report.md
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: agenteval
    path: .agenteval/
```

## Azure DevOps

```yaml
- script: |
    agenteval run scenarios.yaml --agent myagent:agent --require-baseline \
      --json-out .agenteval/latest.json \
      --junit-out .agenteval/junit.xml \
      --markdown-out .agenteval/report.md
  displayName: AgentEval
- task: PublishTestResults@2
  condition: always()
  inputs:
    testResultsFormat: JUnit
    testResultsFiles: .agenteval/junit.xml
- task: PublishBuildArtifacts@1
  condition: always()
  inputs:
    pathToPublish: .agenteval
    artifactName: agenteval
```

## HTTP target in CI

Test an already-deployed agent over HTTP instead of importing it.

Custom header:

```sh
agenteval run scenarios.yaml \
  --target https://staging.example.com/agent \
  --http-input-key message \
  --http-header "X-Environment: ci" \
  --require-baseline --junit-out .agenteval/junit.xml
```

Bearer token from the environment (the secret never appears on the command line
or in config):

```sh
AGENT_TOKEN=$SECRET agenteval run scenarios.yaml \
  --target https://staging.example.com/agent \
  --http-bearer-token-env AGENT_TOKEN \
  --require-baseline
```

GET-style agent (input sent as a query parameter):

```sh
agenteval run scenarios.yaml \
  --target "https://staging.example.com/agent" \
  --http-method GET \
  --http-input-key q \
  --require-baseline
```

HTTP options can also live in `.agenteval.yaml` (`http_timeout`, `http_headers`,
`http_bearer_token_env`, `http_method`); CLI flags override config values.

---

# AgentEval's own CI/CD (this repo)

Everything above is about running AgentEval inside *your* pipeline. This section
documents how *this repository* is tested and released. See also
[release-strategy.md](release-strategy.md) and [publishing.md](publishing.md).

## What `ci.yml` does

Runs on every push and pull request (`.github/workflows/ci.yml`): tests
(`uv run pytest`), lint (`uvx ruff check .`), `uv build`, a direct-script smoke
test (`--version`, `selftest`), and an installed-wheel smoke test (install the
built wheel into a temp venv and run `agenteval --version` / `agenteval selftest`).
It uploads `dist/*.whl` and `dist/*.tar.gz` with 7-day retention.

## What `release.yml` does

Triggered by pushing a `v*` tag. It verifies the tag matches
`agenteval.__version__` (via `scripts/check_version.py`), re-runs tests + lint +
selftest + build on the tagged commit, uploads artifacts (30-day retention), and
creates/updates a GitHub Release with the wheel + sdist attached. It does **not**
publish to PyPI.

## Reading failures

- **Tests/lint red:** open the failed step's log; `pytest`/`ruff` print the exact
  file and line. Reproduce locally with `uv run pytest` / `uvx ruff check .`.
- **Installed-wheel step red but script step green:** a packaging problem (entry
  point / included files), not a logic bug. Reproduce with `scripts/release-check.sh`.
- **`release.yml` fails at "Verify tag matches version":** the tag and
  `__version__` disagree — fix `__version__` or delete/retag.

## Where artifacts go

Under the workflow run's **Artifacts** section (Actions tab → the run). CI
artifacts expire after 7 days, release artifacts after 30; the permanent copies
live on the **GitHub Release** page for each tag. Retention is short on purpose —
it keeps storage usage low.

## Why Linux-only initially

AgentEval is pure Python (stdlib + pyyaml). A macOS/Windows matrix would multiply
runner minutes for little added confidence. Start Linux-only; add other OSes only
if you actually support and test against them.

## Keeping Actions usage low

- Workflows use GitHub-hosted Ubuntu runners and are intentionally small — one
  short Linux job per run.
- Keep artifact retention short and avoid unnecessary `schedule:` triggers.
- Avoid macOS/Windows and large matrices unless you need them.
- Monitor Actions usage in your GitHub settings.

## Running the pipeline elsewhere

The pipeline is just `uv` + `ruff` + `pytest` commands, so it runs anywhere:

- Run `scripts/release-check.sh` locally and release by hand — no CI service needed.
- Add a **GitHub Actions self-hosted runner** on a homelab box or Raspberry Pi.
- Use **Jenkins** on a homelab with a job running the same commands.
- Use **Forgejo/Gitea + Woodpecker CI**, fully self-hosted and open-source.
