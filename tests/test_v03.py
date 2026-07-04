"""v0.3 feature tests: init, validate, baseline commands, partial-baseline
guard, HTTP timeout/headers/bearer/method controls, new deterministic
assertions, and artifact versioning.

The guarantees these encode:
- Onboarding (init) and validation never touch or import an agent.
- Baseline changes are explicit, reviewable operations (promote/diff),
  and a filtered first run can no longer silently mint a partial baseline.
- HTTP hardening (auth, headers, method, timeout) fails loudly at exit 2
  before any scenario runs.
- All new assertions stay deterministic and structurally reported.
"""

import json
import textwrap
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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

BROKEN_AGENT = textwrap.dedent(
    """
    def agent(user_input: str) -> dict:
        return {"output": "How can I help?", "tool_calls": [], "steps": 1}
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
    """
)

TAGGED_SCENARIOS = textwrap.dedent(
    """
    - id: refund
      tags: [smoke]
      input: "I want a refund"
      assert:
        - type: contains
          value: refund
    - id: greeting
      tags: [other]
      input: "hello"
      assert:
        - type: contains
          value: help
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
# Feature 1 — init command
# ---------------------------------------------------------------------------

def test_init_creates_files_in_empty_directory(workspace, capsys):
    assert ae.main(["init"]) == 0
    out = capsys.readouterr().out
    assert (workspace / ".agenteval").is_dir()
    assert (workspace / ".agenteval.yaml").exists()
    assert (workspace / "scenarios.yaml").exists()
    assert "created .agenteval.yaml" in out
    assert "created scenarios.yaml" in out
    # Generated config is a local bootstrap, not CI-locked.
    config = ae.load_config(workspace / ".agenteval.yaml")
    assert config.get("require_baseline") is not True


def test_init_does_not_overwrite_without_force(workspace, capsys):
    (workspace / "scenarios.yaml").write_text("# my precious scenarios\n")
    assert ae.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "skipped scenarios.yaml" in out
    assert (workspace / "scenarios.yaml").read_text() == "# my precious scenarios\n"


def test_init_force_overwrites(workspace):
    (workspace / "scenarios.yaml").write_text("# stale\n")
    assert ae.main(["init", "--force"]) == 0
    assert "smoke_basic" in (workspace / "scenarios.yaml").read_text()


def test_init_rejects_agent_and_target_together(workspace, capsys):
    assert ae.main(["init", "--agent", "m:f", "--target", "http://x/agent"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_init_generated_files_are_valid(workspace):
    assert ae.main(["init", "--http-input-key", "message"]) == 0
    scenarios = ae.load_scenarios(workspace / "scenarios.yaml")  # must validate
    assert scenarios[0]["id"] == "smoke_basic"
    config = ae.load_config(workspace / ".agenteval.yaml")  # must validate
    assert config["http_input_key"] == "message"
    # Generated scenarios use deterministic assertions only.
    for a in scenarios[0]["assert"]:
        assert a["type"] in ae.DETERMINISTIC_TYPES


# ---------------------------------------------------------------------------
# Feature 2 — validate command
# ---------------------------------------------------------------------------

def test_validate_valid_scenarios_exit_0(workspace, capsys):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    # No agent module exists anywhere in this workspace: validate must not need one.
    assert ae.main(["validate", str(path)]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "2 scenario(s)" in out


def test_validate_invalid_scenarios_exit_2(workspace, capsys):
    path = write_scenarios(
        workspace,
        "- id: bad\n  input: hi\n  assert:\n    - type: telepathy\n      value: x",
    )
    assert ae.main(["validate", str(path)]) == 2
    assert "unknown assertion type" in capsys.readouterr().err


def test_validate_malformed_config_exit_2(workspace, capsys):
    (workspace / ".agenteval.yaml").write_text("- a\n- list\n")
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    assert ae.main(["validate", str(path)]) == 2
    assert "config must be a mapping" in capsys.readouterr().err


def test_validate_unknown_config_key_exit_2(workspace, capsys):
    (workspace / ".agenteval.yaml").write_text("basline: x.json\n")
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    assert ae.main(["validate", str(path)]) == 2
    assert "unknown config key" in capsys.readouterr().err


def test_validate_zero_runnable_after_filters_exit_2(workspace, capsys):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    assert ae.main(["validate", str(path), "--include-tag", "no_such_tag"]) == 2
    assert "no runnable scenarios" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Feature 3 — baseline show / promote / diff
# ---------------------------------------------------------------------------

def _make_run(workspace, agent_code=GOOD_AGENT, *extra):
    """Run once to produce a baseline and latest.json; return exit code."""
    path = write_scenarios(workspace, REFUND_SCENARIO)
    agent = write_agent(workspace, agent_code)
    return run_cli(path, agent, "--json-out", "latest.json", *extra)


def test_baseline_show(workspace, capsys):
    assert _make_run(workspace) == 0
    capsys.readouterr()
    assert ae.main(["baseline", "show"]) == 0
    out = capsys.readouterr().out
    assert "refund_happy_path: pass_rate 100% (1 run(s))" in out
    assert "PASS contains:refund" in out


def test_baseline_show_missing_exits_2(workspace, capsys):
    assert ae.main(["baseline", "show", "--baseline", "nope.json"]) == 2
    assert "baseline file not found" in capsys.readouterr().err


def test_baseline_promote_creates_baseline_from_artifact(workspace, capsys):
    assert _make_run(workspace) == 0
    capsys.readouterr()
    rc = ae.main(
        ["baseline", "promote", "--from", "latest.json", "--baseline", "promoted.json"]
    )
    assert rc == 0
    assert "Promoted 1 scenario(s)" in capsys.readouterr().out
    baseline = json.loads((workspace / "promoted.json").read_text())
    assert baseline["scenarios"]["refund_happy_path"]["pass_rate"] == 1.0
    assert baseline["scenarios"]["refund_happy_path"]["assertions"]["contains:refund"] is True


def test_baseline_promote_refuses_invalid_artifact(workspace, capsys):
    (workspace / "junk.json").write_text('{"hello": "world"}')
    assert ae.main(["baseline", "promote", "--from", "junk.json", "--baseline", "b.json"]) == 2
    assert "does not look like an agenteval run artifact" in capsys.readouterr().err


def test_baseline_promote_refuses_regressed_artifact_unless_forced(workspace, capsys):
    assert _make_run(workspace) == 0  # good baseline + artifact
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(workspace / "scenarios.yaml", broken, "--json-out", "regressed.json") == 1
    capsys.readouterr()

    rc = ae.main(
        ["baseline", "promote", "--from", "regressed.json", "--baseline", "b2.json"]
    )
    assert rc == 2
    assert "REGRESSED" in capsys.readouterr().err
    assert not (workspace / "b2.json").exists()

    rc = ae.main(
        ["baseline", "promote", "--from", "regressed.json", "--baseline", "b2.json", "--force"]
    )
    assert rc == 0
    assert (workspace / "b2.json").exists()


def test_baseline_promote_refuses_overwrite_without_force(workspace, capsys):
    assert _make_run(workspace) == 0  # creates .agenteval/baseline.json + latest.json
    before = (workspace / ".agenteval/baseline.json").read_text()
    capsys.readouterr()
    assert ae.main(["baseline", "promote", "--from", "latest.json"]) == 2
    assert "pass --force to overwrite" in capsys.readouterr().err
    assert (workspace / ".agenteval/baseline.json").read_text() == before
    assert ae.main(["baseline", "promote", "--from", "latest.json", "--force"]) == 0


def test_baseline_diff_exit_codes(workspace, capsys):
    assert _make_run(workspace) == 0  # baseline from good agent
    broken = write_agent(workspace, BROKEN_AGENT)
    assert run_cli(workspace / "scenarios.yaml", broken, "--json-out", "regressed.json") == 1
    capsys.readouterr()

    # Passing artifact vs baseline: no regression, exit 0. No agent rerun needed.
    assert ae.main(["baseline", "diff", "--from", "latest.json"]) == 0
    assert "Overall: PASS" in capsys.readouterr().out

    assert ae.main(["baseline", "diff", "--from", "regressed.json"]) == 1
    out = capsys.readouterr().out
    assert "REGRESSED refund_happy_path 100% -> 0%" in out

    assert ae.main(["baseline", "diff", "--from", "missing.json"]) == 2


# ---------------------------------------------------------------------------
# Feature 4 — safer filtered baseline creation
# ---------------------------------------------------------------------------

def test_first_filtered_run_without_baseline_exits_2(workspace, capsys):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--include-tag", "smoke") == 2
    err = capsys.readouterr().err
    assert "Refusing to create a new baseline from a filtered run" in err
    assert "--allow-partial-baseline" in err
    assert not (workspace / ".agenteval/baseline.json").exists()


def test_first_filtered_run_with_allow_partial_creates_baseline(workspace):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent, "--include-tag", "smoke", "--allow-partial-baseline") == 0
    baseline = json.loads((workspace / ".agenteval/baseline.json").read_text())
    assert list(baseline["scenarios"]) == ["refund"]


def test_unfiltered_first_run_still_creates_baseline(workspace):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0
    assert (workspace / ".agenteval/baseline.json").exists()


def test_filtered_run_with_existing_baseline_unchanged(workspace, capsys):
    path = write_scenarios(workspace, TAGGED_SCENARIOS)
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 0  # full baseline
    capsys.readouterr()
    assert run_cli(path, agent, "--scenario", "refund") == 0  # no opt-in needed
    out = capsys.readouterr().out
    assert "MISSING" not in out


# ---------------------------------------------------------------------------
# Features 5+6 — HTTP timeout/headers/bearer/method
# ---------------------------------------------------------------------------

class _InspectHandler(BaseHTTPRequestHandler):
    """Echoes method, body/query keys+values, and selected request headers."""

    def _handle(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        merged = {**query, **body}
        text = str(merged.get("input", merged.get("message", ""))).lower()
        payload = {
            "output": (
                f"method={self.command} "
                f"input_value={merged} "
                f"auth={self.headers.get('Authorization', '')} "
                f"xtest={self.headers.get('X-Test', '')} "
                f"xenv={self.headers.get('X-Environment', '')}"
            ),
            "tool_calls": ["lookup_order", "refund_order"] if "refund" in text else [],
            "steps": 2,
            "message": "Your refund is in progress",
            "ticket_id": "TICKET-123",
            "labels": ["billing", "refund"],
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = _handle

    def log_message(self, *args):
        pass


@pytest.fixture
def http_agent(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), _InspectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/agent"
    server.shutdown()


def _http_run(workspace, http_agent, scenario_yaml, *extra):
    path = write_scenarios(workspace, scenario_yaml)
    return ae.main(["run", str(path), "--target", http_agent, *extra])


CONTAINS = "- id: s\n  input: \"refund please\"\n  assert:\n    - type: contains\n      value: \"{needle}\"\n"


def test_http_timeout_accepted_and_invalid_rejected(workspace, http_agent, capsys):
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=POST"),
                     "--http-timeout", "5") == 0
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=POST"),
                     "--http-timeout", "0") == 2
    assert "positive number" in capsys.readouterr().err


def test_repeated_http_headers_are_sent(workspace, http_agent):
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="xtest=yes"),
        "--http-header", "X-Test: yes", "--http-header", "X-Environment: ci",
    )
    assert rc == 0
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="xenv=ci"),
        "--http-header", "X-Test: yes", "--http-header", "X-Environment: ci",
        "--baseline", "b2.json",
    )
    assert rc == 0


def test_invalid_http_header_exits_2(workspace, http_agent, capsys):
    rc = _http_run(workspace, http_agent, CONTAINS.format(needle="x"),
                   "--http-header", "NoColonHere")
    assert rc == 2
    assert 'expected "Name: Value"' in capsys.readouterr().err


def test_bearer_token_env_sends_authorization(workspace, http_agent, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "sekrit-123")
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="auth=Bearer sekrit-123"),
        "--http-bearer-token-env", "AGENT_TOKEN",
    )
    assert rc == 0


def test_missing_bearer_token_env_exits_2(workspace, http_agent, capsys, monkeypatch):
    monkeypatch.delenv("NO_SUCH_TOKEN_VAR", raising=False)
    rc = _http_run(workspace, http_agent, CONTAINS.format(needle="x"),
                   "--http-bearer-token-env", "NO_SUCH_TOKEN_VAR")
    assert rc == 2
    assert "not set or empty" in capsys.readouterr().err


def test_authorization_header_conflicts_with_bearer_env(workspace, http_agent, capsys,
                                                        monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "t")
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="x"),
        "--http-header", "Authorization: Basic abc",
        "--http-bearer-token-env", "AGENT_TOKEN",
    )
    assert rc == 2
    assert "conflicts" in capsys.readouterr().err


def test_http_config_values_and_cli_override(workspace, http_agent, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "cfg-token")
    (workspace / ".agenteval.yaml").write_text(
        "http_timeout: 5\n"
        'http_headers:\n  - "X-Test: from-config"\n'
        "http_bearer_token_env: AGENT_TOKEN\n"
        "http_method: POST\n"
    )
    # Config header + bearer both arrive.
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="xtest=from-config")) == 0
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="auth=Bearer cfg-token"),
                     "--baseline", "b2.json") == 0
    # CLI header list replaces the config list entirely.
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="xtest=from-cli"),
                     "--http-header", "X-Test: from-cli", "--baseline", "b3.json") == 0


def test_http_method_default_post_and_get_query(workspace, http_agent):
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=POST")) == 0
    # GET sends the input as a query parameter (default key: input).
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="{'input': 'refund please'}"),
        "--http-method", "GET", "--baseline", "b2.json",
    )
    assert rc == 0
    # --http-input-key controls the query key too.
    rc = _http_run(
        workspace, http_agent, CONTAINS.format(needle="{'message': 'refund please'}"),
        "--http-method", "GET", "--http-input-key", "message", "--baseline", "b3.json",
    )
    assert rc == 0


def test_http_method_put_and_patch(workspace, http_agent):
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=PUT"),
                     "--http-method", "PUT") == 0
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=PATCH"),
                     "--http-method", "PATCH", "--baseline", "b2.json") == 0


def test_unsupported_http_method_exits_2(workspace, http_agent, capsys):
    rc = _http_run(workspace, http_agent, CONTAINS.format(needle="x"),
                   "--http-method", "DELETE")
    assert rc == 2
    assert "unsupported --http-method" in capsys.readouterr().err


def test_http_method_config_and_cli_override(workspace, http_agent):
    (workspace / ".agenteval.yaml").write_text("http_method: GET\n")
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=GET")) == 0
    assert _http_run(workspace, http_agent, CONTAINS.format(needle="method=PUT"),
                     "--http-method", "PUT", "--baseline", "b2.json") == 0


# ---------------------------------------------------------------------------
# Feature 7 — new deterministic assertions
# ---------------------------------------------------------------------------

HTTP_RESULT = {
    "output": "Sure — refund started.",
    "tool_calls": ["lookup_order", "refund_order"],
    "steps": 2,
    "http_status": 200,
    "message": "Your refund is in progress",
    "ticket_id": "TICKET-123",
    "labels": ["billing", "refund"],
}


def _eval(a, result=HTTP_RESULT, **kwargs):
    return ae.evaluate_assertion(a, result, **kwargs)


def test_status_code_pass_fail_and_non_http_mode():
    assert _eval({"type": "status_code", "value": 200})["passed"] is True
    res = _eval({"type": "status_code", "value": 404})
    assert res["passed"] is False and res["observed"] == 200
    # Plain module agents have no http_status: fail gracefully with a clear reason.
    res = _eval({"type": "status_code", "value": 200}, result={"output": "hi"})
    assert res["passed"] is False
    assert "--target HTTP mode" in res["reason"]


def test_max_duration_ms_pass_fail():
    assert _eval({"type": "max_duration_ms", "value": 2000}, duration_ms=100)["passed"] is True
    res = _eval({"type": "max_duration_ms", "value": 50}, duration_ms=100)
    assert res["passed"] is False
    assert res["observed"] == 100


def test_tool_call_count_pass_fail():
    assert _eval({"type": "tool_call_count", "value": 2})["passed"] is True
    res = _eval({"type": "tool_call_count", "value": 1})
    assert res["passed"] is False and res["observed"] == 2


def test_max_tool_calls_pass_fail():
    assert _eval({"type": "max_tool_calls", "value": 3})["passed"] is True
    assert _eval({"type": "max_tool_calls", "value": 1})["passed"] is False


def test_tool_sequence_pass_fail():
    ok = _eval({"type": "tool_sequence", "value": ["lookup_order", "refund_order"]})
    assert ok["passed"] is True
    bad = _eval({"type": "tool_sequence", "value": ["refund_order", "lookup_order"]})
    assert bad["passed"] is False
    assert bad["observed"] == ["lookup_order", "refund_order"]


def test_json_path_contains_pass_fail():
    assert _eval({"type": "json_path_contains", "path": "$.message", "value": "refund"})["passed"]
    assert not _eval({"type": "json_path_contains", "path": "$.message", "value": "exchange"})["passed"]
    # List membership.
    assert _eval({"type": "json_path_contains", "path": "$.labels", "value": "billing"})["passed"]
    # Missing path fails clearly.
    res = _eval({"type": "json_path_contains", "path": "$.nope", "value": "x"})
    assert res["passed"] is False and "does not exist" in res["reason"]


def test_json_path_regex_pass_fail():
    ok = _eval({"type": "json_path_regex", "path": "$.ticket_id", "value": "^TICKET-[0-9]+$"})
    assert ok["passed"] is True
    assert not _eval({"type": "json_path_regex", "path": "$.ticket_id", "value": "^ORDER"})["passed"]


def test_new_assertion_results_stay_structured():
    res = _eval({"type": "tool_sequence", "value": ["lookup_order", "refund_order"]})
    assert set(res) >= {"type", "passed", "expected", "observed", "reason"}


@pytest.mark.parametrize(
    "assertion_yaml, expected_msg",
    [
        ("- type: max_duration_ms\n      value: -5", "non-negative number"),
        ("- type: tool_sequence\n      value: lookup_order", "requires a list"),
        ("- type: status_code\n      value: \"200\"", "integer 'value'"),
        ("- type: tool_call_count\n      value: 1.5", "integer 'value'"),
    ],
)
def test_invalid_new_assertion_shapes_exit_2(workspace, capsys, assertion_yaml, expected_msg):
    path = write_scenarios(
        workspace,
        f"- id: s\n  input: hi\n  assert:\n    {assertion_yaml}\n",
    )
    agent = write_agent(workspace, GOOD_AGENT)
    assert run_cli(path, agent) == 2
    assert expected_msg in capsys.readouterr().err


def test_new_assertions_work_end_to_end_over_http(workspace, http_agent):
    scenario = textwrap.dedent(
        """
        - id: full_http_check
          input: "refund please"
          assert:
            - type: status_code
              value: 200
            - type: tool_sequence
              value: [lookup_order, refund_order]
            - type: tool_call_count
              value: 2
            - type: max_tool_calls
              value: 3
            - type: max_duration_ms
              value: 30000
            - type: json_path_contains
              path: "$.message"
              value: refund
            - type: json_path_regex
              path: "$.ticket_id"
              value: "^TICKET-[0-9]+$"
        """
    )
    assert _http_run(workspace, http_agent, scenario) == 0


# ---------------------------------------------------------------------------
# Feature 8 — artifact schema version
# ---------------------------------------------------------------------------

def test_current_artifact_includes_artifact_version(workspace):
    assert _make_run(workspace) == 0
    artifact = json.loads((workspace / "latest.json").read_text())
    assert artifact["artifact_version"] == 1


def test_promote_tolerates_artifact_without_version(workspace):
    """Pre-v0.3 artifacts have no artifact_version but a recognizable shape."""
    assert _make_run(workspace) == 0
    artifact = json.loads((workspace / "latest.json").read_text())
    del artifact["artifact_version"]
    (workspace / "old.json").write_text(json.dumps(artifact))
    rc = ae.main(["baseline", "promote", "--from", "old.json", "--baseline", "b2.json"])
    assert rc == 0


def test_unsupported_artifact_version_exits_2(workspace, capsys):
    assert _make_run(workspace) == 0
    artifact = json.loads((workspace / "latest.json").read_text())
    artifact["artifact_version"] = 99
    (workspace / "future.json").write_text(json.dumps(artifact))
    rc = ae.main(["baseline", "promote", "--from", "future.json", "--baseline", "b2.json"])
    assert rc == 2
    assert "unsupported artifact_version 99" in capsys.readouterr().err
