"""v0.2 feature tests: JUnit XML, Markdown report, --require-baseline,
HTTP target adapter, tag/skip filtering, min_pass_rate, and config files.

The guarantees these encode:
- CI artifacts (JUnit/Markdown/JSON) must be valid and reflect the run.
- --require-baseline must make a fresh CI runner fail loudly, never mint a
  green baseline by accident.
- HTTP failures are scenario failures, never internal crashes.
- Filters must not distort baseline comparison (no fake MISSING).
- Everything still works with zero LLM/model dependencies.
"""

import json
import textwrap
import threading
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import agenteval as ae

GOOD_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        text = user_input.lower()
        if "explode" in text:
            raise RuntimeError("skipped scenario was executed!")
        if "refund" in text:
            return {
                "output": "Sure — I've started your refund.",
                "tool_calls": ["lookup_order"],
                "steps": 2,
            }
        return {"output": "How can I help?", "tool_calls": [], "steps": 1}
    """
)

BROKEN_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        return {"output": "How can I help?", "tool_calls": [], "steps": 1}
    """
)

RAISING_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        raise ValueError("agent blew up")
    """
)

OK_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        return {"output": "ok result", "tool_calls": [], "steps": 1}
    """
)

# Deterministic flakiness via a module-level call counter.
FLAKY_4_OF_5 = textwrap.dedent(
    """
    _calls = {"n": 0}
    def agent(user_input: str) -> dict:
        _calls["n"] += 1
        if _calls["n"] % 5 == 0:
            return {"output": "bad", "tool_calls": [], "steps": 1}
        return {"output": "ok result", "tool_calls": [], "steps": 1}
    """
)

FLAKY_3_OF_5 = textwrap.dedent(
    """
    _calls = {"n": 0}
    def agent(user_input: str) -> dict:
        _calls["n"] += 1
        if _calls["n"] % 5 in (4, 0):
            return {"output": "bad", "tool_calls": [], "steps": 1}
        return {"output": "ok result", "tool_calls": [], "steps": 1}
    """
)

REFUND_SCENARIO = textwrap.dedent(
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

TAGGED_SCENARIOS = textwrap.dedent(
    """
    - id: refund
      tags: [smoke, refund]
      input: "I want a refund"
      assert:
        - type: contains
          value: refund
    - id: greeting
      tags: [smoke]
      input: "hello"
      assert:
        - type: contains
          value: help
    - id: slow_one
      tags: [expensive]
      input: "hello again"
      assert:
        - type: contains
          value: help
    - id: skipped_one
      skip: true
      skip_reason: "waiting for fixture"
      input: "explode"
      assert:
        - type: contains
          value: anything
    """
)

FLAKY_SCENARIO = textwrap.dedent(
    """
    - id: flaky
      runs: 5
      min_pass_rate: 0.8
      input: "hi"
      assert:
        - type: contains
          value: ok
    """
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_agent(workspace: Path, code: str) -> str:
    name = f"agent_{uuid.uuid4().hex[:8]}"
    (workspace / f"{name}.py").write_text(code)
    return name


def write_scenarios(workspace: Path, content: str, name: str = "scenarios.yaml") -> Path:
    path = workspace / name
    path.write_text(content)
    return path


def run_cli(scenarios: Path, agent_module: str, *extra: str) -> int:
    return ae.main(["run", str(scenarios), "--agent", f"{agent_module}:agent", *extra])


# ---------------------------------------------------------------------------
# Feature 1 — JUnit XML output
# ---------------------------------------------------------------------------

def test_junit_written_on_pass(workspace, capsys):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0  # baseline
    assert run_cli(path, agent, "--junit-out", ".agenteval/junit.xml") == 0

    tree = ET.parse(workspace / ".agenteval/junit.xml")  # must parse
    suite = tree.getroot()
    assert suite.tag == "testsuite"
    assert suite.get("tests") == "1"
    assert suite.get("failures") == "0"
    assert suite.get("errors") == "0"
    case = suite.find("testcase")
    assert case.get("name") == "refund_happy_path"
    assert case.find("failure") is None


def test_junit_failure_element_on_regression_and_exit_code_unchanged(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    good = write_agent(workspace, GOOD_AGENT)
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(path, good) == 0
    # JUnit output must not change the CI contract: regression still exits 1.
    assert run_cli(path, broken, "--junit-out", ".agenteval/junit.xml") == 1

    suite = ET.parse(workspace / ".agenteval/junit.xml").getroot()
    assert suite.get("failures") == "1"
    failure = suite.find("testcase").find("failure")
    assert failure is not None
    assert "contains:refund" in failure.text
    assert "observed=" in failure.text
    assert "expected=" in failure.text


def test_junit_error_element_on_agent_exception(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    raising = write_agent(workspace, RAISING_AGENT)
    assert run_cli(path, raising, "--junit-out", "junit.xml") == 0  # baseline created

    suite = ET.parse(workspace / "junit.xml").getroot()
    assert suite.get("errors") == "1"
    error = suite.find("testcase").find("error")
    assert error is not None
    assert "agent blew up" in error.text


# ---------------------------------------------------------------------------
# Feature 2 — Markdown report output
# ---------------------------------------------------------------------------

def test_markdown_report_on_pass(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    assert run_cli(path, agent, "--markdown-out", "report.md") == 0
    md = (workspace / "report.md").read_text()
    assert "# AgentEval Report" in md
    assert "Status: **PASS**" in md
    assert "| refund_happy_path | PASS | 1/1 | 100% |" in md


def test_markdown_report_on_regression_with_json_out(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    good = write_agent(workspace, GOOD_AGENT)
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(path, good) == 0
    rc = run_cli(
        path, broken, "--markdown-out", "report.md", "--json-out", "latest.json"
    )
    assert rc == 1
    md = (workspace / "report.md").read_text()
    assert "Status: **REGRESSED**" in md
    assert "REGRESSED refund_happy_path 100% -> 0%" in md
    assert "does not contain" in md  # failed assertion reason
    # JSON artifact still works alongside markdown.
    artifact = json.loads((workspace / "latest.json").read_text())
    assert artifact["status"] == "regressed"
    assert artifact["exit_code"] == 1


# ---------------------------------------------------------------------------
# Feature 3 — --require-baseline for CI
# ---------------------------------------------------------------------------

def test_require_baseline_missing_exits_2_and_creates_nothing(workspace, capsys):
    """A fresh CI runner must fail loudly, not silently mint a green baseline."""
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--require-baseline") == 2
    assert "Baseline required but not found: .agenteval/baseline.json" in capsys.readouterr().err
    assert not (workspace / ".agenteval/baseline.json").exists()


def test_missing_baseline_without_flag_still_bootstraps(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    assert (workspace / ".agenteval/baseline.json").exists()


def test_require_baseline_with_existing_baseline_passes(workspace):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    assert run_cli(path, agent, "--require-baseline") == 0
    # Overwriting still requires the explicit flag, even with --require-baseline.
    before = (workspace / ".agenteval/baseline.json").read_text()
    assert run_cli(path, agent, "--require-baseline") == 0
    assert (workspace / ".agenteval/baseline.json").read_text() == before


# ---------------------------------------------------------------------------
# Feature 4 — HTTP target adapter
# ---------------------------------------------------------------------------

class _AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/boom":
            self._respond(500, "internal server error", "text/plain")
            return
        if self.path == "/echo-key":
            payload = {"output": f"received keys: {','.join(sorted(body))}"}
        else:
            text = str(body.get("input", "")).lower()
            if "refund" in text:
                payload = {
                    "output": "Sure — refund started.",
                    "tool_calls": ["lookup_order"],
                    "steps": 2,
                }
            else:
                payload = {"output": "How can I help?", "tool_calls": [], "steps": 1}
        self._respond(200, json.dumps(payload), "application/json")

    def _respond(self, status, body, content_type):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_agent(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), _AgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_target_json_response_is_scored_by_existing_assertions(workspace, http_agent):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    rc = ae.main(
        ["run", str(path), "--target", f"{http_agent}/agent", "--json-out", "latest.json"]
    )
    assert rc == 0  # baseline created; contains/used_tool/max_steps all scored
    artifact = json.loads((workspace / "latest.json").read_text())
    record = artifact["scenarios"][0]["run_records"][0]
    assert record["passed"] is True
    assert record["tool_calls"] == ["lookup_order"]
    assert record["steps"] == 2
    assert record["result"]["http_status"] == 200


def test_http_input_key_controls_post_body(workspace, http_agent):
    path = write_scenarios(
        workspace,
        textwrap.dedent(
            """
            - id: key_check
              input: "hello"
              assert:
                - type: equals
                  value: "received keys: message"
            """
        ),
    )
    rc = ae.main(
        ["run", str(path), "--target", f"{http_agent}/echo-key", "--http-input-key", "message"]
    )
    assert rc == 0


def test_agent_and_target_are_mutually_exclusive(workspace, capsys):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    rc = ae.main(
        ["run", str(path), "--agent", f"{agent}:agent", "--target", "http://localhost:1/x"]
    )
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_neither_agent_nor_target_exits_2(workspace, capsys):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    assert ae.main(["run", str(path)]) == 2
    assert "one of --agent" in capsys.readouterr().err


def test_http_500_is_scenario_failure_not_internal_crash(workspace, http_agent, capsys):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    rc = ae.main(
        ["run", str(path), "--target", f"{http_agent}/boom", "--json-out", "latest.json"]
    )
    assert rc == 0  # captured failure, baseline created — crucially NOT exit 4
    out = capsys.readouterr().out
    assert "FAIL refund_happy_path 0/1 runs passed" in out
    record = json.loads((workspace / "latest.json").read_text())["scenarios"][0]["run_records"][0]
    assert "HTTP 500" in record["error"]
    assert "internal server error" in record["error"]


# ---------------------------------------------------------------------------
# Feature 5 — tags, skip, filtering
# ---------------------------------------------------------------------------

def _ran_ids(workspace) -> list[str]:
    artifact = json.loads((workspace / "latest.json").read_text())
    return [sr["id"] for sr in artifact["scenarios"]]


def test_include_tag_runs_only_matching(workspace):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    rc = run_cli(path, agent, "--include-tag", "smoke", "--json-out", "latest.json")
    assert rc == 0
    assert _ran_ids(workspace) == ["refund", "greeting"]


def test_exclude_tag_excludes_matching(workspace):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    rc = run_cli(path, agent, "--exclude-tag", "expensive", "--json-out", "latest.json")
    assert rc == 0
    assert _ran_ids(workspace) == ["refund", "greeting"]


def test_scenario_flag_runs_only_selected_id(workspace):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    rc = run_cli(path, agent, "--scenario", "refund", "--json-out", "latest.json")
    assert rc == 0
    assert _ran_ids(workspace) == ["refund"]


def test_skipped_scenario_is_reported_not_executed(workspace, capsys):
    """The skip scenario's input makes the agent raise — if it ran, we'd see FAIL."""
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    rc = run_cli(path, agent, "--json-out", "latest.json")
    assert rc == 0
    out = capsys.readouterr().out
    assert "SKIP skipped_one (waiting for fixture)" in out
    assert "skipped_one" not in _ran_ids(workspace)
    artifact = json.loads((workspace / "latest.json").read_text())
    assert artifact["skipped"] == [{"id": "skipped_one", "skip_reason": "waiting for fixture"}]


def test_zero_runnable_scenarios_exits_2(workspace, capsys):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--include-tag", "no_such_tag") == 2
    assert "no runnable scenarios" in capsys.readouterr().err


def test_filtered_out_baseline_scenarios_are_not_missing(workspace, capsys):
    """A filter deselecting a scenario must not look like a deleted scenario."""
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--include-tag", "smoke") == 0  # baseline: refund+greeting
    capsys.readouterr()
    rc = run_cli(path, agent, "--scenario", "refund")
    assert rc == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out
    assert "Overall: PASS" in out


# ---------------------------------------------------------------------------
# Feature 6 — min_pass_rate and runs precedence
# ---------------------------------------------------------------------------

def test_min_pass_rate_met_4_of_5_passes(workspace, capsys):
    path = write_scenarios(workspace, FLAKY_SCENARIO)
    ok = write_agent(workspace, OK_AGENT)
    flaky = write_agent(workspace, FLAKY_4_OF_5)
    assert run_cli(path, ok) == 0  # baseline: passed at threshold 0.8
    capsys.readouterr()
    rc = run_cli(path, flaky)
    assert rc == 0  # 4/5 = 0.8 >= 0.8: tolerated flakiness, no regression
    out = capsys.readouterr().out
    assert "PASS flaky 4/5 runs passed (min_pass_rate 0.8)" in out


def test_min_pass_rate_not_met_3_of_5_regresses(workspace, capsys):
    path = write_scenarios(workspace, FLAKY_SCENARIO)
    ok = write_agent(workspace, OK_AGENT)
    flaky = write_agent(workspace, FLAKY_3_OF_5)
    assert run_cli(path, ok) == 0
    capsys.readouterr()
    rc = run_cli(path, flaky)
    assert rc == 1  # 3/5 = 0.6 < 0.8: threshold missed, regression
    out = capsys.readouterr().out
    assert "FAIL flaky 3/5 runs passed (min_pass_rate 0.8)" in out
    assert "REGRESSED flaky" in out


def test_invalid_min_pass_rate_exits_2(workspace, capsys):
    path = write_scenarios(
        workspace,
        textwrap.dedent(
            """
            - id: bad
              min_pass_rate: 1.5
              input: "hi"
              assert:
                - type: contains
                  value: ok
            """
        ),
    )
    agent = write_agent(workspace, OK_AGENT)
    assert run_cli(path, agent) == 2
    assert "min_pass_rate" in capsys.readouterr().err


def test_scenario_runs_used_and_cli_runs_overrides(workspace):
    path = write_scenarios(workspace, FLAKY_SCENARIO)
    ok = write_agent(workspace, OK_AGENT)
    assert run_cli(path, ok, "--json-out", "latest.json") == 0
    assert json.loads((workspace / "latest.json").read_text())["scenarios"][0]["runs"] == 5

    # Explicit --runs wins over the scenario-level 'runs: 5'.
    assert run_cli(path, ok, "--runs", "1", "--json-out", "latest.json") == 0
    assert json.loads((workspace / "latest.json").read_text())["scenarios"][0]["runs"] == 1


# ---------------------------------------------------------------------------
# Feature 7 — config file
# ---------------------------------------------------------------------------

def test_default_config_file_is_loaded_automatically(workspace):
    (workspace / ".agenteval.yaml").write_text("runs: 3\njson_out: latest.json\n")
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    artifact = json.loads((workspace / "latest.json").read_text())
    assert artifact["scenarios"][0]["runs"] == 3


def test_explicit_config_path_is_loaded(workspace):
    (workspace / "ci.yaml").write_text("baseline: custom/baseline.json\n")
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--config", "ci.yaml") == 0
    assert (workspace / "custom/baseline.json").exists()
    assert not (workspace / ".agenteval/baseline.json").exists()


def test_cli_overrides_config(workspace):
    (workspace / ".agenteval.yaml").write_text("runs: 3\njson_out: latest.json\n")
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--runs", "2") == 0
    artifact = json.loads((workspace / "latest.json").read_text())
    assert artifact["scenarios"][0]["runs"] == 2


def test_unknown_config_key_exits_2(workspace, capsys):
    (workspace / ".agenteval.yaml").write_text("basline: oops.json\n")
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 2
    assert "unknown config key 'basline'" in capsys.readouterr().err


def test_malformed_config_exits_2(workspace, capsys):
    (workspace / ".agenteval.yaml").write_text("- this\n- is a list\n")
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 2
    assert "config must be a mapping" in capsys.readouterr().err


def test_missing_explicit_config_exits_2(workspace, capsys):
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--config", "nope.yaml") == 2
    assert "config file not found" in capsys.readouterr().err


def test_config_include_tags_apply(workspace):
    (workspace / ".agenteval.yaml").write_text("include_tags:\n  - smoke\njson_out: latest.json\n")
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    assert _ran_ids(workspace) == ["refund", "greeting"]
