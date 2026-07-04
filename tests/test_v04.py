"""v0.4 tests: --version/packaging entrypoint, doctor, selftest, and CLI
contract/help stability.

The guarantees these encode:
- The public CLI surface (command names, --help, --version) stays stable.
- doctor and selftest never import or call a user agent and need no examples.
- Expected user errors exit with clean codes, never a Python traceback.
"""

import textwrap
from pathlib import Path

import pytest

import agenteval as ae


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def expect_systemexit(argv) -> int:
    with pytest.raises(SystemExit) as exc:
        ae.main(argv)
    code = exc.value.code
    return 0 if code is None else code


VALID_SCENARIOS = textwrap.dedent(
    """
    - id: refund
      tags: [smoke]
      input: "I want a refund"
      assert:
        - type: contains
          value: refund
    """
)


# ---------------------------------------------------------------------------
# Feature 1 — version / entrypoint
# ---------------------------------------------------------------------------

def test_version_flag_prints_and_exits_0(capsys):
    assert expect_systemexit(["--version"]) == 0
    assert ae.__version__ in capsys.readouterr().out


def test_version_constant_is_a_semver_string():
    parts = ae.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_main_is_callable_and_returns_int(workspace):
    """The packaged console script is `agenteval = agenteval:main`; main must
    stay importable and return an int exit code."""
    path = write(workspace / "scenarios.yaml", VALID_SCENARIOS)
    rc = ae.main(["validate", str(path)])
    assert isinstance(rc, int)
    assert rc == 0


# ---------------------------------------------------------------------------
# Feature 2 — doctor
# ---------------------------------------------------------------------------

def test_doctor_healthy_exits_0(workspace, capsys):
    assert ae.main(["init"]) == 0
    capsys.readouterr()
    rc = ae.main(["doctor", "--scenario-file", "scenarios.yaml"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "HEALTHY" in out
    assert "Python: OK" in out
    assert "PyYAML: OK" in out


def test_doctor_malformed_config_exits_2(workspace, capsys):
    write(workspace / "bad.yaml", "- a\n- list\n")
    rc = ae.main(["doctor", "--config", "bad.yaml"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "UNHEALTHY" in out
    assert "Config: FAIL" in out


def test_doctor_invalid_scenarios_exits_2(workspace, capsys):
    write(workspace / "bad.yaml", "- id: x\n  input: hi\n  assert:\n    - type: nope\n      value: 1")
    rc = ae.main(["doctor", "--scenario-file", "bad.yaml"])
    assert rc == 2
    assert "Scenarios: FAIL" in capsys.readouterr().out


def test_doctor_does_not_import_agent(workspace, capsys):
    """A workspace with a broken agent module must still pass doctor: doctor
    never imports agents. If it did, this module would raise on import."""
    write(workspace / "myagent.py", "raise RuntimeError('doctor imported me!')\n")
    write(workspace / "scenarios.yaml", VALID_SCENARIOS)
    rc = ae.main(["doctor", "--scenario-file", "scenarios.yaml"])
    assert rc == 0
    assert "HEALTHY" in capsys.readouterr().out


def test_doctor_reports_missing_baseline_without_crashing(workspace, capsys):
    write(workspace / "scenarios.yaml", VALID_SCENARIOS)
    rc = ae.main(["doctor", "--scenario-file", "scenarios.yaml", "--baseline", "nope.json"])
    out = capsys.readouterr().out
    assert rc == 0  # missing baseline is informational, not a failure
    assert "Baseline: none yet" in out


def test_doctor_flags_unwritable_artifact_dir(workspace, capsys, monkeypatch):
    write(workspace / ".agenteval.yaml", "json_out: out/latest.json\n")
    write(workspace / "scenarios.yaml", VALID_SCENARIOS)
    monkeypatch.setattr(ae, "_dir_writable", lambda p: False)
    rc = ae.main(["doctor", "--scenario-file", "scenarios.yaml"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "Artifact dir (json_out): FAIL" in out


def test_doctor_invalid_baseline_exits_2(workspace, capsys):
    write(workspace / "corrupt.json", "{not json")
    rc = ae.main(["doctor", "--baseline", "corrupt.json"])
    assert rc == 2
    assert "Baseline: FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Feature 3 — selftest
# ---------------------------------------------------------------------------

def test_selftest_exits_0(workspace, capsys):
    rc = ae.main(["selftest"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "selftest: PASS" in out


def test_selftest_no_persistent_files_by_default(workspace):
    holder = workspace / "holder"
    holder.mkdir()
    assert ae.main(["selftest", "--tmp-dir", str(holder)]) == 0
    # The throwaway workspace was created under holder and cleaned up.
    assert list(holder.iterdir()) == []


def test_selftest_keep_dir_leaves_files(workspace, capsys):
    holder = workspace / "holder"
    holder.mkdir()
    assert ae.main(["selftest", "--tmp-dir", str(holder), "--keep-dir"]) == 0
    left = list(holder.iterdir())
    assert len(left) == 1 and left[0].is_dir()
    assert (left[0] / "baseline.json").exists()
    assert (left[0] / "scenarios.yaml").exists()
    assert "Kept workspace" in capsys.readouterr().out


def test_selftest_tmp_dir_must_exist(workspace):
    assert ae.main(["selftest", "--tmp-dir", "does-not-exist"]) == 2


def test_selftest_does_not_require_examples(workspace):
    """selftest builds its own agent/scenarios; no examples/ dir here at all."""
    assert not (workspace / "examples").exists()
    assert ae.main(["selftest"]) == 0


def test_evaluate_selftest_all_ok_with_stub(tmp_path):
    seq = iter([0, 0, 1])  # baseline, pass, regression
    results = ae.evaluate_selftest(tmp_path, run_fn=lambda a, c: next(seq))
    assert [r["ok"] for r in results] == [True, True, True]


def test_evaluate_selftest_detects_wrong_exit(tmp_path):
    # An agent that never regresses: the broken-agent step should be flagged.
    results = ae.evaluate_selftest(tmp_path, run_fn=lambda a, c: 0)
    assert results[-1]["ok"] is False


def test_selftest_failure_path_returns_nonzero(workspace, monkeypatch):
    # Force the broken-agent step to "not regress" -> selftest must fail loudly.
    monkeypatch.setattr(ae, "_selftest_run", lambda a, c: 0)
    assert ae.main(["selftest"]) == 4


# ---------------------------------------------------------------------------
# Feature 4 — CLI contract and help stability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["run", "--help"],
        ["init", "--help"],
        ["validate", "--help"],
        ["doctor", "--help"],
        ["selftest", "--help"],
        ["baseline", "--help"],
        ["baseline", "show", "--help"],
        ["baseline", "promote", "--help"],
        ["baseline", "diff", "--help"],
    ],
)
def test_help_exits_0(argv, capsys):
    assert expect_systemexit(argv) == 0
    # Help text should at least name the command/prog.
    assert capsys.readouterr().out.strip()


def test_top_level_help_lists_core_commands(capsys):
    expect_systemexit(["--help"])
    out = capsys.readouterr().out
    for command in ("run", "init", "validate", "doctor", "selftest", "baseline"):
        assert command in out


def test_run_help_lists_key_options(capsys):
    expect_systemexit(["run", "--help"])
    out = capsys.readouterr().out
    for opt in ("--agent", "--target", "--require-baseline", "--json-out", "--junit-out"):
        assert opt in out


def test_no_command_exits_2(capsys):
    # argparse: required subcommand missing.
    assert expect_systemexit([]) == 2


def test_invalid_command_exits_2(capsys):
    assert expect_systemexit(["frobnicate"]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_user_errors_have_no_traceback(workspace, capsys):
    """Every expected user error prints a clean message, never a Python traceback."""
    # Missing scenario file.
    assert ae.main(["run", "missing.yaml", "--agent", "x:y"]) == 2
    # Invalid baseline.
    write(workspace / "corrupt.json", "{not json")
    assert ae.main(["baseline", "show", "--baseline", "corrupt.json"]) == 2
    # Invalid artifact.
    write(workspace / "junk.json", "{}")
    assert ae.main(["baseline", "diff", "--from", "junk.json", "--baseline", "corrupt.json"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "ERROR:" in err


def test_missing_scenario_file_exits_2(workspace):
    assert ae.main(["validate", "definitely-missing.yaml"]) == 2


def test_agent_import_failure_still_exits_3(workspace):
    """Contract check: agent import failure is 3, distinct from user-config 2."""
    write(workspace / "scenarios.yaml", VALID_SCENARIOS)
    assert ae.main(["run", "scenarios.yaml", "--agent", "no_such_module:agent"]) == 3
