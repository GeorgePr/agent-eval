"""Release-tooling tests: tag/version matching and CI/CD workflow guardrails.

The guarantees these encode:
- A release tag can only ship artifacts built from the matching __version__.
- The workflow files stay free-tier and token-free: Linux-only, no PyPI API
  tokens, and PyPI publishing stays manual + OIDC-gated (never automatic).
"""

from pathlib import Path

import pytest

from scripts import check_version as cv

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


# ---------------------------------------------------------------------------
# tag / version matching helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tag, version, expected",
    [
        ("v0.4.0", "0.4.0", True),
        ("0.4.0", "0.4.0", True),
        ("refs/tags/v0.4.0", "0.4.0", True),
        ("v0.4.0", "0.5.0", False),
        ("v0.4.1", "0.4.0", False),
        ("release-0.4.0", "0.4.0", False),
    ],
)
def test_check_tag_matches_version(tag, version, expected):
    assert cv.check_tag_matches_version(tag, version) is expected


def test_check_version_matches_current_package():
    """The current tag convention must match the shipped __version__."""
    from agenteval import __version__

    assert cv.check_tag_matches_version(f"v{__version__}", __version__)


def test_check_version_main_reports_mismatch(capsys):
    rc = cv.main(["v9.9.9"])
    assert rc == 1
    assert "MISMATCH" in capsys.readouterr().err


def test_check_version_main_accepts_current(capsys):
    from agenteval import __version__

    assert cv.main([f"v{__version__}"]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_version_main_usage_error():
    assert cv.main([]) == 2


# ---------------------------------------------------------------------------
# workflow file guardrails (text assertions — no YAML parser dependency)
# ---------------------------------------------------------------------------

def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def test_workflow_files_exist():
    assert (WORKFLOWS / "ci.yml").exists()
    assert (WORKFLOWS / "release.yml").exists()


def test_ci_triggers_on_push_and_pull_request():
    ci = _read("ci.yml")
    assert "pull_request" in ci
    assert "push" in ci


def test_release_is_tag_driven():
    rel = _read("release.yml")
    assert "tags:" in rel
    assert 'v*' in rel


def test_release_verifies_tag_version_match():
    assert "check_version.py" in _read("release.yml")


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_workflows_are_linux_only(name):
    text = _read(name)
    assert "ubuntu-latest" in text
    assert "macos-latest" not in text
    assert "windows-latest" not in text


def test_workflows_have_no_pypi_api_tokens():
    """No long-lived PyPI credentials anywhere in the workflows."""
    for name in ("ci.yml", "release.yml", "publish.yml"):
        path = WORKFLOWS / name
        if not path.exists():
            continue
        text = path.read_text().lower()
        assert "pypi_api_token" not in text
        assert "twine_password" not in text
        assert "password:" not in text  # OIDC publishing uses no password


def test_ci_and_release_do_not_publish():
    for name in ("ci.yml", "release.yml"):
        assert "pypi-publish" not in _read(name)


def test_ci_uses_short_artifact_retention():
    assert "retention-days: 7" in _read("ci.yml")


# ---------------------------------------------------------------------------
# publish workflow (optional) must be manual + OIDC only
# ---------------------------------------------------------------------------

def test_publish_workflow_is_manual_and_oidc_only():
    path = WORKFLOWS / "publish.yml"
    if not path.exists():
        pytest.skip("publish.yml not present (documented manual setup only)")
    text = path.read_text()
    assert "workflow_dispatch" in text
    # Not triggered by push or tags.
    assert "\n  push:" not in text
    # Trusted Publishing via OIDC, gated behind an environment.
    assert "id-token: write" in text
    assert "environment: pypi" in text
