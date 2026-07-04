# Scenario format

Scenarios are a YAML list. Each item describes one input to send to your agent
and the deterministic assertions its result must satisfy. All assertions run
with zero model dependencies.

## Scenario fields

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Unique scenario id (string). Used in reports and the baseline. |
| `input` | yes | The string passed to your agent. |
| `assert` | yes | Non-empty list of assertions (see below). |
| `tags` | no | List of strings for `--include-tag` / `--exclude-tag` filtering. |
| `skip` | no | `true` reports the scenario as SKIP and never runs it. |
| `skip_reason` | no | String shown next to a skipped scenario. |
| `runs` | no | Integer ≥ 1: run this scenario K times (default 1). |
| `min_pass_rate` | no | Number in `[0, 1]`: scenario passes if `pass_rate >= min_pass_rate` (default 1.0). |

**Runs precedence:** an explicit `--runs` on the CLI (or `runs:` in config)
overrides a scenario's `runs` for every scenario; otherwise the scenario's
`runs`; otherwise 1.

**Agent result shape.** Your agent returns either a bare string (treated as
`output`) or a dict. Recognized dict keys: `output` (string), `tool_calls`
(list of names or `{name: ...}` objects), `steps` (number). In `--target` HTTP
mode, `http_status` is attached automatically and JSON response bodies are used
as the result dict.

## Examples

### Simple deterministic output check

```yaml
- id: greeting
  input: "hello"
  assert:
    - type: contains
      value: help
```

### Tool usage check

```yaml
- id: refund_uses_lookup
  input: "I want a refund for order 123"
  assert:
    - type: used_tool
      value: lookup_order
    - type: max_steps
      value: 5
```

### HTTP status check (`--target` mode)

```yaml
- id: http_ok
  input: "ping"
  assert:
    - type: status_code
      value: 200
```

### JSON path checks

```yaml
- id: ticket_shape
  input: "open a ticket"
  assert:
    - type: json_path_exists
      path: "$.ticket_id"
    - type: json_path_regex
      path: "$.ticket_id"
      value: "^TICKET-[0-9]+$"
    - type: json_path_contains
      path: "$.message"
      value: refund
```

The path helper is intentionally tiny: `$.field` and nested `$.a.b` against the
raw result dict. It is not a full JSONPath implementation.

### Tags and filtering

```yaml
- id: fast_smoke
  tags: [smoke]
  input: "hi"
  assert:
    - type: contains
      value: hi
- id: expensive_case
  tags: [nightly, expensive]
  input: "big task"
  assert:
    - type: contains
      value: done
```

```sh
agenteval run scenarios.yaml --agent m:f --include-tag smoke
agenteval run scenarios.yaml --agent m:f --exclude-tag expensive
agenteval run scenarios.yaml --agent m:f --scenario fast_smoke
```

### Skip

```yaml
- id: not_ready
  skip: true
  skip_reason: "waiting for fixture"
  input: "..."
  assert:
    - type: contains
      value: anything
```

### min_pass_rate with repeated runs

```yaml
- id: flaky_but_acceptable
  runs: 5
  min_pass_rate: 0.8
  input: "give me an ok"
  assert:
    - type: contains
      value: ok
```

For a thresholded scenario, regression means "met the threshold in the baseline,
misses it now" — pass-rate dips that stay at or above the threshold are tolerated
flakiness, not regressions.

## Assertion reference

Every assertion produces a structured result: `type`, `passed`, `expected`,
`observed`, `reason`.

| Type | Fields | Passes when |
|------|--------|-------------|
| `contains` | `value` | `value` appears in output (case-insensitive) |
| `not_contains` | `value` | `value` does not appear in output |
| `used_tool` | `value` | `value` is in `tool_calls` |
| `not_used_tool` | `value` | `value` is not in `tool_calls` |
| `max_steps` | `value` (int) | `steps <= value` |
| `min_steps` | `value` (int) | `steps >= value` |
| `equals` | `value` | normalized output (trim + casefold) equals `value` |
| `regex` | `value` | `re.search(value, output)` matches |
| `json_path_equals` | `path` | value at `path` equals `value` |
| `json_path_exists` | `path` | `path` resolves in the result dict |
| `json_path_contains` | `path`, `value` | string field contains `value`, or list field includes it |
| `json_path_regex` | `path`, `value` | `re.search(value, str(field))` matches |
| `status_code` | `value` (int) | `http_status == value` (fails clearly outside HTTP mode) |
| `max_duration_ms` | `value` (num ≥ 0) | run duration ≤ `value` ms |
| `tool_call_count` | `value` (int) | exactly `value` tool calls |
| `max_tool_calls` | `value` (int) | at most `value` tool calls |
| `tool_sequence` | `value` (list) | `tool_calls` exactly equals the ordered list |

### Optional assertions (not required, not used by default)

| Type | Behavior |
|------|----------|
| `semantic` | Fails with a friendly message unless `sentence-transformers` is installed; stubbed in this release. |
| `judge` | Fails with a friendly message; requires configuring an optional judge backend. |

These exist so scenario files can reference them, but the core product is
deterministic. Neither is installed automatically or used in the default path.
