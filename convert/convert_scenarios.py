"""Convert `scenarios_full_1000.jsonl` into research-team-style JSON files.

Each line of the JSONL is a scenario *specification* (not an execution
trace), so the converted output has:

  - a populated `agents_registry` (1-3 agents per scenario, with the
    real `role`/`clearance` from the spec),
  - a populated `tools_registry` (every scenario has 1-3 tools, with
    capabilities + clearance requirements),
  - a `communication_graph` whose edges are inferred from the roles
    declared in the spec (single-agent vs assistant + coordinator vs
    assistant + coordinator + specialist/analyst/reviewer),
  - a single "ScenarioDefinition" session/span carrying the user
    request, allowed/forbidden field set, attack config, and
    private-vault *schema* (we record record-types and canary tiers,
    not the canary values themselves).

Tools are bound to an agent when the agent's clearance dominates the
tool's `requires_clearance` (ordering: public < restricted <
confidential < secret; `null` means no clearance required).

Usage:
    python -m convert.convert_scenarios \
        --src scenarios_full_1000.jsonl \
        --dst research_scenarios
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def _hex(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def _trace_id(scenario_id: str) -> str:
    return _hex(scenario_id, 32)


def _session_id(scenario_id: str) -> str:
    return _hex(scenario_id, 16)


def _span_id(scenario_id: str, span_path: str) -> str:
    return _hex(f"{scenario_id}|{span_path}", 16)


def _parse_ts(ts: str | None) -> _dt.datetime:
    if not ts:
        return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(ts).replace(tzinfo=None)


def _fmt_ts(dt: _dt.datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Clearance ordering
# ---------------------------------------------------------------------------


_CLEARANCE_ORDER = {
    None: 0,
    "public": 0,
    "restricted": 1,
    "confidential": 2,
    "secret": 3,
}


def _clearance_level(name: str | None) -> int:
    return _CLEARANCE_ORDER.get(name, 0)


# ---------------------------------------------------------------------------
# Role -> description / factory templates
# ---------------------------------------------------------------------------


_ROLE_DESCRIPTIONS: dict[str, str] = {
    "assistant": "Primary user-facing assistant.",
    "coordinator": "Coordinator \u2014 plans the task and delegates work to other agents.",
    "specialist": "Specialist \u2014 handles a domain-specific sub-task delegated by the coordinator.",
    "analyst": "Analyst \u2014 inspects data and produces structured findings for the coordinator.",
    "reviewer": "Reviewer \u2014 checks the output of upstream agents for policy compliance before release.",
}


def _agent_description(role: str | None) -> str:
    return _ROLE_DESCRIPTIONS.get(role or "assistant", f"Agent with role {role!r}.")


def _factory_for(role: str | None) -> str:
    return f"create_{(role or 'assistant')}_agent"


def _agent_name(role: str | None, agent_id: str, role_counts: dict[str, int]) -> str:
    """Stable agent_name: ``<role>_agent`` for unique roles, otherwise suffix with agent_id."""
    base = f"{role or 'assistant'}_agent"
    if role_counts.get(role, 0) > 1:
        return f"{base}_{agent_id}"
    return base


def _node_name(role: str | None, agent_id: str) -> str:
    return f"{role or 'assistant'}_{agent_id}".lower()


# ---------------------------------------------------------------------------
# Registry builders
# ---------------------------------------------------------------------------


def _build_tool_parameters(tool: dict[str, Any]) -> dict[str, Any]:
    """Synthesise an OpenAI-style parameters schema from a tool's capabilities."""
    caps = tool.get("capabilities") or []
    return {
        "type": "object",
        "properties": {
            "operation": {
                "title": "Operation",
                "type": "string",
                "enum": list(caps) if caps else [],
                "description": "Capability to invoke on this tool.",
            },
            "query": {
                "title": "Query",
                "type": "string",
                "description": "Free-form arguments for the chosen operation.",
            },
        },
        "required": ["operation"],
    }


def _build_tool_description(tool: dict[str, Any]) -> str:
    name = tool.get("tool_name")
    caps = tool.get("capabilities") or []
    clear = tool.get("requires_clearance")
    lines = [f"Tool: {name}."]
    if caps:
        lines.append(f"Capabilities: {', '.join(caps)}.")
    if clear:
        lines.append(f"Requires clearance: {clear}.")
    else:
        lines.append("No clearance required.")
    return " ".join(lines)


def _build_agents_registry(
    agents: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Build agents_registry and return (registry, id_to_name, id_to_node)."""
    role_counts: dict[str, int] = {}
    for a in agents:
        role_counts[a.get("role")] = role_counts.get(a.get("role"), 0) + 1

    id_to_name: dict[str, str] = {}
    id_to_node: dict[str, str] = {}
    out_agents: list[dict[str, Any]] = []
    for a in agents:
        agent_id = a.get("agent_id") or ""
        role = a.get("role")
        clearance = a.get("clearance")
        name = _agent_name(role, agent_id, role_counts)
        node = _node_name(role, agent_id)
        id_to_name[agent_id] = name
        id_to_node[agent_id] = node

        bound = [
            t.get("tool_name")
            for t in tools
            if _clearance_level(t.get("requires_clearance")) <= _clearance_level(clearance)
        ]
        out_agents.append(
            {
                "agent_name": name,
                "description": _agent_description(role),
                "system_prompt": a.get("system_prompt"),
                "tools_bound": bound,
                "node_name": node,
                "factory_function": _factory_for(role),
                "role": role,
                "clearance": clearance,
                "agent_id": agent_id,
                "can_communicate_with": [],  # populated after we build the graph
                "participated": False,
            }
        )

    return (
        {
            "framework_name": "agentleak_scenarios",
            "total_agents": len(out_agents),
            "agents": out_agents,
        },
        id_to_name,
        id_to_node,
    )


def _build_tools_registry(
    tools: list[dict[str, Any]], agents_meta: list[dict[str, Any]]
) -> dict[str, Any]:
    out_tools: list[dict[str, Any]] = []
    for t in tools:
        clearance = t.get("requires_clearance")
        bound_agents = [
            a["agent_name"]
            for a in agents_meta
            if _clearance_level(clearance) <= _clearance_level(a.get("clearance"))
        ]
        out_tools.append(
            {
                "tool_name": t.get("tool_name"),
                "description": _build_tool_description(t),
                "capabilities": list(t.get("capabilities") or []),
                "requires_clearance": clearance,
                "parameters": _build_tool_parameters(t),
                "bound_to_agents": bound_agents,
                "invoked": False,
                "invocation_count": 0,
            }
        )
    return {
        "framework_name": "agentleak_scenarios",
        "total_tools": len(out_tools),
        "tools": out_tools,
    }


# ---------------------------------------------------------------------------
# Communication graph
# ---------------------------------------------------------------------------


def _build_communication_graph(
    agents: list[dict[str, Any]],
    id_to_name: dict[str, str],
    attack: dict[str, Any],
) -> dict[str, Any]:
    """Infer the communication topology from the declared agent roles.

    The dataset uses three canonical topologies:

      - **1 agent (assistant)**          user <-> assistant
      - **2 agents (assistant + X)**     user -> X -> assistant -> X -> user
                                         (X = coordinator/reviewer/analyst/...)
      - **3 agents (assistant + X + Y)** user -> coordinator -> assistant
                                                                  -> {analyst,specialist,reviewer}
                                                                  -> coordinator -> user

    For scenarios without a coordinator we treat the *first* non-assistant
    agent as the lead. ``attack.target_channels`` is consulted to add a
    `memory` / `tool_input` / `tool_output` / `log` / `artifact` side node.
    """
    edges: list[dict[str, Any]] = []

    by_role: dict[str, list[str]] = {}
    for a in agents:
        by_role.setdefault(a.get("role"), []).append(a["agent_id"])

    def name(agent_id: str) -> str:
        return id_to_name[agent_id]

    if len(agents) == 1:
        only = agents[0]
        a_name = name(only["agent_id"])
        edges.append(
            {
                "source": "user",
                "target": a_name,
                "condition": "Direct request",
                "edge_type": "sequential",
                "traversed": False,
            }
        )
        edges.append(
            {
                "source": a_name,
                "target": "user",
                "condition": "Direct reply",
                "edge_type": "sequential",
                "traversed": False,
            }
        )
    else:
        # Identify the lead. Prefer coordinator if present, else assistant.
        if by_role.get("coordinator"):
            lead_id = by_role["coordinator"][0]
        elif by_role.get("assistant"):
            lead_id = by_role["assistant"][0]
        else:
            lead_id = agents[0]["agent_id"]
        lead = name(lead_id)

        edges.append(
            {
                "source": "user",
                "target": lead,
                "condition": "Multi-agent request",
                "edge_type": "sequential",
                "traversed": False,
            }
        )

        for a in agents:
            if a["agent_id"] == lead_id:
                continue
            other_name = name(a["agent_id"])
            edges.append(
                {
                    "source": lead,
                    "target": other_name,
                    "condition": f"Delegate to {a.get('role')}",
                    "edge_type": "sequential",
                    "traversed": False,
                }
            )
            edges.append(
                {
                    "source": other_name,
                    "target": lead,
                    "condition": f"{a.get('role')} returns findings",
                    "edge_type": "sequential",
                    "traversed": False,
                }
            )

        edges.append(
            {
                "source": lead,
                "target": "user",
                "condition": "Final reply",
                "edge_type": "sequential",
                "traversed": False,
            }
        )

    # Attack-driven side channels.
    if attack and attack.get("enabled"):
        target_channels = attack.get("target_channels") or []
        if "memory_write" in target_channels:
            edges.append(
                {
                    "source": (
                        name(by_role.get("assistant", [None])[0])
                        if by_role.get("assistant")
                        else name(agents[0]["agent_id"])
                    ),
                    "target": "memory",
                    "condition": "Adversarial memory write (target_channel=memory_write)",
                    "edge_type": "side_channel",
                    "traversed": False,
                }
            )
        for side in ("log", "artifact"):
            if side in target_channels:
                lead_node = name(agents[0]["agent_id"])
                edges.append(
                    {
                        "source": lead_node,
                        "target": side,
                        "condition": f"Adversarial side channel target_channel={side}",
                        "edge_type": "side_channel",
                        "traversed": False,
                    }
                )

    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e["source"], []).append(e["target"])
    for n in ("user", "memory", "log", "artifact"):
        if any(e["target"] == n for e in edges):
            adjacency.setdefault(n, [])

    terminal_targets = sorted({"user"} | {n for n in ("memory", "log", "artifact") if n in adjacency})

    return {
        "entry_point": "user",
        "terminal_agents": terminal_targets,
        "total_edges": len(edges),
        "edges": edges,
        "adjacency_list": adjacency,
    }


def _fill_can_communicate_with(
    agents_registry: dict[str, Any], graph: dict[str, Any]
) -> None:
    out: dict[str, list[str]] = {}
    for e in graph["edges"]:
        out.setdefault(e["source"], [])
        if e["target"] not in out[e["source"]]:
            out[e["source"]].append(e["target"])
    for a in agents_registry["agents"]:
        a["can_communicate_with"] = list(out.get(a["agent_name"], []))


# ---------------------------------------------------------------------------
# Span construction (a single ScenarioDefinition span; not executed)
# ---------------------------------------------------------------------------


_SERVICE_NAME = "mas-example-agentleak-scenarios"


def _build_scenario_span(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario.get("scenario_id") or "unknown"
    created = _parse_ts(scenario.get("created_at"))
    obj = scenario.get("objective") or {}
    return {
        "name": "ScenarioDefinition",
        "trace_id": _trace_id(scenario_id),
        "span_id": _span_id(scenario_id, "scenario_definition"),
        "parent_span_id": None,
        "start_time": _fmt_ts(created),
        "end_time": _fmt_ts(created),
        "duration_ms": 0.0,
        "status": "UNSET",
        "status_message": "Scenario specification \u2014 not yet executed.",
        "kind": "INTERNAL",
        "service_name": _SERVICE_NAME,
        "openinference": {
            "input_value": obj.get("user_request") or "",
            "input_mime_type": "text/plain",
            "output_value": "",
            "output_mime_type": "text/plain",
        },
        "attributes": {
            "scenario.id": scenario_id,
            "scenario.version": scenario.get("version"),
            "scenario.vertical": scenario.get("vertical"),
            "scenario.difficulty": scenario.get("difficulty"),
            "scenario.tags": list(scenario.get("tags") or []),
            "scenario.privacy_instruction": obj.get("privacy_instruction"),
            "scenario.success_criteria": list(obj.get("success_criteria") or []),
        },
        "events": [],
        "eval_metadata": None,
    }


def _vault_schema(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Record-types + canary tiers + field *names* only (no canary values)."""
    records = (scenario.get("private_vault") or {}).get("records") or []
    schema: list[dict[str, Any]] = []
    for r in records:
        schema.append(
            {
                "record_type": r.get("record_type"),
                "canary_tier": r.get("canary_tier"),
                "sensitivity_weight": r.get("sensitivity_weight"),
                "field_names": sorted((r.get("fields") or {}).keys()),
            }
        )
    return schema


# ---------------------------------------------------------------------------
# Per-scenario conversion
# ---------------------------------------------------------------------------


def convert_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario.get("scenario_id") or "unknown"
    agents = list(scenario.get("agents") or [])
    tools = list(scenario.get("tools") or [])
    attack = scenario.get("attack") or {}

    agents_registry, id_to_name, _id_to_node = _build_agents_registry(agents, tools)
    tools_registry = _build_tools_registry(tools, agents_registry["agents"])
    graph = _build_communication_graph(agents, id_to_name, attack)
    _fill_can_communicate_with(agents_registry, graph)

    span = _build_scenario_span(scenario)

    obj = scenario.get("objective") or {}
    session = {
        "session_id": _session_id(scenario_id),
        "trace_id": _trace_id(scenario_id),
        "start_time": _fmt_ts(_parse_ts(scenario.get("created_at"))),
        "end_time": _fmt_ts(_parse_ts(scenario.get("created_at"))),
        "duration_ms": 0.0,
        "input_query": obj.get("user_request"),
        "final_output": None,
        "route_taken": None,
        "spans": [span],
        "metadata": {
            "scenario_id": scenario_id,
            "version": scenario.get("version"),
            "created_at": scenario.get("created_at"),
            "vertical": scenario.get("vertical"),
            "difficulty": scenario.get("difficulty"),
            "tags": list(scenario.get("tags") or []),
            "objective": {
                "user_request": obj.get("user_request"),
                "success_criteria": list(obj.get("success_criteria") or []),
                "privacy_instruction": obj.get("privacy_instruction"),
            },
            "allowed_set": scenario.get("allowed_set") or {},
            "attack": attack,
            "evaluation": scenario.get("evaluation") or {},
            "private_vault_schema": _vault_schema(scenario),
        },
    }

    return {
        "export_time": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(),
        "agents_registry": agents_registry,
        "tools_registry": tools_registry,
        "communication_graph": graph,
        "session_count": 1,
        "span_count": len(session["spans"]),
        "sessions": [session],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--src", default="scenarios_full_1000.jsonl", help="Source JSONL file of scenarios"
    )
    parser.add_argument(
        "--dst",
        default="research_scenarios",
        help="Destination directory for one JSON file per scenario",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Optional cap on number of scenarios (debug)"
    )
    args = parser.parse_args(argv)

    if not os.path.isfile(args.src):
        print(f"error: source file {args.src!r} not found", file=sys.stderr)
        return 2

    os.makedirs(args.dst, exist_ok=True)

    ok = 0
    fail = 0
    failures: list[tuple[str, str]] = []
    with open(args.src, "r") as fh:
        for i, line in enumerate(fh, 1):
            if args.limit and i > args.limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                scenario = json.loads(line)
                out = convert_scenario(scenario)
                name = f"{scenario.get('scenario_id') or f'scenario_{i:05d}'}.json"
                with open(os.path.join(args.dst, name), "w") as wh:
                    json.dump(out, wh, indent=2, default=str)
                ok += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                failures.append((str(i), f"{type(e).__name__}: {e}"))
            if i % 250 == 0:
                print(f"  {i} scenarios processed (ok={ok}, fail={fail})", flush=True)

    print(f"Done. ok={ok}, fail={fail}")
    if failures:
        print("First few failures:")
        for src, err in failures[:5]:
            print(f"  line {src}: {err}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
