# multi-agent-system-failures — `agentleak` branch

This branch contains the **AgentLeak** dataset: 4 979 trace files under
[`traces/`](traces/) plus one reference export of the research-team
benchmark ([`research_team_trace_20260417_003354.json`](research_team_trace_20260417_003354.json))
used as the target schema.

## Layout

| Path | What it is |
| --- | --- |
| `traces/` | 4 979 raw AgentLeak traces (one JSON per scenario). |
| `research_team_trace_20260417_003354.json` | Reference of the research-team export format. |
| `convert/convert_traces.py` | Converter: AgentLeak trace JSON → research-team-style export JSON. |
| `research_traces_sample/` | A small committed sample of the conversion output (1 trace per (vertical × attack family) cell, ~28 files). |
| `ANALYSIS.md` | Full dataset analysis answering "is this a MAS?", "do agents have tools?", plus per-model / per-vertical / per-attack-family breakdowns. |
| `.gitignore` | Excludes the bulk `research_traces/` output (~344 MB) from being committed. |

## Quick start — run the converter

```bash
# default: traces/ -> research_traces/   (all 4 979 files, ~30s on 4 cores)
python -m convert.convert_traces

# convert only a handful (debug / smoke test)
python -m convert.convert_traces --limit 10 --workers 1

# custom paths
python -m convert.convert_traces --src traces --dst research_traces
```

Each output mirrors the layout of the research-team export
(`agents_registry`, `tools_registry`, `communication_graph`,
`sessions[*].spans` with OpenInference-style attributes).

## Two-sentence answer

- **The traces describe a Multi-Agent System.** Every trace runs two
  topologies in parallel: a single-agent baseline (1 LLM call) and a
  3-step **coordinator → worker → compiler** chain with a shared-memory
  cache side channel. See the topology diagram and per-channel leak
  rates in [`ANALYSIS.md`](ANALYSIS.md).
- **The agents have no tools.** All 4 979 traces are pure
  prompt-driven LLM completions — there are zero `tool_calls` /
  `function_call` invocations. `tools_registry.total_tools == 0` in
  every converted file.

For the full breakdown (per-model deltas, per-vertical leak rates,
attack-family effects, joint result profile, top leaked fields, etc.)
see [`ANALYSIS.md`](ANALYSIS.md).
