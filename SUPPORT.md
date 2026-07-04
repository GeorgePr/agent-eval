# Support

## Look here first

Most questions are answered in the docs:

- [README](README.md) — quickstart, core thesis, common commands.
- [Scenario format](docs/scenario-format.md) — YAML schema and every assertion type.
- [Artifacts](docs/artifacts.md) — JSON run artifact, baseline, JUnit, Markdown.
- [CI guide](docs/ci.md) — using AgentEval in your pipeline, and this repo's CI/CD.
- [Release strategy](docs/release-strategy.md) — free-tier CI/CD and releases.
- [Publishing](docs/publishing.md) — optional, token-free PyPI publishing.

Quick self-checks:

```sh
uv run agenteval.py doctor --scenario-file scenarios.yaml   # is my setup sane?
uv run agenteval.py selftest                                # does the tool work at all?
```

## Asking for help

If the docs don't cover it, open an issue using the appropriate template
(bug / feature / regression). Please include:

- AgentEval version (`agenteval --version`) or commit SHA
- the exact command you ran and its exit code
- a minimal scenario snippet (redact secrets)
- expected vs. actual behavior
- a relevant artifact/log excerpt
- OS and Python version

For anything security-sensitive, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.
