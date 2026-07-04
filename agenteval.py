#!/usr/bin/env python3
"""agenteval — CLI-first agent regression testing.

The loop this tool proves:

    run agent -> score behavior -> diff against baseline -> exit non-zero on regression

Deterministic-first: every assertion in the default path works with zero model
dependencies. Optional semantic/judge assertions exist only as import-guarded
stubs and are never required.

Usage:

    uv run agenteval.py run scenarios.yaml --agent myagent:agent
    uv run agenteval.py run scenarios.yaml --target http://localhost:8000/agent

Exit codes:
    0  pass (or baseline created / explicitly updated)
    1  regression vs baseline (or missing scenarios with --fail-on-missing)
    2  invalid scenario file / invalid config / missing required baseline
    3  agent import failure
    4  internal unexpected error
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yaml

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INVALID_SCENARIOS = 2
EXIT_AGENT_ERROR = 3
EXIT_INTERNAL = 4

BASELINE_VERSION = 1
ARTIFACT_VERSION = 1
SUPPORTED_ARTIFACT_VERSIONS = {1}
DEFAULT_BASELINE = ".agenteval/baseline.json"
DEFAULT_CONFIG_FILE = ".agenteval.yaml"
HTTP_TIMEOUT_S = 30
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH"}

DETERMINISTIC_TYPES = {
    "contains",
    "not_contains",
    "used_tool",
    "not_used_tool",
    "max_steps",
    "min_steps",
    "equals",
    "regex",
    "json_path_equals",
    "json_path_exists",
    "status_code",
    "max_duration_ms",
    "tool_call_count",
    "max_tool_calls",
    "tool_sequence",
    "json_path_contains",
    "json_path_regex",
}
OPTIONAL_TYPES = {"semantic", "judge"}
KNOWN_TYPES = DETERMINISTIC_TYPES | OPTIONAL_TYPES

# Assertion types that must carry a "value" / "path" field to be meaningful.
REQUIRES_VALUE = KNOWN_TYPES - {"json_path_exists", "judge"}
REQUIRES_PATH = {"json_path_equals", "json_path_exists", "json_path_contains", "json_path_regex"}
REQUIRES_INT_VALUE = {"max_steps", "min_steps", "status_code", "tool_call_count", "max_tool_calls"}


class ScenarioError(Exception):
    """The scenario YAML is missing, unparseable, or fails schema validation."""


class AgentLoadError(Exception):
    """The --agent module:function spec could not be imported/resolved."""


class ConfigError(Exception):
    """Invalid CLI flag combination, config file, filter selection, or missing
    required baseline. Maps to exit 2, same as an invalid scenario file."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(rate: float) -> str:
    return f"{round(rate * 100)}%"


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

def load_scenarios(path: Path) -> list[dict]:
    if not path.exists():
        raise ScenarioError(f"scenario file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ScenarioError(f"could not parse YAML in {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ScenarioError(
            f"{path}: top level must be a list of scenarios, got {type(data).__name__}"
        )
    if not data:
        raise ScenarioError(f"{path}: scenario list is empty")

    seen_ids: set[str] = set()
    for i, scenario in enumerate(data):
        where = f"{path}: scenario #{i + 1}"
        if not isinstance(scenario, dict):
            raise ScenarioError(f"{where}: must be a mapping, got {type(scenario).__name__}")
        sid = scenario.get("id")
        if not isinstance(sid, str) or not sid:
            raise ScenarioError(f"{where}: missing required string field 'id'")
        if sid in seen_ids:
            raise ScenarioError(f"{where}: duplicate scenario id '{sid}'")
        seen_ids.add(sid)
        if not isinstance(scenario.get("input"), str):
            raise ScenarioError(f"{where} ('{sid}'): missing required string field 'input'")
        if "tags" in scenario and (
            not isinstance(scenario["tags"], list)
            or not all(isinstance(t, str) for t in scenario["tags"])
        ):
            raise ScenarioError(f"{where} ('{sid}'): 'tags' must be a list of strings")
        if "skip" in scenario and not isinstance(scenario["skip"], bool):
            raise ScenarioError(f"{where} ('{sid}'): 'skip' must be a boolean")
        if "skip_reason" in scenario and not isinstance(scenario["skip_reason"], str):
            raise ScenarioError(f"{where} ('{sid}'): 'skip_reason' must be a string")
        if "runs" in scenario and (
            not isinstance(scenario["runs"], int)
            or isinstance(scenario["runs"], bool)
            or scenario["runs"] < 1
        ):
            raise ScenarioError(f"{where} ('{sid}'): 'runs' must be an integer >= 1")
        if "min_pass_rate" in scenario:
            mpr = scenario["min_pass_rate"]
            if isinstance(mpr, bool) or not isinstance(mpr, (int, float)) or not 0 <= mpr <= 1:
                raise ScenarioError(
                    f"{where} ('{sid}'): 'min_pass_rate' must be a number between 0 and 1"
                )
        assertions = scenario.get("assert")
        if not isinstance(assertions, list) or not assertions:
            raise ScenarioError(
                f"{where} ('{sid}'): 'assert' must be a non-empty list of assertions"
            )
        for j, assertion in enumerate(assertions):
            a_where = f"{where} ('{sid}'), assertion #{j + 1}"
            if not isinstance(assertion, dict):
                raise ScenarioError(f"{a_where}: must be a mapping")
            a_type = assertion.get("type")
            if a_type not in KNOWN_TYPES:
                raise ScenarioError(
                    f"{a_where}: unknown assertion type {a_type!r}; "
                    f"known types: {', '.join(sorted(KNOWN_TYPES))}"
                )
            if a_type in REQUIRES_PATH and not isinstance(assertion.get("path"), str):
                raise ScenarioError(f"{a_where}: '{a_type}' requires a string 'path' (e.g. $.field)")
            if a_type in REQUIRES_VALUE and "value" not in assertion:
                raise ScenarioError(f"{a_where}: '{a_type}' requires a 'value' field")
            if a_type in REQUIRES_INT_VALUE and (
                not isinstance(assertion.get("value"), int)
                or isinstance(assertion.get("value"), bool)
            ):
                raise ScenarioError(f"{a_where}: '{a_type}' requires an integer 'value'")
            if a_type == "max_duration_ms":
                value = assertion.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ScenarioError(
                        f"{a_where}: 'max_duration_ms' requires a non-negative number 'value'"
                    )
            if a_type == "tool_sequence" and not isinstance(assertion.get("value"), list):
                raise ScenarioError(
                    f"{a_where}: 'tool_sequence' requires a list 'value' of tool names in order"
                )
    return data


def select_scenarios(
    scenarios: list[dict],
    ids: list[str] | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Apply --scenario/--include-tag/--exclude-tag filters and skip: flags.

    Returns (to_run, skipped). Raises ConfigError if nothing runnable remains.
    """
    if ids:
        known = {s["id"] for s in scenarios}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise ConfigError(
                f"--scenario id(s) not found in scenario file: {', '.join(unknown)}"
            )
        wanted = set(ids)
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if include_tags:
        inc = set(include_tags)
        scenarios = [s for s in scenarios if inc & set(s.get("tags", []))]
    if exclude_tags:
        exc = set(exclude_tags)
        scenarios = [s for s in scenarios if not exc & set(s.get("tags", []))]
    to_run = [s for s in scenarios if not s.get("skip", False)]
    skipped = [s for s in scenarios if s.get("skip", False)]
    if not to_run:
        raise ConfigError(
            "no runnable scenarios after filtering "
            "(check --scenario/--include-tag/--exclude-tag and 'skip:' flags)"
        )
    return to_run, skipped


def effective_runs(scenario: dict, cli_runs: int | None) -> int:
    """Runs precedence: explicit --runs (CLI or config) overrides scenario
    'runs'; otherwise scenario 'runs'; otherwise 1."""
    if cli_runs is not None:
        return cli_runs
    return scenario.get("runs", 1)


# ---------------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------------

def load_agent(spec: str):
    if ":" not in spec:
        raise AgentLoadError(
            f"agent spec must be 'module:function' (e.g. myagent:agent), got {spec!r}"
        )
    module_name, func_name = spec.split(":", 1)
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise AgentLoadError(f"could not import module '{module_name}': {exc}") from exc
    fn = getattr(module, func_name, None)
    if fn is None:
        raise AgentLoadError(f"module '{module_name}' has no attribute '{func_name}'")
    if not callable(fn):
        raise AgentLoadError(f"'{spec}' is not callable")
    return fn


# ---------------------------------------------------------------------------
# HTTP target adapter (stdlib urllib; no requests/httpx dependency)
# ---------------------------------------------------------------------------

def normalize_http_response(status: int, body: str) -> dict:
    """Map an HTTP response onto the result shape assertions understand.

    JSON dicts pass through (output/tool_calls/steps score as usual); anything
    else becomes {"output": <text>}. http_status is always attached.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"output": body, "http_status": status}
    if isinstance(parsed, dict):
        result = dict(parsed)
        result.setdefault("output", body)
        result["http_status"] = status
        return result
    return {"output": body, "http_status": status}


def parse_http_headers(raw_headers: list[str]) -> dict[str, str]:
    """Parse repeated --http-header values of the exact form "Name: Value"."""
    headers: dict[str, str] = {}
    for raw in raw_headers:
        name, sep, value = raw.partition(":")
        if not sep or not name.strip() or not value.strip():
            raise ConfigError(f'invalid HTTP header {raw!r}; expected "Name: Value"')
        headers[name.strip()] = value.strip()
    return headers


def make_http_agent(
    url: str,
    input_key: str = "input",
    method: str = "POST",
    timeout: float = HTTP_TIMEOUT_S,
    headers: dict[str, str] | None = None,
):
    """Return an agent callable that sends scenario input to url.

    POST/PUT/PATCH send a {input_key: user_input} JSON body; GET sends the
    input as a ?input_key=... query parameter.
    """
    extra_headers = dict(headers or {})

    def call_http_agent(user_input: str) -> dict:
        if method == "GET":
            sep = "&" if "?" in url else "?"
            full_url = url + sep + urllib.parse.urlencode({input_key: user_input})
            req = urllib.request.Request(full_url, headers=extra_headers, method="GET")
        else:
            payload = json.dumps({input_key: user_input}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", **extra_headers},
                method=method,
            )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # Non-2xx is a scenario failure with a useful message, not a crash.
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {url}: {err_body[:500]}") from exc
        return normalize_http_response(status, body)

    return call_http_agent


# ---------------------------------------------------------------------------
# Result field extraction (agents may return a dict or a bare string)
# ---------------------------------------------------------------------------

def output_text(result) -> str:
    out = result.get("output", "") if isinstance(result, dict) else result
    if isinstance(out, str):
        return out
    return json.dumps(out, default=str)


def tool_names(result) -> list[str]:
    tools = result.get("tool_calls", []) if isinstance(result, dict) else []
    if not isinstance(tools, list):
        tools = [tools]
    return [str(t.get("name", t)) if isinstance(t, dict) else str(t) for t in tools]


def resolve_json_path(result, path: str) -> tuple[bool, object]:
    """Tiny json-path helper: supports $.field (and $.a.b for nested dicts)."""
    if not path.startswith("$."):
        raise ValueError(f"json path must start with '$.', got {path!r}")
    node = result
    for part in path[2:].split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False, None
    return True, node


# ---------------------------------------------------------------------------
# Assertions (pure functions; structured results)
# ---------------------------------------------------------------------------

def assertion_key(assertion: dict) -> str:
    a_type = assertion["type"]
    if a_type in REQUIRES_PATH:
        return f"{a_type}:{assertion['path']}"
    return f"{a_type}:{assertion.get('value', '')}"


def evaluate_assertion(
    assertion: dict, result, error: str | None = None, duration_ms: float | None = None
) -> dict:
    """Score one assertion against one agent run. Never raises."""
    a_type = assertion["type"]
    res = {
        "type": a_type,
        "key": assertion_key(assertion),
        "passed": False,
        "expected": assertion.get("path") if a_type in REQUIRES_PATH else assertion.get("value"),
        "observed": None,
        "reason": "",
    }
    if error is not None:
        res["reason"] = f"agent call failed: {error}"
        return res
    try:
        _evaluate(a_type, assertion, result, res, duration_ms)
    except Exception as exc:  # a broken assertion must fail the run, not crash the CLI
        res["passed"] = False
        res["reason"] = f"assertion could not be evaluated: {exc}"
    return res


def _evaluate(a_type: str, assertion: dict, result, res: dict,
              duration_ms: float | None = None) -> None:
    value = assertion.get("value")

    if a_type in ("contains", "not_contains"):
        text = output_text(result)
        needle = str(value)
        found = needle.lower() in text.lower()
        res["observed"] = text
        if a_type == "contains":
            res["passed"] = found
            res["reason"] = (
                f"output contains {needle!r}" if found else f"output does not contain {needle!r}"
            )
        else:
            res["passed"] = not found
            res["reason"] = (
                f"output does not contain {needle!r}" if not found else f"output contains {needle!r}"
            )

    elif a_type in ("used_tool", "not_used_tool"):
        tools = tool_names(result)
        used = str(value) in tools
        res["observed"] = tools
        if a_type == "used_tool":
            res["passed"] = used
            res["reason"] = (
                f"tool {value!r} was called" if used else f"tool {value!r} was not called (tools: {tools})"
            )
        else:
            res["passed"] = not used
            res["reason"] = (
                f"tool {value!r} was not called" if not used else f"tool {value!r} was called"
            )

    elif a_type in ("max_steps", "min_steps"):
        steps = result.get("steps") if isinstance(result, dict) else None
        res["observed"] = steps
        if not isinstance(steps, (int, float)) or isinstance(steps, bool):
            res["reason"] = f"result has no numeric 'steps' field (got {steps!r})"
            return
        if a_type == "max_steps":
            res["passed"] = steps <= value
            res["reason"] = f"steps {steps} {'<=' if res['passed'] else '>'} max {value}"
        else:
            res["passed"] = steps >= value
            res["reason"] = f"steps {steps} {'>=' if res['passed'] else '<'} min {value}"

    elif a_type == "equals":
        text = output_text(result)
        res["observed"] = text
        res["passed"] = text.strip().casefold() == str(value).strip().casefold()
        res["reason"] = (
            "normalized output equals expected value"
            if res["passed"]
            else f"normalized output {text.strip()!r} != expected {str(value).strip()!r}"
        )

    elif a_type == "regex":
        text = output_text(result)
        res["observed"] = text
        res["passed"] = re.search(str(value), text) is not None
        res["reason"] = (
            f"output matches /{value}/" if res["passed"] else f"output does not match /{value}/"
        )

    elif a_type in ("json_path_equals", "json_path_exists"):
        path = assertion["path"]
        found, node = resolve_json_path(result, path)
        res["observed"] = node if found else "<path not found>"
        if a_type == "json_path_exists":
            res["passed"] = found
            res["reason"] = f"path {path} {'exists' if found else 'does not exist'}"
        else:
            res["passed"] = found and node == value
            if not found:
                res["reason"] = f"path {path} does not exist"
            elif res["passed"]:
                res["reason"] = f"path {path} equals {value!r}"
            else:
                res["reason"] = f"path {path} is {node!r}, expected {value!r}"

    elif a_type == "status_code":
        status = result.get("http_status") if isinstance(result, dict) else None
        res["observed"] = status
        if status is None:
            res["reason"] = (
                "result has no http_status field "
                "(status_code assertions require --target HTTP mode)"
            )
        else:
            res["passed"] = status == value
            res["reason"] = f"http status {status} {'==' if res['passed'] else '!='} {value}"

    elif a_type == "max_duration_ms":
        res["observed"] = duration_ms
        if duration_ms is None:
            res["reason"] = "no run duration available for this result"
        else:
            res["passed"] = duration_ms <= value
            res["reason"] = (
                f"duration {duration_ms:.1f}ms "
                f"{'<=' if res['passed'] else '>'} max {value}ms"
            )

    elif a_type in ("tool_call_count", "max_tool_calls"):
        count = len(tool_names(result))
        res["observed"] = count
        if a_type == "tool_call_count":
            res["passed"] = count == value
            res["reason"] = f"{count} tool call(s), expected exactly {value}"
        else:
            res["passed"] = count <= value
            res["reason"] = f"{count} tool call(s) {'<=' if res['passed'] else '>'} max {value}"

    elif a_type == "tool_sequence":
        tools = tool_names(result)
        expected_seq = [str(v) for v in value]
        res["expected"] = expected_seq
        res["observed"] = tools
        res["passed"] = tools == expected_seq
        res["reason"] = (
            f"tool calls match expected sequence {expected_seq}"
            if res["passed"]
            else f"tool calls {tools} != expected sequence {expected_seq}"
        )

    elif a_type == "json_path_contains":
        path = assertion["path"]
        res["expected"] = value
        found, node = resolve_json_path(result, path)
        res["observed"] = node if found else "<path not found>"
        if not found:
            res["reason"] = f"path {path} does not exist"
        elif isinstance(node, str):
            res["passed"] = str(value).lower() in node.lower()
            res["reason"] = (
                f"path {path} contains {value!r}"
                if res["passed"]
                else f"path {path} value {node!r} does not contain {value!r}"
            )
        elif isinstance(node, list):
            res["passed"] = value in node or str(value) in [str(x) for x in node]
            res["reason"] = (
                f"path {path} list contains {value!r}"
                if res["passed"]
                else f"path {path} list {node!r} does not contain {value!r}"
            )
        else:
            res["reason"] = (
                f"path {path} is {type(node).__name__}, expected a string or list"
            )

    elif a_type == "json_path_regex":
        path = assertion["path"]
        res["expected"] = value
        found, node = resolve_json_path(result, path)
        res["observed"] = node if found else "<path not found>"
        if not found:
            res["reason"] = f"path {path} does not exist"
        else:
            res["passed"] = re.search(str(value), str(node)) is not None
            res["reason"] = (
                f"path {path} matches /{value}/"
                if res["passed"]
                else f"path {path} value {node!r} does not match /{value}/"
            )

    elif a_type == "semantic":
        # Optional add-on stub: never required, never auto-installed.
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            res["reason"] = "semantic assertion requires optional dependency: sentence-transformers"
            return
        res["reason"] = "semantic assertion is a stub in this MVP (not implemented yet)"

    elif a_type == "judge":
        # Optional add-on stub: no Ollama/Anthropic/OpenAI requirement in the MVP.
        res["reason"] = "judge assertion requires configuring an optional judge backend"

    else:  # unreachable: schema validation rejects unknown types
        res["reason"] = f"unknown assertion type {a_type!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict, agent_fn, runs: int) -> dict:
    run_records = []
    for _ in range(runs):
        start = time.perf_counter()
        result, error = None, None
        try:
            result = agent_fn(scenario["input"])
        except Exception as exc:  # agent call failure is a scenario failure, not a crash
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = (time.perf_counter() - start) * 1000
        assertion_results = [
            evaluate_assertion(a, result, error, duration_ms) for a in scenario["assert"]
        ]
        run_records.append(
            {
                "output": output_text(result) if error is None else None,
                "tool_calls": tool_names(result) if error is None else [],
                "steps": result.get("steps") if isinstance(result, dict) else None,
                "duration_ms": round(duration_ms, 3),
                "error": error,
                "result": result,
                "assertions": assertion_results,
                "passed": all(r["passed"] for r in assertion_results),
            }
        )

    passed_runs = sum(1 for r in run_records if r["passed"])
    # An assertion counts as passing only if it passed in every run.
    assertions_summary: dict[str, bool] = {}
    for assertion in scenario["assert"]:
        key = assertion_key(assertion)
        assertions_summary[key] = all(
            ar["passed"]
            for record in run_records
            for ar in record["assertions"]
            if ar["key"] == key
        )
    pass_rate = passed_runs / runs
    min_pass_rate = float(scenario.get("min_pass_rate", 1.0))
    return {
        "id": scenario["id"],
        "input": scenario["input"],
        "tags": scenario.get("tags", []),
        "runs": runs,
        "passed_runs": passed_runs,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "passed": pass_rate >= min_pass_rate,
        "assertions": assertions_summary,
        "run_records": run_records,
    }


# ---------------------------------------------------------------------------
# Baseline (plain JSON file; diffing is a pure function)
# ---------------------------------------------------------------------------

def build_baseline(scenario_results: list[dict]) -> dict:
    return {
        "version": BASELINE_VERSION,
        "created_at": _utcnow(),
        "scenarios": {
            sr["id"]: {
                "pass_rate": sr["pass_rate"],
                "min_pass_rate": sr["min_pass_rate"],
                "passed": sr["passed"],
                "runs": sr["runs"],
                "assertions": sr["assertions"],
            }
            for sr in scenario_results
        },
    }


def write_baseline(
    path: Path,
    scenario_results: list[dict],
    existing: dict | None = None,
    file_ids: set[str] | None = None,
) -> None:
    """Write the baseline. When updating an existing baseline (--update-baseline),
    merge: keep entries for scenarios still in the file but not run this time
    (filtered/skipped), drop entries removed from the file, overwrite what ran.
    """
    baseline = build_baseline(scenario_results)
    if existing is not None:
        keep = file_ids if file_ids is not None else set(existing["scenarios"])
        merged = {
            sid: entry for sid, entry in existing["scenarios"].items() if sid in keep
        }
        merged.update(baseline["scenarios"])
        baseline["scenarios"] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2) + "\n")


def load_baseline(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"baseline file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"baseline file {path} is unreadable or corrupt ({exc}); "
            f"delete it or pass --update-baseline after fixing"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), dict):
        raise ConfigError(f"baseline file {path} has unexpected shape (missing 'scenarios' map)")
    return data


def load_artifact(path: Path) -> dict:
    """Read and validate a --json-out run artifact for baseline promote/diff.

    Artifacts without artifact_version (pre-v0.3) are tolerated when the rest
    of the shape is recognizable; unsupported future versions are rejected.
    """
    if not path.exists():
        raise ConfigError(f"run artifact not found: {path}")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"run artifact {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"run artifact {path} must be a JSON object")
    version = data.get("artifact_version")
    if version is not None and version not in SUPPORTED_ARTIFACT_VERSIONS:
        raise ConfigError(
            f"unsupported artifact_version {version!r} in {path}; this agenteval "
            f"supports: {', '.join(str(v) for v in sorted(SUPPORTED_ARTIFACT_VERSIONS))}"
        )
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and "pass_rate" in s
        for s in scenarios
    ):
        raise ConfigError(
            f"{path} does not look like an agenteval run artifact "
            f"(expected a 'scenarios' list of results with 'id' and 'pass_rate'; "
            f"create one with: agenteval run ... --json-out {path})"
        )
    for sr in scenarios:  # normalize pre-v0.2 artifacts that lack these fields
        sr.setdefault("min_pass_rate", 1.0)
        sr.setdefault("passed", sr["pass_rate"] >= sr["min_pass_rate"])
        sr.setdefault("runs", 1)
        sr.setdefault("assertions", {})
    return data


def diff_against_baseline(
    baseline: dict,
    scenario_results: list[dict],
    file_ids: set[str] | None = None,
) -> tuple[bool, list[dict]]:
    """Compare current results to baseline. Returns (regressed, events).

    file_ids is every scenario id present in the scenario file (including
    filtered-out and skipped ones): a baseline scenario is MISSING only if it
    left the file, never because a filter deselected it this run.
    """
    events: list[dict] = []
    regressed = False
    base_scenarios = baseline["scenarios"]

    for sr in scenario_results:
        sid = sr["id"]
        if sid not in base_scenarios:
            events.append({"kind": "new", "scenario_id": sid})
            continue
        base = base_scenarios[sid]
        base_rate = base.get("pass_rate", 0.0)
        if sr["min_pass_rate"] < 1.0:
            # Thresholded scenario: pass-rate dips above the threshold are
            # tolerated flakiness, not regressions. Regression = the scenario
            # met its threshold in the baseline and misses it now.
            base_passed = base.get("passed", base_rate >= sr["min_pass_rate"])
            if base_passed and not sr["passed"]:
                regressed = True
                events.append(
                    {
                        "kind": "regressed",
                        "scenario_id": sid,
                        "baseline_pass_rate": base_rate,
                        "current_pass_rate": sr["pass_rate"],
                    }
                )
            continue
        if sr["pass_rate"] < base_rate:
            regressed = True
            events.append(
                {
                    "kind": "regressed",
                    "scenario_id": sid,
                    "baseline_pass_rate": base_rate,
                    "current_pass_rate": sr["pass_rate"],
                }
            )
        for key, was_passing in base.get("assertions", {}).items():
            if was_passing and key in sr["assertions"] and not sr["assertions"][key]:
                regressed = True
                events.append({"kind": "assertion_regressed", "scenario_id": sid, "assertion": key})

    considered = file_ids if file_ids is not None else {sr["id"] for sr in scenario_results}
    for sid in base_scenarios:
        if sid not in considered:
            events.append({"kind": "missing", "scenario_id": sid})
    return regressed, events


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_event(ev: dict) -> str:
    if ev["kind"] == "regressed":
        return (
            f"REGRESSED {ev['scenario_id']} "
            f"{_pct(ev['baseline_pass_rate'])} -> {_pct(ev['current_pass_rate'])}"
        )
    if ev["kind"] == "assertion_regressed":
        return (
            f"REGRESSED {ev['scenario_id']} assertion {ev['assertion']}: "
            f"was passing in baseline, now failing"
        )
    if ev["kind"] == "new":
        return f"NEW {ev['scenario_id']} (not in baseline; add with --update-baseline)"
    if ev["kind"] == "missing":
        return f"MISSING {ev['scenario_id']} (in baseline but not in current scenario file)"
    return f"{ev['kind'].upper()} {ev.get('scenario_id', '')}"


def failed_assertion_details(sr: dict) -> list[dict]:
    """Failed assertion results from the first failing run of a scenario."""
    first_failed = next((r for r in sr["run_records"] if not r["passed"]), None)
    if first_failed is None:
        return []
    return [ar for ar in first_failed["assertions"] if not ar["passed"]]


def print_report(scenario_results: list[dict], skipped: list[dict], events: list[dict],
                 status: str, baseline_path: Path, overall_pass_rate: float,
                 exit_code: int) -> None:
    for sr in scenario_results:
        word = "PASS" if sr["passed"] else "FAIL"
        threshold = f" (min_pass_rate {sr['min_pass_rate']})" if sr["min_pass_rate"] < 1.0 else ""
        print(f"{word} {sr['id']} {sr['passed_runs']}/{sr['runs']} runs passed{threshold}")
        if not sr["passed"]:
            for ar in failed_assertion_details(sr):
                print(f"    FAILED {ar['key']}: {ar['reason']}")

    for sk in skipped:
        reason = f" ({sk['skip_reason']})" if sk.get("skip_reason") else ""
        print(f"SKIP {sk['id']}{reason}")

    for ev in events:
        print(format_event(ev))

    _print_overall(status, baseline_path, scenario_results, overall_pass_rate, exit_code)


def _print_overall(status, baseline_path, scenario_results, overall_pass_rate, exit_code):
    if status == "baseline_created":
        print(f"Baseline created: {baseline_path} ({len(scenario_results)} scenario(s))")
        print(f"Overall: BASELINE CREATED ({_pct(overall_pass_rate)} pass rate recorded)")
    elif status == "baseline_updated":
        print(f"Baseline updated: {baseline_path}")
        print(f"Overall: BASELINE UPDATED ({_pct(overall_pass_rate)} pass rate recorded)")
    elif status == "regressed":
        print("Overall: REGRESSED")
    elif status == "fail_missing":
        print("Overall: FAIL (scenarios missing vs baseline, --fail-on-missing set)")
    else:
        print(f"Overall: PASS {_pct(overall_pass_rate)}")
    if exit_code != 0:
        print(f"Exit: {exit_code}")


def write_junit(path: Path, scenario_results: list[dict], skipped: list[dict],
                agent_label: str, duration_s: float) -> None:
    """JUnit XML via stdlib ElementTree: one testsuite, one testcase per
    scenario. Assertion failures -> <failure>, agent exceptions -> <error>,
    skip: scenarios -> <skipped>."""
    failures = errors = 0
    suite = ET.Element("testsuite")
    for sr in scenario_results:
        case_time = sum(r["duration_ms"] for r in sr["run_records"]) / 1000
        case = ET.SubElement(
            suite, "testcase", classname="agenteval", name=sr["id"], time=f"{case_time:.3f}"
        )
        if sr["passed"]:
            continue
        run_errors = [r["error"] for r in sr["run_records"] if r["error"]]
        tag = "error" if run_errors else "failure"
        if tag == "error":
            errors += 1
        else:
            failures += 1
        lines = [
            f"scenario {sr['id']}: {sr['passed_runs']}/{sr['runs']} runs passed "
            f"(min_pass_rate {sr['min_pass_rate']})"
        ]
        for err in dict.fromkeys(run_errors):
            lines.append(f"agent error: {err}")
        for ar in failed_assertion_details(sr):
            lines.append(
                f"{ar['key']}: expected={ar['expected']!r} "
                f"observed={ar['observed']!r} reason={ar['reason']}"
            )
        el = ET.SubElement(
            case, tag, message=f"{sr['passed_runs']}/{sr['runs']} runs passed"
        )
        el.text = "\n".join(lines)
    for sk in skipped:
        case = ET.SubElement(
            suite, "testcase", classname="agenteval", name=sk["id"], time="0.000"
        )
        ET.SubElement(case, "skipped", message=sk.get("skip_reason") or "skipped")

    suite.set("name", f"agenteval: {agent_label}")
    suite.set("tests", str(len(scenario_results) + len(skipped)))
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    suite.set("skipped", str(len(skipped)))
    suite.set("time", f"{duration_s:.3f}")

    tree = ET.ElementTree(suite)
    ET.indent(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def render_markdown(artifact: dict) -> str:
    scenario_results = artifact["scenarios"]
    skipped = artifact["skipped"]
    events = artifact["comparison"]["events"]
    lines = [
        "# AgentEval Report",
        "",
        f"- Status: **{artifact['status'].upper()}**",
        f"- Agent: `{artifact['agent'] or artifact['target']}`",
        f"- Scenarios file: `{artifact['scenarios_file']}`",
        f"- Baseline: `{artifact['baseline']['path']}`",
        f"- Overall pass rate: {_pct(artifact['overall_pass_rate'])}",
        f"- Scenarios: {artifact['scenario_count']} run, {len(skipped)} skipped",
        f"- Total runs: {artifact['total_runs']}",
        f"- Started: {artifact['started_at']}",
        f"- Completed: {artifact['completed_at']} ({artifact['duration_ms']} ms)",
        f"- Exit code: {artifact['exit_code']}",
        "",
        "## Summary",
        "",
        "| Scenario | Result | Runs Passed | Pass Rate |",
        "|---|---:|---:|---:|",
    ]
    for sr in scenario_results:
        word = "PASS" if sr["passed"] else "FAIL"
        lines.append(
            f"| {sr['id']} | {word} | {sr['passed_runs']}/{sr['runs']} | {_pct(sr['pass_rate'])} |"
        )
    for sk in skipped:
        lines.append(f"| {sk['id']} | SKIP | - | - |")

    if events:
        lines += ["", "## Comparison Events", ""]
        lines += [f"- {format_event(ev)}" for ev in events]

    failed = [sr for sr in scenario_results if not sr["passed"]]
    if failed:
        lines += ["", "## Failed Assertions", ""]
        for sr in failed:
            lines.append(f"### {sr['id']}")
            lines.append("")
            run_errors = list(dict.fromkeys(r["error"] for r in sr["run_records"] if r["error"]))
            for err in run_errors:
                lines.append(f"- agent error: `{err}`")
            for ar in failed_assertion_details(sr):
                lines.append(
                    f"- {ar['type']}: expected `{ar['expected']!r}`, "
                    f"observed `{ar['observed']!r}` — {ar['reason']}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(artifact))


# ---------------------------------------------------------------------------
# Config file (--config or auto-discovered .agenteval.yaml)
# ---------------------------------------------------------------------------

CONFIG_SCHEMA: dict[str, type] = {
    "baseline": str,
    "require_baseline": bool,
    "fail_on_missing": bool,
    "runs": int,
    "json_out": str,
    "junit_out": str,
    "markdown_out": str,
    "include_tags": list,
    "exclude_tags": list,
    "http_input_key": str,
    "http_timeout": float,  # int or float accepted (special-cased below)
    "http_headers": list,
    "http_bearer_token_env": str,
    "http_method": str,
}


def load_config(path: Path | None) -> dict:
    """Load and validate a config file. Explicit missing path is an error;
    absent default file just means no config."""
    if path is None:
        path = Path(DEFAULT_CONFIG_FILE)
        if not path.exists():
            return {}
    elif not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse config {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: config must be a mapping, got {type(data).__name__}")
    for key, value in data.items():
        expected = CONFIG_SCHEMA.get(key)
        if expected is None:
            raise ConfigError(
                f"{path}: unknown config key {key!r}; "
                f"known keys: {', '.join(sorted(CONFIG_SCHEMA))}"
            )
        if key == "http_timeout":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{path}: config key 'http_timeout' must be a number")
            continue
        if not isinstance(value, expected) or (isinstance(value, bool) and expected is not bool):
            raise ConfigError(f"{path}: config key {key!r} must be a {expected.__name__}")
        if expected is list and not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{path}: config key {key!r} must be a list of strings")
    if "runs" in data and data["runs"] < 1:
        raise ConfigError(f"{path}: config key 'runs' must be >= 1")
    return data


def resolve_settings(args: argparse.Namespace) -> dict:
    """Merge CLI flags over config-file values into one settings dict.
    CLI always wins; config supplies defaults; hard defaults last."""
    config = load_config(Path(args.config) if args.config else None)

    def pick(cli_value, key, default=None):
        return cli_value if cli_value is not None else config.get(key, default)

    settings = {
        "scenarios": args.scenarios,
        "agent": args.agent,
        "target": args.target,
        "http_input_key": pick(args.http_input_key, "http_input_key", "input"),
        "baseline": pick(args.baseline, "baseline", DEFAULT_BASELINE),
        "json_out": pick(args.json_out, "json_out"),
        "junit_out": pick(args.junit_out, "junit_out"),
        "markdown_out": pick(args.markdown_out, "markdown_out"),
        "runs": pick(args.runs, "runs"),
        "require_baseline": args.require_baseline or config.get("require_baseline", False),
        "fail_on_missing": args.fail_on_missing or config.get("fail_on_missing", False),
        "update_baseline": args.update_baseline,
        "allow_partial_baseline": args.allow_partial_baseline,
        "scenario_ids": args.scenario,
        "include_tags": pick(args.include_tag, "include_tags"),
        "exclude_tags": pick(args.exclude_tag, "exclude_tags"),
        "http_timeout": pick(args.http_timeout, "http_timeout", HTTP_TIMEOUT_S),
        "http_method": str(pick(args.http_method, "http_method", "POST")).upper(),
        "http_headers": pick(args.http_header, "http_headers", []),
        "http_bearer_token_env": pick(args.http_bearer_token_env, "http_bearer_token_env"),
    }
    if settings["agent"] and settings["target"]:
        raise ConfigError("--agent and --target are mutually exclusive; pass exactly one")
    if not settings["agent"] and not settings["target"]:
        raise ConfigError("one of --agent (module:function) or --target (URL) is required")
    if settings["runs"] is not None and settings["runs"] < 1:
        raise ConfigError("--runs must be >= 1")
    if settings["target"]:
        _resolve_http_settings(settings)
    return settings


def _resolve_http_settings(settings: dict) -> None:
    """Validate --target HTTP options and build the final header dict in place."""
    if not settings["target"].startswith(("http://", "https://")):
        raise ConfigError(f"--target must be an http(s) URL, got {settings['target']!r}")
    if settings["http_method"] not in HTTP_METHODS:
        raise ConfigError(
            f"unsupported --http-method {settings['http_method']!r}; "
            f"allowed: {', '.join(sorted(HTTP_METHODS))}"
        )
    timeout = settings["http_timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError(f"--http-timeout must be a positive number of seconds, got {timeout!r}")
    headers = parse_http_headers(settings["http_headers"])
    token_env = settings["http_bearer_token_env"]
    if token_env:
        if any(name.lower() == "authorization" for name in headers):
            raise ConfigError(
                "--http-bearer-token-env conflicts with an explicit Authorization header; "
                "pass only one"
            )
        token = os.environ.get(token_env, "")
        if not token:
            raise ConfigError(
                f"--http-bearer-token-env: environment variable {token_env!r} is not set or empty"
            )
        headers["Authorization"] = f"Bearer {token}"
    settings["http_headers"] = headers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    started_at = _utcnow()
    t0 = time.perf_counter()

    settings = resolve_settings(args)  # ConfigError -> exit 2
    scenarios = load_scenarios(Path(settings["scenarios"]))  # ScenarioError -> exit 2
    file_ids = {s["id"] for s in scenarios}
    to_run, skipped_scenarios = select_scenarios(
        scenarios, settings["scenario_ids"], settings["include_tags"], settings["exclude_tags"]
    )
    skipped = [
        {"id": s["id"], "skip_reason": s.get("skip_reason", "")} for s in skipped_scenarios
    ]

    baseline_path = Path(settings["baseline"])
    baseline_exists = baseline_path.exists()
    if settings["require_baseline"] and not baseline_exists:
        # CI safety: a fresh runner must never mint a green baseline by accident.
        raise ConfigError(f"Baseline required but not found: {baseline_path}")
    filters_active = bool(
        settings["scenario_ids"] or settings["include_tags"] or settings["exclude_tags"]
    )
    if not baseline_exists and filters_active and not settings["allow_partial_baseline"]:
        # A baseline minted from a filtered run silently under-covers the suite.
        raise ConfigError(
            "Refusing to create a new baseline from a filtered run. "
            "Re-run without filters or pass --allow-partial-baseline."
        )

    if settings["target"]:
        agent_fn = make_http_agent(
            settings["target"],
            settings["http_input_key"],
            method=settings["http_method"],
            timeout=settings["http_timeout"],
            headers=settings["http_headers"],
        )
        agent_label = settings["target"]
    else:
        agent_fn = load_agent(settings["agent"])  # AgentLoadError -> exit 3
        agent_label = settings["agent"]

    scenario_results = [
        run_scenario(sc, agent_fn, effective_runs(sc, settings["runs"])) for sc in to_run
    ]
    overall_pass_rate = sum(sr["pass_rate"] for sr in scenario_results) / len(scenario_results)

    baseline = load_baseline(baseline_path) if baseline_exists else None
    events: list[dict] = []
    baseline_created = baseline_updated = False
    if baseline is None:
        # First run: creating the baseline is the expected bootstrap path.
        write_baseline(baseline_path, scenario_results)
        baseline_created = True
        status, exit_code = "baseline_created", EXIT_OK
    else:
        regressed, events = diff_against_baseline(baseline, scenario_results, file_ids)
        missing = [e for e in events if e["kind"] == "missing"]
        if settings["update_baseline"]:
            # The only way to overwrite an existing baseline.
            write_baseline(baseline_path, scenario_results, existing=baseline, file_ids=file_ids)
            baseline_updated = True
            status, exit_code = "baseline_updated", EXIT_OK
        elif regressed:
            status, exit_code = "regressed", EXIT_REGRESSION
        elif settings["fail_on_missing"] and missing:
            status, exit_code = "fail_missing", EXIT_REGRESSION
        else:
            status, exit_code = "pass", EXIT_OK

    print_report(
        scenario_results, skipped, events, status, baseline_path, overall_pass_rate, exit_code
    )

    duration_ms = round((time.perf_counter() - t0) * 1000, 3)
    run_counts = {sr["runs"] for sr in scenario_results}
    artifact = {
        "version": 1,
        "artifact_version": ARTIFACT_VERSION,
        "started_at": started_at,
        "completed_at": _utcnow(),
        "duration_ms": duration_ms,
        "agent": settings["agent"],
        "target": settings["target"],
        "scenarios_file": settings["scenarios"],
        "scenario_count": len(scenario_results),
        "runs_per_scenario": run_counts.pop() if len(run_counts) == 1 else None,
        "total_runs": sum(sr["runs"] for sr in scenario_results),
        "overall_pass_rate": overall_pass_rate,
        "scenarios": scenario_results,
        "skipped": skipped,
        "baseline": {
            "path": str(baseline_path),
            "existed": baseline is not None,
            "created": baseline_created,
            "updated": baseline_updated,
        },
        "comparison": {"events": events},
        "status": status,
        "exit_code": exit_code,
    }
    if settings["json_out"]:
        json_out = Path(settings["json_out"])
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(artifact, indent=2, default=str) + "\n")
    if settings["junit_out"]:
        write_junit(
            Path(settings["junit_out"]), scenario_results, skipped, agent_label,
            duration_ms / 1000,
        )
    if settings["markdown_out"]:
        write_markdown(Path(settings["markdown_out"]), artifact)

    return exit_code


# ---------------------------------------------------------------------------
# init / validate / baseline subcommands
# ---------------------------------------------------------------------------

INIT_CONFIG_TEMPLATE = """\
# agenteval config — CLI flags override these values.
# For CI, add: require_baseline: true (and commit your baseline file).
baseline: .agenteval/baseline.json
json_out: .agenteval/latest.json
junit_out: .agenteval/junit.xml
markdown_out: .agenteval/report.md
fail_on_missing: false
"""

INIT_SCENARIOS_TEMPLATE = """\
# agenteval scenarios — deterministic assertions only; no model dependencies.
- id: smoke_basic
  tags: [smoke]
  input: "I want a refund for order 123"
  assert:
    - type: contains
      value: refund
    - type: max_steps
      value: 5
"""


def cmd_init(args: argparse.Namespace) -> int:
    if args.agent and args.target:
        raise ConfigError("--agent and --target are mutually exclusive")

    def ensure_file(path: Path, content: str) -> None:
        if path.exists() and not args.force:
            print(f"skipped {path} (exists; use --force to overwrite)")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"created {path}")

    state_dir = Path(".agenteval")
    if state_dir.exists():
        print(f"skipped {state_dir}/ (exists)")
    else:
        state_dir.mkdir(parents=True)
        print(f"created {state_dir}/")

    config_text = INIT_CONFIG_TEMPLATE
    if args.http_input_key:
        config_text += f"http_input_key: {args.http_input_key}\n"
    ensure_file(Path(args.config_file), config_text)
    ensure_file(Path(args.scenario_file), INIT_SCENARIOS_TEMPLATE)

    run_cmd = f"uv run agenteval.py run {args.scenario_file}"
    if args.agent:
        run_cmd += f" --agent {args.agent}"
    elif args.target:
        run_cmd += f" --target {args.target}"
    else:
        run_cmd += " --agent yourmodule:agent"
    print(f"Next: {run_cmd}")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate scenarios + config + filters without importing or calling an agent."""
    config = load_config(Path(args.config) if args.config else None)
    scenarios = load_scenarios(Path(args.scenarios))
    include_tags = args.include_tag if args.include_tag is not None else config.get("include_tags")
    exclude_tags = args.exclude_tag if args.exclude_tag is not None else config.get("exclude_tags")
    to_run, skipped = select_scenarios(scenarios, args.scenario, include_tags, exclude_tags)

    assertion_count = sum(len(s["assert"]) for s in scenarios)
    print(
        f"OK {args.scenarios}: {len(scenarios)} scenario(s), "
        f"{assertion_count} assertion(s) valid"
    )
    print(f"Runnable after filters: {len(to_run)}; skipped: {len(skipped)}")
    if config:
        print(f"Config OK ({len(config)} key(s))")
    return EXIT_OK


def cmd_baseline_show(args: argparse.Namespace) -> int:
    baseline_path = Path(args.baseline)
    baseline = load_baseline(baseline_path)  # ConfigError -> exit 2
    scenarios = baseline["scenarios"]
    print(
        f"Baseline {baseline_path} (version {baseline.get('version', '?')}, "
        f"created {baseline.get('created_at', 'unknown')}): {len(scenarios)} scenario(s)"
    )
    for sid, entry in scenarios.items():
        print(f"  {sid}: pass_rate {_pct(entry.get('pass_rate', 0.0))} ({entry.get('runs', '?')} run(s))")
        for key, passing in entry.get("assertions", {}).items():
            print(f"    {'PASS' if passing else 'FAIL'} {key}")
    return EXIT_OK


PROMOTABLE_STATUSES = {"pass", "regressed", "baseline_created", "baseline_updated", "fail_missing"}


def cmd_baseline_promote(args: argparse.Namespace) -> int:
    artifact = load_artifact(Path(args.from_path))
    status = artifact.get("status")
    if status not in PROMOTABLE_STATUSES:
        raise ConfigError(
            f"refusing to promote artifact with status {status!r}; "
            f"promotable statuses: {', '.join(sorted(PROMOTABLE_STATUSES))}"
        )
    if status == "regressed" and not args.force:
        raise ConfigError(
            "refusing to promote a REGRESSED artifact to baseline; pass --force to accept "
            "the regressed behavior as the new reference"
        )
    baseline_path = Path(args.baseline)
    if baseline_path.exists() and not args.force:
        raise ConfigError(
            f"baseline already exists: {baseline_path}; pass --force to overwrite"
        )
    write_baseline(baseline_path, artifact["scenarios"])
    print(f"Promoted {len(artifact['scenarios'])} scenario(s) from {args.from_path} to {baseline_path}")
    return EXIT_OK


def cmd_baseline_diff(args: argparse.Namespace) -> int:
    """Offline diff of a --json-out artifact against a baseline; no agent runs."""
    artifact = load_artifact(Path(args.from_path))
    baseline = load_baseline(Path(args.baseline))
    regressed, events = diff_against_baseline(baseline, artifact["scenarios"])
    for ev in events:
        print(format_event(ev))
    if regressed:
        print("Overall: REGRESSED")
        return EXIT_REGRESSION
    print("Overall: PASS (no regression)")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenteval",
        description="Run agent scenarios, score with deterministic assertions, diff vs baseline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run scenarios against an agent and diff against baseline")
    run_p.add_argument("scenarios", help="path to scenarios YAML file")
    run_p.add_argument("--agent", default=None, help="agent as module:function, e.g. myagent:agent")
    run_p.add_argument("--target", default=None, help="URL of a running HTTP agent to POST to")
    run_p.add_argument(
        "--http-input-key", default=None,
        help="JSON key for the scenario input in --target POST bodies (default: input)",
    )
    run_p.add_argument(
        "--http-timeout", type=float, default=None,
        help=f"HTTP timeout in seconds for --target (default: {HTTP_TIMEOUT_S})",
    )
    run_p.add_argument(
        "--http-header", action="append", default=None, metavar='"Name: Value"',
        help="extra HTTP header for --target requests (repeatable)",
    )
    run_p.add_argument(
        "--http-bearer-token-env", default=None, metavar="ENV_VAR",
        help="read a bearer token from this env var and send Authorization: Bearer <token>",
    )
    run_p.add_argument(
        "--http-method", default=None,
        help="HTTP method for --target: GET, POST, PUT, or PATCH (default: POST)",
    )
    run_p.add_argument("--baseline", default=None,
                       help=f"baseline JSON path (default: {DEFAULT_BASELINE})")
    run_p.add_argument("--json-out", default=None, help="write full run artifact JSON to this path")
    run_p.add_argument("--junit-out", default=None, help="write JUnit XML report to this path")
    run_p.add_argument("--markdown-out", default=None, help="write Markdown report to this path")
    run_p.add_argument(
        "--runs", type=int, default=None,
        help="runs per scenario; overrides scenario-level 'runs' (default 1)",
    )
    run_p.add_argument(
        "--require-baseline", action="store_true",
        help="exit 2 if the baseline file does not exist (recommended for CI)",
    )
    run_p.add_argument(
        "--update-baseline", action="store_true",
        help="overwrite the existing baseline with the current results",
    )
    run_p.add_argument(
        "--allow-partial-baseline", action="store_true",
        help="allow creating a new baseline from a filtered run",
    )
    run_p.add_argument(
        "--fail-on-missing", action="store_true",
        help="exit 1 if a baseline scenario is missing from the scenario file",
    )
    run_p.add_argument(
        "--scenario", action="append", default=None, metavar="ID",
        help="run only this scenario id (repeatable)",
    )
    run_p.add_argument(
        "--include-tag", action="append", default=None, metavar="TAG",
        help="run only scenarios with at least one included tag (repeatable)",
    )
    run_p.add_argument(
        "--exclude-tag", action="append", default=None, metavar="TAG",
        help="exclude scenarios with any excluded tag (repeatable)",
    )
    run_p.add_argument(
        "--config", default=None,
        help=f"config YAML path (default: {DEFAULT_CONFIG_FILE} if present); CLI flags win",
    )
    run_p.set_defaults(func=cmd_run)

    init_p = sub.add_parser("init", help="scaffold .agenteval/, config, and a starter scenario file")
    init_p.add_argument("--force", action="store_true", help="overwrite existing files")
    init_p.add_argument("--scenario-file", default="scenarios.yaml", help="scenario file to create")
    init_p.add_argument("--config-file", default=DEFAULT_CONFIG_FILE, help="config file to create")
    init_p.add_argument("--agent", default=None, help="module:function to suggest in next steps")
    init_p.add_argument("--target", default=None, help="HTTP URL to suggest in next steps")
    init_p.add_argument(
        "--http-input-key", default=None, help="write http_input_key into the generated config"
    )
    init_p.set_defaults(func=cmd_init)

    val_p = sub.add_parser(
        "validate", help="validate scenarios, config, and filters without running an agent"
    )
    val_p.add_argument("scenarios", help="path to scenarios YAML file")
    val_p.add_argument("--config", default=None, help="config YAML path")
    val_p.add_argument("--scenario", action="append", default=None, metavar="ID")
    val_p.add_argument("--include-tag", action="append", default=None, metavar="TAG")
    val_p.add_argument("--exclude-tag", action="append", default=None, metavar="TAG")
    val_p.set_defaults(func=cmd_validate)

    base_p = sub.add_parser("baseline", help="inspect, promote, or diff baseline files")
    base_sub = base_p.add_subparsers(dest="baseline_command", required=True)

    show_p = base_sub.add_parser("show", help="print baseline scenarios and assertion states")
    show_p.add_argument("--baseline", default=DEFAULT_BASELINE)
    show_p.set_defaults(func=cmd_baseline_show)

    promote_p = base_sub.add_parser(
        "promote", help="promote a --json-out run artifact into the baseline (no agent rerun)"
    )
    promote_p.add_argument("--from", dest="from_path", required=True, metavar="ARTIFACT",
                           help="run artifact JSON created with --json-out")
    promote_p.add_argument("--baseline", default=DEFAULT_BASELINE)
    promote_p.add_argument(
        "--force", action="store_true",
        help="overwrite an existing baseline / accept a regressed artifact",
    )
    promote_p.set_defaults(func=cmd_baseline_promote)

    diff_p = base_sub.add_parser(
        "diff", help="diff a --json-out run artifact against a baseline (no agent rerun)"
    )
    diff_p.add_argument("--from", dest="from_path", required=True, metavar="ARTIFACT",
                        help="run artifact JSON created with --json-out")
    diff_p.add_argument("--baseline", default=DEFAULT_BASELINE)
    diff_p.set_defaults(func=cmd_baseline_diff)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ScenarioError as exc:
        print(f"ERROR: invalid scenario file: {exc}", file=sys.stderr)
        return EXIT_INVALID_SCENARIOS
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID_SCENARIOS
    except AgentLoadError as exc:
        print(f"ERROR: could not load agent: {exc}", file=sys.stderr)
        return EXIT_AGENT_ERROR
    except Exception:
        print("ERROR: internal agenteval error:", file=sys.stderr)
        traceback.print_exc()
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
