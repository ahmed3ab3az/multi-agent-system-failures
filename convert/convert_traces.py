"""Convert AgentLeak `traces/*.json` into the research-team trace JSON format.

The source traces capture an AgentLeak experiment that compares a single-agent
baseline against a multi-agent (coordinator + worker + compiler) flow with a
shared memory cache. The target format mirrors `research_team_trace_*.json`
which exposes:

    - agents_registry      (per-agent metadata + system prompts)
    - tools_registry       (tools available to the framework)
    - communication_graph  (entry / terminal / edges / adjacency_list)
    - sessions[*].spans    (OpenInference-style span tree)

Usage:
    python -m convert.convert_traces \
        --src traces \
        --dst research_traces

The script is deterministic: span and session IDs are derived from a hash of
the source `trace_id` + span path, so re-running produces stable output.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def _hex(seed: str, length: int) -> str:
    """Deterministic hex string of ``length`` chars derived from ``seed``."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def _trace_id(source_trace_id: str) -> str:
    return _hex(source_trace_id, 32)


def _session_id(source_trace_id: str) -> str:
    return _hex(source_trace_id, 16)


def _span_id(source_trace_id: str, span_path: str) -> str:
    return _hex(f"{source_trace_id}|{span_path}", 16)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _parse_ts(ts: str) -> _dt.datetime:
    """Parse an ISO timestamp (tolerating ``Z`` and missing tzinfo)."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(ts)


def _fmt_ts(dt: _dt.datetime) -> str:
    # Match the research_team_trace style (no tz suffix, microsecond precision)
    return dt.replace(tzinfo=None).isoformat()


def _add_ms(dt: _dt.datetime, ms: float) -> _dt.datetime:
    return dt + _dt.timedelta(milliseconds=ms)


# ---------------------------------------------------------------------------
# Static registries
# ---------------------------------------------------------------------------


def _build_agents_registry(llm_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the agents_registry block from the 4 LLM calls in a trace.

    The four calls map to fixed roles:
      0. single_agent      (baseline)
      1. coordinator_agent (delegation)
      2. worker_agent      (drafting)
      3. compiler_agent    (final user-facing response)
    """

    def _sys(i: int) -> str:
        if i < len(llm_calls):
            return llm_calls[i].get("system_prompt") or ""
        return ""

    agents = [
        {
            "agent_name": "single_agent",
            "description": (
                "Baseline single-agent assistant with direct access to user data. "
                "Used as the privacy-leak control for the multi-agent flow."
            ),
            "system_prompt": _sys(0),
            "tools_bound": [],
            "node_name": "single_agent",
            "factory_function": "create_single_agent",
            "can_communicate_with": ["user"],
            "participated": True,
        },
        {
            "agent_name": "coordinator_agent",
            "description": (
                "Coordinator \u2014 receives the user request together with the data "
                "context and delegates an instruction packet to the Worker."
            ),
            "system_prompt": _sys(1),
            "tools_bound": [],
            "node_name": "coordinator",
            "factory_function": "create_coordinator_agent",
            "can_communicate_with": ["worker_agent"],
            "participated": True,
        },
        {
            "agent_name": "worker_agent",
            "description": (
                "Worker \u2014 follows the coordinator's instructions, has access to "
                "the full data vault, drafts a response and writes a cache "
                "snapshot to shared memory."
            ),
            "system_prompt": _sys(2),
            "tools_bound": [],
            "node_name": "worker",
            "factory_function": "create_worker_agent",
            "can_communicate_with": ["coordinator_agent", "memory"],
            "participated": True,
        },
        {
            "agent_name": "compiler_agent",
            "description": (
                "Compiler \u2014 takes the worker's draft and emits the final "
                "privacy-conscious response to the user."
            ),
            "system_prompt": _sys(3),
            "tools_bound": [],
            "node_name": "compiler",
            "factory_function": "create_compiler_agent",
            "can_communicate_with": ["user"],
            "participated": True,
        },
    ]

    return {
        "framework_name": "agentleak",
        "total_agents": len(agents),
        "agents": agents,
    }


def _build_tools_registry() -> dict[str, Any]:
    """AgentLeak traces contain no tool invocations \u2014 return an empty registry."""
    return {
        "framework_name": "agentleak",
        "total_tools": 0,
        "tools": [],
    }


def _build_communication_graph(channel_messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Static topology of the AgentLeak benchmark.

    The graph encodes both the single-agent baseline and the multi-agent
    (coordinator -> worker -> compiler) flow plus the memory side-channel.
    Edge ``traversed`` is computed from the channel_messages of this trace.
    """
    sources_targets = {
        (m.get("source"), m.get("target")) for m in channel_messages
    }
    # Map source-data labels onto agent names for lookup.
    _alias = {
        "single_agent": "single_agent",
        "coordinator": "coordinator_agent",
        "worker": "worker_agent",
        "user": "user",
        "memory": "memory",
    }
    observed: set[tuple[str, str]] = set()
    for src, tgt in sources_targets:
        if src in _alias and tgt in _alias:
            observed.add((_alias[src], _alias[tgt]))

    edges = [
        {
            "source": "user",
            "target": "single_agent",
            "condition": "Baseline direct request",
            "edge_type": "sequential",
        },
        {
            "source": "single_agent",
            "target": "user",
            "condition": "Baseline reply (channel C1)",
            "edge_type": "sequential",
        },
        {
            "source": "user",
            "target": "coordinator_agent",
            "condition": "Multi-agent request",
            "edge_type": "sequential",
        },
        {
            "source": "coordinator_agent",
            "target": "worker_agent",
            "condition": "Delegate task (channel C2)",
            "edge_type": "sequential",
        },
        {
            "source": "worker_agent",
            "target": "coordinator_agent",
            "condition": "Return findings (channel C2)",
            "edge_type": "sequential",
        },
        {
            "source": "worker_agent",
            "target": "memory",
            "condition": "Cache write (channel C5)",
            "edge_type": "side_channel",
        },
        {
            "source": "coordinator_agent",
            "target": "compiler_agent",
            "condition": "Hand off draft for final compilation",
            "edge_type": "sequential",
        },
        {
            "source": "compiler_agent",
            "target": "user",
            "condition": "Final privacy-conscious reply (channel C1)",
            "edge_type": "sequential",
        },
    ]
    # Mark traversal. The user/coordinator-handoff edges are always traversed
    # in a complete trace; observed-edge check is informational only.
    always_traversed = {
        ("user", "single_agent"),
        ("user", "coordinator_agent"),
        ("coordinator_agent", "compiler_agent"),
    }
    for e in edges:
        key = (e["source"], e["target"])
        e["traversed"] = key in observed or key in always_traversed

    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e["source"], []).append(e["target"])
    # Ensure terminals appear as keys with empty lists.
    for node in ("user", "memory"):
        adjacency.setdefault(node, [])

    return {
        "entry_point": "user",
        "terminal_agents": ["user", "memory"],
        "total_edges": len(edges),
        "edges": edges,
        "adjacency_list": adjacency,
    }


# ---------------------------------------------------------------------------
# Span construction
# ---------------------------------------------------------------------------


_SERVICE_NAME = "mas-example-agentleak"

# Source-call index -> (agent_name, node_name, provider_span_name)
_CALL_TO_AGENT = [
    ("single_agent", "single_agent", "ChatLiteLLM"),
    ("coordinator_agent", "coordinator", "ChatLiteLLM"),
    ("worker_agent", "worker", "ChatLiteLLM"),
    ("compiler_agent", "compiler", "ChatLiteLLM"),
]


def _llm_messages_attributes(system_prompt: str, user_prompt: str, response: str) -> dict[str, Any]:
    """OpenInference-style flattened message attributes."""
    return {
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": system_prompt,
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": user_prompt,
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": response,
    }


def _make_llm_provider_span(
    *,
    source_trace_id: str,
    trace_id_hex: str,
    parent_span_id: str,
    call: dict[str, Any],
    provider_span_name: str,
    span_path: str,
) -> dict[str, Any]:
    start = _parse_ts(call["timestamp"])
    end = _add_ms(start, float(call.get("latency_ms") or 0))
    sys_prompt = call.get("system_prompt") or ""
    user_prompt = call.get("user_prompt") or ""
    response = call.get("response") or ""

    return {
        "name": provider_span_name,
        "trace_id": trace_id_hex,
        "span_id": _span_id(source_trace_id, span_path),
        "parent_span_id": parent_span_id,
        "start_time": _fmt_ts(start),
        "end_time": _fmt_ts(end),
        "duration_ms": float(call.get("latency_ms") or 0),
        "status": "ERROR" if call.get("error") else "OK",
        "status_message": call.get("error"),
        "kind": "INTERNAL",
        "service_name": _SERVICE_NAME,
        "openinference": {
            "input_value": json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                }
            ),
            "input_mime_type": "application/json",
            "output_value": json.dumps(
                {
                    "generations": [
                        {
                            "text": response,
                            "generation_info": {
                                "finish_reason": "STOP",
                                "model_name": call.get("model"),
                            },
                            "type": "ChatGeneration",
                            "message": {"role": "assistant", "content": response},
                        }
                    ]
                }
            ),
            "output_mime_type": "application/json",
            "llm_model_name": call.get("model"),
            "llm_token_count_prompt": int(call.get("tokens_prompt") or 0),
            "llm_token_count_completion": int(call.get("tokens_completion") or 0),
            "llm_token_count_total": int(
                (call.get("tokens_prompt") or 0) + (call.get("tokens_completion") or 0)
            ),
        },
        "attributes": _llm_messages_attributes(sys_prompt, user_prompt, response),
        "events": [],
        "eval_metadata": None,
    }


def _make_agent_span(
    *,
    source_trace_id: str,
    trace_id_hex: str,
    parent_span_id: str,
    agent_name: str,
    node_name: str,
    call: dict[str, Any],
    span_path: str,
) -> dict[str, Any]:
    start = _parse_ts(call["timestamp"])
    end = _add_ms(start, float(call.get("latency_ms") or 0))
    return {
        "name": node_name,
        "trace_id": trace_id_hex,
        "span_id": _span_id(source_trace_id, span_path),
        "parent_span_id": parent_span_id,
        "start_time": _fmt_ts(start),
        "end_time": _fmt_ts(end),
        "duration_ms": float(call.get("latency_ms") or 0),
        "status": "ERROR" if call.get("error") else "OK",
        "status_message": call.get("error"),
        "kind": "INTERNAL",
        "service_name": _SERVICE_NAME,
        "openinference": {
            "input_value": call.get("user_prompt") or "",
            "input_mime_type": "text/plain",
            "output_value": call.get("response") or "",
            "output_mime_type": "text/plain",
        },
        "attributes": {
            "graph.node": node_name,
            "agent.name": agent_name,
        },
        "events": [],
        "eval_metadata": None,
    }


def _make_channel_span(
    *,
    source_trace_id: str,
    trace_id_hex: str,
    parent_span_id: str,
    msg: dict[str, Any],
    span_path: str,
    start: _dt.datetime,
    end: _dt.datetime,
) -> dict[str, Any]:
    name = f"channel_send:{msg.get('channel')}"
    return {
        "name": name,
        "trace_id": trace_id_hex,
        "span_id": _span_id(source_trace_id, span_path),
        "parent_span_id": parent_span_id,
        "start_time": _fmt_ts(end),
        "end_time": _fmt_ts(end),
        "duration_ms": 0.0,
        "status": "OK",
        "status_message": None,
        "kind": "INTERNAL",
        "service_name": _SERVICE_NAME,
        "openinference": {
            "input_value": msg.get("content") or "",
            "input_mime_type": "text/plain",
            "output_value": msg.get("content") or "",
            "output_mime_type": "text/plain",
        },
        "attributes": {
            "channel.name": msg.get("channel"),
            "channel.source": msg.get("source"),
            "channel.target": msg.get("target"),
            "channel.has_leak": bool(msg.get("has_leak")),
            "channel.leaked_fields": list(msg.get("leaked_fields") or []),
            "channel.llm_call_id": msg.get("llm_call_id"),
        },
        "events": [],
        "eval_metadata": None,
    }


def _make_root_span(
    *,
    source: dict[str, Any],
    source_trace_id: str,
    trace_id_hex: str,
    start: _dt.datetime,
    end: _dt.datetime,
) -> dict[str, Any]:
    duration = (end - start).total_seconds() * 1000.0
    return {
        "name": "AgentLeakScenario",
        "trace_id": trace_id_hex,
        "span_id": _span_id(source_trace_id, "root"),
        "parent_span_id": None,
        "start_time": _fmt_ts(start),
        "end_time": _fmt_ts(end),
        "duration_ms": duration,
        "status": "OK",
        "status_message": None,
        "kind": "INTERNAL",
        "service_name": _SERVICE_NAME,
        "openinference": {
            "input_value": (source.get("input") or {}).get("request") or "",
            "input_mime_type": "text/plain",
            "output_value": "",
            "output_mime_type": "text/plain",
        },
        "attributes": {
            "scenario.id": source.get("scenario_id"),
            "scenario.vertical": source.get("vertical"),
            "scenario.attack_family": source.get("attack_family"),
            "scenario.model": source.get("model"),
        },
        "events": [],
        "eval_metadata": None,
    }


# ---------------------------------------------------------------------------
# Per-trace conversion
# ---------------------------------------------------------------------------


def convert_trace(source: dict[str, Any]) -> dict[str, Any]:
    src_trace_id = source.get("trace_id") or ""
    trace_id_hex = _trace_id(src_trace_id)
    session_id = _session_id(src_trace_id)

    calls: list[dict[str, Any]] = list(source.get("llm_calls") or [])
    msgs: list[dict[str, Any]] = list(source.get("channel_messages") or [])

    # Compute root timing from earliest call start and latest call end.
    if calls:
        starts = [_parse_ts(c["timestamp"]) for c in calls if c.get("timestamp")]
        ends = [
            _add_ms(_parse_ts(c["timestamp"]), float(c.get("latency_ms") or 0))
            for c in calls
            if c.get("timestamp")
        ]
        root_start = min(starts)
        root_end = max(ends)
    else:
        root_start = _parse_ts(
            source.get("timestamp") or _dt.datetime.now(_dt.timezone.utc).isoformat()
        )
        root_end = root_start

    root_span = _make_root_span(
        source=source,
        source_trace_id=src_trace_id,
        trace_id_hex=trace_id_hex,
        start=root_start,
        end=root_end,
    )
    root_span_id = root_span["span_id"]

    spans: list[dict[str, Any]] = [root_span]

    # Index llm_calls by id so we can attach channel sends to the originating agent span.
    call_by_id: dict[str, dict[str, Any]] = {}
    for c in calls:
        if c.get("call_id"):
            call_by_id[c["call_id"]] = c

    # Track the agent-span span_id keyed by call_id so we can parent channel spans.
    agent_span_id_by_call_id: dict[str, str] = {}

    for i, call in enumerate(calls):
        if i >= len(_CALL_TO_AGENT):
            agent_name = f"agent_{i}"
            node_name = f"node_{i}"
            provider_name = "ChatLiteLLM"
        else:
            agent_name, node_name, provider_name = _CALL_TO_AGENT[i]

        agent_path = f"agent/{i}/{node_name}"
        agent_span = _make_agent_span(
            source_trace_id=src_trace_id,
            trace_id_hex=trace_id_hex,
            parent_span_id=root_span_id,
            agent_name=agent_name,
            node_name=node_name,
            call=call,
            span_path=agent_path,
        )
        spans.append(agent_span)
        if call.get("call_id"):
            agent_span_id_by_call_id[call["call_id"]] = agent_span["span_id"]

        provider_path = f"{agent_path}/provider"
        provider_span = _make_llm_provider_span(
            source_trace_id=src_trace_id,
            trace_id_hex=trace_id_hex,
            parent_span_id=agent_span["span_id"],
            call=call,
            provider_span_name=provider_name,
            span_path=provider_path,
        )
        spans.append(provider_span)

    # Channel-message spans.
    # Parent the channel span on the originating agent's span (via llm_call_id)
    # when available; otherwise (e.g. C5 memory write) parent on worker_agent.
    worker_span_id = None
    for s in spans:
        if s.get("attributes", {}).get("agent.name") == "worker_agent":
            worker_span_id = s["span_id"]
            break

    for j, msg in enumerate(msgs):
        call_id = msg.get("llm_call_id")
        if call_id and call_id in agent_span_id_by_call_id:
            parent_id = agent_span_id_by_call_id[call_id]
            ref_call = call_by_id.get(call_id)
            ref_start = _parse_ts(ref_call["timestamp"]) if ref_call else root_start
            ref_end = (
                _add_ms(ref_start, float(ref_call.get("latency_ms") or 0))
                if ref_call
                else root_end
            )
        else:
            # Side-channel (memory) write: no LLM call \u2014 happens after the worker.
            parent_id = worker_span_id or root_span_id
            # Use worker span end as the timestamp, or root_end as fallback.
            worker_call = calls[2] if len(calls) > 2 else None
            if worker_call and worker_call.get("timestamp"):
                ref_start = _parse_ts(worker_call["timestamp"])
                ref_end = _add_ms(ref_start, float(worker_call.get("latency_ms") or 0))
            else:
                ref_start = root_start
                ref_end = root_end

        spans.append(
            _make_channel_span(
                source_trace_id=src_trace_id,
                trace_id_hex=trace_id_hex,
                parent_span_id=parent_id,
                msg=msg,
                span_path=f"channel/{j}/{msg.get('channel')}",
                start=ref_start,
                end=ref_end,
            )
        )

    # Final output = last C1 message addressed to user (from compiler).
    final_output = None
    for m in reversed(msgs):
        if m.get("channel") == "C1" and m.get("target") == "user":
            final_output = m.get("content")
            break

    route_taken = (
        "user -> single_agent -> user || "
        "user -> coordinator_agent -> worker_agent -> coordinator_agent "
        "-> compiler_agent -> user (worker_agent -> memory)"
    )

    vault = (source.get("input") or {}).get("vault") or {}
    metadata = {
        "scenario_id": source.get("scenario_id"),
        "vertical": source.get("vertical"),
        "attack_family": source.get("attack_family"),
        "model": source.get("model"),
        "results": source.get("results") or {},
        "metrics": source.get("metrics") or {},
        "allowed_set": (source.get("input") or {}).get("allowed_set") or {},
        # Record vault field names only; the original vault is preserved in the
        # source traces and we don't want to duplicate canary PII.
        "vault_field_names": sorted(vault.keys()),
    }

    session = {
        "session_id": session_id,
        "trace_id": trace_id_hex,
        "start_time": _fmt_ts(root_start),
        "end_time": _fmt_ts(root_end),
        "duration_ms": (root_end - root_start).total_seconds() * 1000.0,
        "input_query": (source.get("input") or {}).get("request"),
        "final_output": final_output,
        "route_taken": route_taken,
        "spans": spans,
        "metadata": metadata,
    }

    out = {
        "export_time": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(),
        "agents_registry": _build_agents_registry(calls),
        "tools_registry": _build_tools_registry(),
        "communication_graph": _build_communication_graph(msgs),
        "session_count": 1,
        "span_count": len(spans),
        "sessions": [session],
    }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _convert_one(args: tuple[str, str, str]) -> tuple[str, bool, str]:
    src_path, dst_path, _name = args
    try:
        with open(src_path, "r") as fh:
            source = json.load(fh)
        out = convert_trace(source)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(dst_path, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        return src_path, True, ""
    except Exception as e:  # noqa: BLE001
        return src_path, False, f"{type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", default="traces", help="Source directory of trace_*.json files")
    parser.add_argument(
        "--dst",
        default="research_traces",
        help="Destination directory for research-team-style JSON files",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Optional cap on number of files to convert (debug)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Parallel worker count (default: ncpu-1)",
    )
    args = parser.parse_args(argv)

    src_dir = args.src
    dst_dir = args.dst
    if not os.path.isdir(src_dir):
        print(f"error: source directory {src_dir!r} not found", file=sys.stderr)
        return 2

    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".json"))
    if args.limit:
        files = files[: args.limit]
    print(f"Converting {len(files)} traces from {src_dir!r} -> {dst_dir!r} ...", flush=True)

    jobs = []
    for f in files:
        # Replace the "trace_" prefix with "research_trace_" for clarity.
        out_name = f.replace("trace_", "research_trace_", 1)
        jobs.append((os.path.join(src_dir, f), os.path.join(dst_dir, out_name), f))

    ok = 0
    fail = 0
    failures: list[tuple[str, str]] = []
    if args.workers <= 1:
        for job in jobs:
            _, success, err = _convert_one(job)
            if success:
                ok += 1
            else:
                fail += 1
                failures.append((job[0], err))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_convert_one, j) for j in jobs]
            for n, fut in enumerate(as_completed(futures), 1):
                src, success, err = fut.result()
                if success:
                    ok += 1
                else:
                    fail += 1
                    failures.append((src, err))
                if n % 250 == 0 or n == len(futures):
                    print(f"  {n}/{len(futures)} done (ok={ok}, fail={fail})", flush=True)

    print(f"Done. ok={ok}, fail={fail}")
    if failures:
        print("First few failures:")
        for src, err in failures[:5]:
            print(f"  {src}: {err}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
