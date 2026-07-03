#!/usr/bin/env python3
"""agenteval — CLI-first agent regression testing.

The loop this tool proves:

    run agent -> score behavior -> diff against baseline -> exit non-zero on regression

Deterministic-first: every assertion in the default path works with zero model
dependencies. Optional semantic/judge assertions exist only as import-guarded
stubs and are never required.

Usage:

    uv run agenteval.py run scenarios.yaml --agent myagent:agent

Exit codes:
    0  pass (or baseline created / explicitly updated)
    1  regression vs baseline (or missing scenarios with --fail-on-missing)
    2  invalid scenario file
    3  agent import failure
    4  internal unexpected error
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INVALID_SCENARIOS = 2
EXIT_AGENT_ERROR = 3
EXIT_INTERNAL = 4

BASELINE_VERSION = 1
DEFAULT_BASELINE = ".agenteval/baseline.json"

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
}
OPTIONAL_TYPES = {"semantic", "judge"}
KNOWN_TYPES = DETERMINISTIC_TYPES | OPTIONAL_TYPES

# Assertion types that must carry a "value" / "path" field to be meaningful.
REQUIRES_VALUE = KNOWN_TYPES - {"json_path_exists", "judge"}
REQUIRES_PATH = {"json_path_equals", "json_path_exists"}
REQUIRES_INT_VALUE = {"max_steps", "min_steps"}


class ScenarioError(Exception):
    """The scenario YAML is missing, unparseable, or fails schema validation."""


class AgentLoadError(Exception):
    """The --agent module:function spec could not be imported/resolved."""


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
            if a_type in REQUIRES_INT_VALUE and not isinstance(assertion.get("value"), int):
                raise ScenarioError(f"{a_where}: '{a_type}' requires an integer 'value'")
    return data


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


def evaluate_assertion(assertion: dict, result, error: str | None = None) -> dict:
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
        _evaluate(a_type, assertion, result, res)
    except Exception as exc:  # a broken assertion must fail the run, not crash the CLI
        res["passed"] = False
        res["reason"] = f"assertion could not be evaluated: {exc}"
    return res


def _evaluate(a_type: str, assertion: dict, result, res: dict) -> None:
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
            evaluate_assertion(a, result, error) for a in scenario["assert"]
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
    return {
        "id": scenario["id"],
        "input": scenario["input"],
        "runs": runs,
        "passed_runs": passed_runs,
        "pass_rate": passed_runs / runs,
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
                "runs": sr["runs"],
                "assertions": sr["assertions"],
            }
            for sr in scenario_results
        },
    }


def write_baseline(path: Path, scenario_results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_baseline(scenario_results), indent=2) + "\n")


def load_baseline(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"baseline file {path} is unreadable or corrupt ({exc}); "
            f"delete it or pass --update-baseline after fixing"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), dict):
        raise RuntimeError(f"baseline file {path} has unexpected shape (missing 'scenarios' map)")
    return data


def diff_against_baseline(baseline: dict, scenario_results: list[dict]) -> tuple[bool, list[dict]]:
    """Compare current results to baseline. Returns (regressed, events)."""
    events: list[dict] = []
    regressed = False
    base_scenarios = baseline["scenarios"]
    current_ids = {sr["id"] for sr in scenario_results}

    for sr in scenario_results:
        sid = sr["id"]
        if sid not in base_scenarios:
            events.append({"kind": "new", "scenario_id": sid})
            continue
        base = base_scenarios[sid]
        base_rate = base.get("pass_rate", 0.0)
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

    for sid in base_scenarios:
        if sid not in current_ids:
            events.append({"kind": "missing", "scenario_id": sid})
    return regressed, events


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(scenario_results: list[dict], events: list[dict], status: str,
                 baseline_path: Path, overall_pass_rate: float, exit_code: int) -> None:
    for sr in scenario_results:
        word = "PASS" if sr["pass_rate"] == 1.0 else "FAIL"
        print(f"{word} {sr['id']} {sr['passed_runs']}/{sr['runs']} runs passed")
        if sr["pass_rate"] < 1.0:
            first_failed = next(r for r in sr["run_records"] if not r["passed"])
            for ar in first_failed["assertions"]:
                if not ar["passed"]:
                    print(f"    FAILED {ar['key']}: {ar['reason']}")

    for ev in events:
        if ev["kind"] == "regressed":
            print(
                f"REGRESSED {ev['scenario_id']} "
                f"{_pct(ev['baseline_pass_rate'])} -> {_pct(ev['current_pass_rate'])}"
            )
        elif ev["kind"] == "assertion_regressed":
            print(
                f"REGRESSED {ev['scenario_id']} assertion {ev['assertion']}: "
                f"was passing in baseline, now failing"
            )
        elif ev["kind"] == "new":
            print(f"NEW {ev['scenario_id']} (not in baseline; add with --update-baseline)")
        elif ev["kind"] == "missing":
            print(f"MISSING {ev['scenario_id']} (in baseline but not in current scenario file)")

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    started_at = _utcnow()
    t0 = time.perf_counter()

    scenarios = load_scenarios(Path(args.scenarios))  # ScenarioError -> exit 2
    agent_fn = load_agent(args.agent)  # AgentLoadError -> exit 3
    runs = args.runs

    scenario_results = [run_scenario(sc, agent_fn, runs) for sc in scenarios]
    overall_pass_rate = sum(sr["pass_rate"] for sr in scenario_results) / len(scenario_results)

    baseline_path = Path(args.baseline)
    baseline = load_baseline(baseline_path) if baseline_path.exists() else None

    events: list[dict] = []
    baseline_created = baseline_updated = False
    if baseline is None:
        # First run: creating the baseline is the expected bootstrap path.
        write_baseline(baseline_path, scenario_results)
        baseline_created = True
        status, exit_code = "baseline_created", EXIT_OK
    else:
        regressed, events = diff_against_baseline(baseline, scenario_results)
        missing = [e for e in events if e["kind"] == "missing"]
        if args.update_baseline:
            # The only way to overwrite an existing baseline.
            write_baseline(baseline_path, scenario_results)
            baseline_updated = True
            status, exit_code = "baseline_updated", EXIT_OK
        elif regressed:
            status, exit_code = "regressed", EXIT_REGRESSION
        elif args.fail_on_missing and missing:
            status, exit_code = "fail_missing", EXIT_REGRESSION
        else:
            status, exit_code = "pass", EXIT_OK

    print_report(scenario_results, events, status, baseline_path, overall_pass_rate, exit_code)

    if args.json_out:
        artifact = {
            "version": 1,
            "started_at": started_at,
            "completed_at": _utcnow(),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
            "agent": args.agent,
            "scenarios_file": args.scenarios,
            "scenario_count": len(scenario_results),
            "runs_per_scenario": runs,
            "scenarios": scenario_results,
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
        json_out = Path(args.json_out)
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(artifact, indent=2, default=str) + "\n")

    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenteval",
        description="Run agent scenarios, score with deterministic assertions, diff vs baseline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run scenarios against an agent and diff against baseline")
    run_p.add_argument("scenarios", help="path to scenarios YAML file")
    run_p.add_argument("--agent", required=True, help="agent as module:function, e.g. myagent:agent")
    run_p.add_argument("--baseline", default=DEFAULT_BASELINE, help="baseline JSON path")
    run_p.add_argument("--json-out", default=None, help="write full run artifact JSON to this path")
    run_p.add_argument("--runs", type=int, default=1, help="runs per scenario (default 1)")
    run_p.add_argument(
        "--update-baseline", action="store_true",
        help="overwrite the existing baseline with the current results",
    )
    run_p.add_argument(
        "--fail-on-missing", action="store_true",
        help="exit 1 if a baseline scenario is missing from the scenario file",
    )
    run_p.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "runs", 1) < 1:
        parser.error("--runs must be >= 1")
    try:
        return args.func(args)
    except ScenarioError as exc:
        print(f"ERROR: invalid scenario file: {exc}", file=sys.stderr)
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
