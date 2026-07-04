"""DevOps hardening tests: Dependabot, workflow hardening, release checksums,
and distribution sanity checks.

Text assertions are used for workflow YAML (no YAML parser dependency, per the
project's thin-deps rule). The checksum/verify scripts are exercised as real
importable functions against synthetic archives.
"""

import hashlib
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import build_checksums as bc
from scripts import verify_dist as vd

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


def _wf(name: str) -> str:
    return (WORKFLOWS / name).read_text()


# ---------------------------------------------------------------------------
# Phase 4 — Dependabot
# ---------------------------------------------------------------------------

def test_dependabot_exists_and_covers_both_ecosystems():
    text = (REPO / ".github" / "dependabot.yml").read_text()
    assert "github-actions" in text
    assert "pip" in text
    assert "weekly" in text


# ---------------------------------------------------------------------------
# Phase 5 — workflow hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ci.yml", "release.yml", "publish.yml"])
def test_workflows_have_job_timeouts(name):
    assert "timeout-minutes:" in _wf(name)


def test_ci_permissions_are_minimal():
    assert "contents: read" in _wf("ci.yml")


def test_release_has_contents_write():
    assert "contents: write" in _wf("release.yml")


def test_publish_uses_oidc_and_environment():
    text = _wf("publish.yml")
    assert "id-token: write" in text
    assert "environment: pypi" in text


def test_no_workflow_uses_api_token_or_password():
    for name in ("ci.yml", "release.yml", "publish.yml"):
        text = _wf(name).lower()
        assert "pypi_api_token" not in text
        assert "password:" not in text


@pytest.mark.parametrize("name", ["ci.yml", "release.yml", "publish.yml"])
def test_workflows_remain_linux_only(name):
    text = _wf(name)
    assert "ubuntu-latest" in text
    assert "macos-latest" not in text
    assert "windows-latest" not in text


def test_no_scheduled_workflows():
    # Cost control: no cron-triggered Actions runs.
    for name in ("ci.yml", "release.yml", "publish.yml"):
        assert "schedule:" not in _wf(name)


# ---------------------------------------------------------------------------
# Phase 6 — checksums (script + workflow wiring)
# ---------------------------------------------------------------------------

def _fake_dists(dist_dir: Path, wheel=True, sdist=True):
    if wheel:
        (dist_dir / "agenteval-9.9.9-py3-none-any.whl").write_bytes(b"fake wheel bytes")
    if sdist:
        (dist_dir / "agenteval-9.9.9.tar.gz").write_bytes(b"fake sdist bytes")


def test_build_checksums_writes_sha256sums(tmp_path):
    _fake_dists(tmp_path)
    out = bc.build_checksums(tmp_path)
    assert out == tmp_path / "SHA256SUMS"
    lines = out.read_text().splitlines()
    names = {line.split("  ", 1)[1] for line in lines}
    assert names == {"agenteval-9.9.9-py3-none-any.whl", "agenteval-9.9.9.tar.gz"}
    # Each line is "<64-hex>  <name>" and the digest is correct.
    for line in lines:
        digest, name = line.split("  ", 1)
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()


def test_build_checksums_fails_without_wheel(tmp_path):
    _fake_dists(tmp_path, wheel=False)
    with pytest.raises(FileNotFoundError, match="wheel"):
        bc.build_checksums(tmp_path)
    assert bc.main(["--dist-dir", str(tmp_path)]) == 2


def test_build_checksums_fails_without_sdist(tmp_path):
    _fake_dists(tmp_path, sdist=False)
    with pytest.raises(FileNotFoundError, match="sdist"):
        bc.build_checksums(tmp_path)


def test_ci_and_release_generate_and_ship_checksums():
    assert "SHA256SUMS" in _wf("ci.yml")
    assert "build_checksums.py" in _wf("ci.yml")
    rel = _wf("release.yml")
    assert "build_checksums.py" in rel
    assert "SHA256SUMS" in rel  # attached to the GitHub Release


# ---------------------------------------------------------------------------
# Phase 7 — distribution sanity checks
# ---------------------------------------------------------------------------

def _make_wheel(path: Path, members: dict[str, str]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def _make_sdist(path: Path, members: dict[str, str]):
    import io

    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


GOOD_WHEEL = {
    "agenteval.py": "x = 1\n",
    "agenteval-9.9.9.dist-info/METADATA": "Name: agenteval\n",
    "agenteval-9.9.9.dist-info/RECORD": "",
}
GOOD_SDIST = {
    "agenteval-9.9.9/agenteval.py": "x = 1\n",
    "agenteval-9.9.9/README.md": "# AgentEval\n",
    "agenteval-9.9.9/pyproject.toml": "[project]\n",
}


def test_verify_dist_passes_on_clean_artifacts(tmp_path):
    _make_wheel(tmp_path / "agenteval-9.9.9-py3-none-any.whl", GOOD_WHEEL)
    _make_sdist(tmp_path / "agenteval-9.9.9.tar.gz", GOOD_SDIST)
    assert vd.verify_dist(tmp_path) == []
    assert vd.main(["--dist-dir", str(tmp_path)]) == 0


def test_verify_dist_fails_when_wheel_missing_agenteval(tmp_path):
    bad = dict(GOOD_WHEEL)
    del bad["agenteval.py"]
    _make_wheel(tmp_path / "agenteval-9.9.9-py3-none-any.whl", bad)
    _make_sdist(tmp_path / "agenteval-9.9.9.tar.gz", GOOD_SDIST)
    problems = vd.verify_dist(tmp_path)
    assert any("agenteval.py" in p for p in problems)


def test_verify_dist_fails_when_sdist_missing_readme(tmp_path):
    bad = dict(GOOD_SDIST)
    del bad["agenteval-9.9.9/README.md"]
    _make_wheel(tmp_path / "agenteval-9.9.9-py3-none-any.whl", GOOD_WHEEL)
    _make_sdist(tmp_path / "agenteval-9.9.9.tar.gz", bad)
    problems = vd.verify_dist(tmp_path)
    assert any("README.md" in p for p in problems)


def test_verify_dist_flags_junk_paths(tmp_path):
    junk_sdist = dict(GOOD_SDIST)
    junk_sdist["agenteval-9.9.9/.github/workflows/ci.yml"] = "name: CI\n"
    _make_wheel(tmp_path / "agenteval-9.9.9-py3-none-any.whl", GOOD_WHEEL)
    _make_sdist(tmp_path / "agenteval-9.9.9.tar.gz", junk_sdist)
    problems = vd.verify_dist(tmp_path)
    assert any(".github" in p for p in problems)
    assert vd.main(["--dist-dir", str(tmp_path)]) == 2


def test_verify_dist_fails_when_no_artifacts(tmp_path):
    problems = vd.verify_dist(tmp_path)
    assert any("wheel" in p for p in problems)
    assert any("sdist" in p for p in problems)


def test_ci_and_release_verify_dist():
    assert "verify_dist.py" in _wf("ci.yml")
    assert "verify_dist.py" in _wf("release.yml")


# ---------------------------------------------------------------------------
# Community / governance files exist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "relpath",
    [
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/regression_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "SUPPORT.md",
        "docs/repo-governance.md",
        "docs/runbooks/ci-failure.md",
        "docs/runbooks/release.md",
        "docs/runbooks/rollback.md",
    ],
)
def test_governance_and_community_files_exist(relpath):
    assert (REPO / relpath).is_file()
