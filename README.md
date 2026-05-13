# multi-agent-system-failures — `agentleak` branch

This branch contains the **AgentLeak** datasets and the converter
toolkit that maps them into the research-team export schema
([`research_team_trace_20260417_003354.json`](research_team_trace_20260417_003354.json)
is the reference).

Two datasets are covered:

1. **Executed traces** under [`traces/`](traces/) — 4 979 trace files
   with real LLM calls + channel sends + leak labels.
2. **Scenario specifications** in
   [`scenarios_full_1000.jsonl`](scenarios_full_1000.jsonl) — 1 000
   scenario specs that include explicit **tools** (the previous traces
   dataset had none).

## Layout

| Path | What it is |
| --- | --- |
| `traces/` | 4 979 raw AgentLeak traces (one JSON per scenario). |
| `scenarios_full_1000.jsonl` | 1 000 scenario specifications with agents, tools (1-3 per scenario, clearance-gated), attack config, and 3 canary-tier records each. |
| `research_team_trace_20260417_003354.json` | Reference of the research-team export format. |
| `convert/convert_traces.py` | Converter for `traces/` → research-team-style export. |
| `convert/convert_scenarios.py` | Converter for `scenarios_full_1000.jsonl` → 1 000 research-team-style JSONs (with populated `tools_registry`). |
| `research_traces_sample/` | Committed sample of the trace conversion (1 trace per `vertical × attack_family` cell, ~28 files). |
| `research_scenarios/` | All 1 000 converted scenarios (~12 MB). |
| `ANALYSIS.md` | Trace-dataset analysis (MAS? tools? per-model / vertical / attack breakdowns). |
| `SCENARIOS_ANALYSIS.md` | Scenario-dataset analysis (tools? clearance gaps, attack mix, vault schema). |
| `.gitignore` | Excludes the bulk `research_traces/` output (~344 MB) from being committed. |

## Quick start

### Convert executed traces

```bash
# default: traces/ -> research_traces/   (all 4 979 files, ~10s on 4 cores)
python -m convert.convert_traces

# convert only a handful (debug / smoke test)
python -m convert.convert_traces --limit 10 --workers 1
```

### Convert scenario specs (1 000 JSONL lines → 1 000 JSON files)

```bash
# default: scenarios_full_1000.jsonl -> research_scenarios/   (~1s)
python -m convert.convert_scenarios

# convert only the first N (debug)
python -m convert.convert_scenarios --limit 5 --dst /tmp/scenarios_sample
```

Both converters emit the same top-level layout: `agents_registry`,
`tools_registry`, `communication_graph`, `sessions[*].spans` with
OpenInference-style attributes. IDs are deterministic SHA-256 prefixes
so re-runs are idempotent.

## Two-line summary

- **Traces dataset (4 979 files):** Multi-Agent System (single-agent
  baseline vs coordinator/worker/compiler chain with a shared-memory
  cache). **No tools** — zero `tool_calls` across all traces. See
  [`ANALYSIS.md`](ANALYSIS.md).
- **Scenarios dataset (1 000 lines):** Specifications with 1–3 agents,
  **1–3 explicit tools per scenario** (with capabilities and clearance
  requirements), and 3 canary tiers per private vault. ~50 % attack /
  ~50 % baseline. See [`SCENARIOS_ANALYSIS.md`](SCENARIOS_ANALYSIS.md).
