#!/bin/sh
# Run the same gates CI runs, locally, before tagging a release.
# POSIX sh, fail-fast. No network beyond uv's normal dependency resolution.
set -eu

echo "==> pytest"
uv run pytest -q

echo "==> ruff"
uvx ruff check .

echo "==> build"
rm -rf dist
uv build

echo "==> script selftest"
uv run agenteval.py --version
uv run agenteval.py selftest

echo "==> installed wheel smoke test"
rm -rf .relenv
uv venv .relenv
uv pip install --python .relenv/bin/python dist/*.whl
.relenv/bin/agenteval --version
.relenv/bin/agenteval selftest
rm -rf .relenv

echo ""
echo "release-check: OK — safe to tag. Next: update __version__/CHANGELOG if needed,"
echo "then 'git tag vX.Y.Z && git push origin vX.Y.Z'."
