"""Core behavior tests for agenteval.

These tests encode the guarantees CI depends on:
- Non-zero exit on regression must be trustworthy.
- An existing baseline must never be silently overwritten.
- Deterministic assertions must work with zero LLM/model dependencies.
"""

import importlib.util
import json
import textwrap
import uuid
from pathlib import Path

import pytest

import agenteval as ae

GOOD_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        text = user_input.lower()
        if "refund" in text:
            return {
                "output": "Sure — I've started your refund.",
                "tool_calls": ["lookup_order"],
                "steps": 2,
            }
        return {"output": "How can I help?", "tool_calls": [], "steps": 1}
    """
)

# The "regressed" agent: refund handling was removed.
BROKEN_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        return {"output": "How can I help?", "tool_calls": [], "steps": 1}
    """
)

SCENARIOS_YAML = textwrap.dedent(
    """
    - id: refund_happy_path
      input: "I want a refund for order 123"
      assert:
        - type: contains
          value: refund
        - type: used_tool
          value: lookup_order
        - type: max_steps
          value: 5
    """
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolated cwd so baselines/agents don't leak between tests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_agent(workspace: Path, code: str) -> str:
    """Write an agent module with a unique name (avoids import cache collisions)."""
    name = f"agent_{uuid.uuid4().hex[:8]}"
    (workspace / f"{name}.py").write_text(code)
    return name


def write_scenarios(workspace: Path, content: str = SCENARIOS_YAML) -> Path:
    path = workspace / "scenarios.yaml"
    path.write_text(content)
    return path


def run_cli(scenarios: Path, agent_module: str, *extra: str) -> int:
    return ae.main(["run", str(scenarios), "--agent", f"{agent_module}:agent", *extra])


# ---------------------------------------------------------------------------
# 1-2. Scenario loading
# ---------------------------------------------------------------------------

def test_valid_scenario_yaml_loads(workspace):
    path = write_scenarios(workspace)
    scenarios = ae.load_scenarios(path)
    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "refund_happy_path"
    assert len(scenarios[0]["assert"]) == 3


@pytest.mark.parametrize(
    "bad_yaml, expected_msg",
    [
        ("just a string", "must be a list"),
        ("- input: no id here\n  assert:\n    - type: contains\n      value: x", "'id'"),
        ("- id: s1\n  input: hi\n  assert: []", "non-empty list"),
        (
            "- id: s1\n  input: hi\n  assert:\n    - type: telepathy\n      value: x",
            "unknown assertion type",
        ),
    ],
)
def test_invalid_scenario_yaml_fails_clearly(workspace, bad_yaml, expected_msg):
    path = write_scenarios(workspace, bad_yaml)
    with pytest.raises(ae.ScenarioError, match=expected_msg):
        ae.load_scenarios(path)


def test_invalid_scenario_file_exits_2(workspace, capsys):
    """CI needs a distinct exit code for config errors vs regressions."""
    path = write_scenarios(workspace, "not: a list")
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 2
    assert "invalid scenario file" in capsys.readouterr().err


def test_agent_import_failure_exits_3(workspace):
    path = write_scenarios(workspace)
    assert run_cli(path, "no_such_module_xyz") == 3


# ---------------------------------------------------------------------------
# 3-8. Deterministic assertions (zero model dependencies)
# ---------------------------------------------------------------------------

REFUND_RESULT = {
    "output": "Sure — I've started your refund.",
    "tool_calls": ["lookup_order"],
    "steps": 2,
}


def test_contains_passes():
    res = ae.evaluate_assertion({"type": "contains", "value": "REFUND"}, REFUND_RESULT)
    assert res["passed"] is True  # case-insensitive


def test_contains_fails():
    res = ae.evaluate_assertion({"type": "contains", "value": "exchange"}, REFUND_RESULT)
    assert res["passed"] is False
    assert "exchange" in res["reason"]
    assert res["observed"] == REFUND_RESULT["output"]


def test_used_tool_passes():
    res = ae.evaluate_assertion({"type": "used_tool", "value": "lookup_order"}, REFUND_RESULT)
    assert res["passed"] is True


def test_used_tool_fails():
    res = ae.evaluate_assertion({"type": "used_tool", "value": "issue_refund"}, REFUND_RESULT)
    assert res["passed"] is False
    assert res["observed"] == ["lookup_order"]


def test_max_steps_passes():
    res = ae.evaluate_assertion({"type": "max_steps", "value": 5}, REFUND_RESULT)
    assert res["passed"] is True


def test_max_steps_fails():
    res = ae.evaluate_assertion({"type": "max_steps", "value": 1}, REFUND_RESULT)
    assert res["passed"] is False
    assert res["observed"] == 2


def test_other_deterministic_assertions():
    assert ae.evaluate_assertion({"type": "not_contains", "value": "sorry"}, REFUND_RESULT)["passed"]
    assert ae.evaluate_assertion({"type": "not_used_tool", "value": "escalate"}, REFUND_RESULT)["passed"]
    assert ae.evaluate_assertion({"type": "min_steps", "value": 2}, REFUND_RESULT)["passed"]
    assert not ae.evaluate_assertion({"type": "min_steps", "value": 3}, REFUND_RESULT)["passed"]
    assert ae.evaluate_assertion(
        {"type": "equals", "value": "  sure — i've started your refund. "}, REFUND_RESULT
    )["passed"]
    assert ae.evaluate_assertion({"type": "regex", "value": r"refund\."}, REFUND_RESULT)["passed"]
    assert ae.evaluate_assertion(
        {"type": "json_path_equals", "path": "$.steps", "value": 2}, REFUND_RESULT
    )["passed"]
    assert ae.evaluate_assertion(
        {"type": "json_path_exists", "path": "$.tool_calls"}, REFUND_RESULT
    )["passed"]
    assert not ae.evaluate_assertion(
        {"type": "json_path_exists", "path": "$.nope"}, REFUND_RESULT
    )["passed"]


def test_agent_exception_is_captured_as_assertion_failure():
    res = ae.evaluate_assertion(
        {"type": "contains", "value": "refund"}, None, error="ValueError: boom"
    )
    assert res["passed"] is False
    assert "agent call failed" in res["reason"]


# ---------------------------------------------------------------------------
# 9-12. Baseline lifecycle and regression detection
# ---------------------------------------------------------------------------

def test_first_run_creates_baseline_and_exits_0(workspace, capsys):
    path = write_scenarios(workspace)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    baseline = json.loads((workspace / ".agenteval/baseline.json").read_text())
    assert baseline["version"] == 1
    assert baseline["scenarios"]["refund_happy_path"]["pass_rate"] == 1.0
    assert baseline["scenarios"]["refund_happy_path"]["assertions"]["contains:refund"] is True
    assert "Baseline created" in capsys.readouterr().out


def test_second_identical_run_passes(workspace, capsys):
    path = write_scenarios(workspace)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    capsys.readouterr()
    assert run_cli(path, agent) == 0
    out = capsys.readouterr().out
    assert "PASS refund_happy_path 1/1 runs passed" in out
    assert "Overall: PASS 100%" in out


def test_changed_agent_behavior_regresses_and_exits_1(workspace, capsys):
    """The core CI guarantee: breaking the agent must produce exit code 1."""
    path = write_scenarios(workspace)
    good = write_agent(workspace, GOOD_AGENT)
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(path, good) == 0  # creates baseline
    capsys.readouterr()

    assert run_cli(path, broken) == 1
    out = capsys.readouterr().out
    assert "FAIL refund_happy_path 0/1 runs passed" in out
    assert "REGRESSED refund_happy_path 100% -> 0%" in out
    assert "Overall: REGRESSED" in out
    assert "Exit: 1" in out


def test_regression_does_not_silently_overwrite_baseline(workspace):
    """A regressed run must not rewrite history — otherwise the next run passes."""
    path = write_scenarios(workspace)
    good = write_agent(workspace, GOOD_AGENT)
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(path, good) == 0
    baseline_path = workspace / ".agenteval/baseline.json"
    before = baseline_path.read_text()

    assert run_cli(path, broken) == 1
    assert baseline_path.read_text() == before
    # ...and the regression is still detected on repeat runs.
    assert run_cli(path, broken) == 1


def test_update_baseline_flag_overwrites_and_exits_0(workspace):
    path = write_scenarios(workspace)
    good = write_agent(workspace, GOOD_AGENT)
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(path, good) == 0
    assert run_cli(path, broken, "--update-baseline") == 0
    baseline = json.loads((workspace / ".agenteval/baseline.json").read_text())
    assert baseline["scenarios"]["refund_happy_path"]["pass_rate"] == 0.0
    # New baseline accepted: the broken behavior is now the reference point.
    assert run_cli(path, broken) == 0


def test_new_and_missing_scenarios_reported_without_failing(workspace, capsys):
    path = write_scenarios(workspace)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    capsys.readouterr()

    other = write_scenarios(
        workspace,
        textwrap.dedent(
            """
            - id: greeting
              input: "hello"
              assert:
                - type: contains
                  value: help
            """
        ),
    )
    assert run_cli(other, agent) == 0  # NEW + MISSING are informational by default
    out = capsys.readouterr().out
    assert "NEW greeting" in out
    assert "MISSING refund_happy_path" in out
    # Baseline untouched: new scenarios only enter via --update-baseline.
    baseline = json.loads((workspace / ".agenteval/baseline.json").read_text())
    assert list(baseline["scenarios"]) == ["refund_happy_path"]

    assert run_cli(other, agent, "--fail-on-missing") == 1


def test_multiple_runs_per_scenario(workspace):
    path = write_scenarios(workspace)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--runs", "3") == 0
    baseline = json.loads((workspace / ".agenteval/baseline.json").read_text())
    assert baseline["scenarios"]["refund_happy_path"]["runs"] == 3


# ---------------------------------------------------------------------------
# 13. JSON artifact
# ---------------------------------------------------------------------------

def test_json_out_writes_full_artifact(workspace):
    path = write_scenarios(workspace)
    agent = write_agent(workspace, GOOD_AGENT)
    out_path = workspace / ".agenteval/latest.json"
    assert run_cli(path, agent, "--json-out", str(out_path)) == 0

    artifact = json.loads(out_path.read_text())
    assert artifact["status"] == "baseline_created"
    assert artifact["exit_code"] == 0
    assert artifact["agent"] == f"{agent}:agent"
    assert artifact["scenario_count"] == 1
    assert artifact["runs_per_scenario"] == 1
    assert artifact["started_at"] and artifact["completed_at"]
    assert artifact["duration_ms"] >= 0
    run_record = artifact["scenarios"][0]["run_records"][0]
    assert run_record["passed"] is True
    assert run_record["duration_ms"] >= 0
    assert {a["key"]: a["passed"] for a in run_record["assertions"]} == {
        "contains:refund": True,
        "used_tool:lookup_order": True,
        "max_steps:5": True,
    }


# ---------------------------------------------------------------------------
# 14. Optional assertions fail gracefully without their dependencies
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is not None,
    reason="sentence-transformers happens to be installed",
)
def test_semantic_assertion_fails_gracefully_without_dependency():
    res = ae.evaluate_assertion({"type": "semantic", "value": "a refund was issued"}, REFUND_RESULT)
    assert res["passed"] is False
    assert res["reason"] == "semantic assertion requires optional dependency: sentence-transformers"


def test_judge_assertion_fails_gracefully_without_backend():
    res = ae.evaluate_assertion({"type": "judge", "value": "is the reply polite?"}, REFUND_RESULT)
    assert res["passed"] is False
    assert res["reason"] == "judge assertion requires configuring an optional judge backend"


def test_optional_assertions_do_not_crash_a_full_run(workspace):
    """A scenario using optional assertions must degrade to a normal failure, not a crash."""
    path = write_scenarios(
        workspace,
        textwrap.dedent(
            """
            - id: semantic_scenario
              input: "I want a refund"
              assert:
                - type: semantic
                  value: "a refund was started"
                - type: judge
            """
        ),
    )
    agent = write_agent(workspace, GOOD_AGENT)
    # First run records the (failing) state as baseline and exits 0 — no crash, no exit 4.
    assert run_cli(path, agent) == 0
