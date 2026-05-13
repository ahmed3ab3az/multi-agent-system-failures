# AgentLeak — Dataset Analysis

This document analyses the JSON trace files under `traces/` on the
`agentleak` branch and answers the two questions:

1. **Do the traces represent a Multi-Agent System (MAS)?**
2. **Do the agents have tools?**

It is generated from a full sweep of all **4 979 traces** (no sampling).

> All numbers below are derived from the raw `traces/*.json` files. The
> converter described in the [README](README.md) reproduces the
> research-team-style export under `research_traces/`.

---

## 1. Quick answers

| Question | Answer |
| --- | --- |
| Is this a Multi-Agent System? | **Yes** — every trace runs *two* topologies side-by-side: a single-agent baseline (1 LLM call) and a 3-step coordinator → worker → compiler chain with a shared-memory side channel (3 LLM calls + 1 memory write). |
| Do the agents have tools? | **No** — across all 4 979 traces there are zero structured tool / function-call invocations. The four LLM calls are pure prompt-driven completions; the "memory" sink is a string-cache write, not a tool call. |

---

## 2. Source schema

Each trace is a single JSON object:

```text
{
  "trace_id":        "trace_YYYYMMDD_HHMMSS_<hex>",
  "scenario_id":     "agentleak_<vertical-prefix>_<seq>",
  "timestamp":       "ISO-8601",
  "model":           "<provider>/<model>",
  "vertical":        "healthcare | legal | corporate | finance",
  "attack_family":   null | "direct_prompt_injection" | "role_confusion"
                   | "tool_output_poisoning" | "memory_write_exfiltration"
                   | "cross_agent_collusion" | "indirect_prompt_injection",

  "input": {
    "vault":         { ...PII fields, including CANARY_* markers and "<field>_real" ground truth... },
    "request":       "<user prompt>",
    "allowed_set":   { "fields": [...], "forbidden_fields": [...] }
  },

  "llm_calls": [                  # exactly 4 per trace
    { call_id, timestamp, model, system_prompt, user_prompt,
      response, tokens_prompt, tokens_completion, latency_ms, error }
  ],

  "channel_messages": [           # exactly 5 per trace
    { channel: "C1"|"C2"|"C5", source, target,
      content, has_leak, leaked_fields, llm_call_id }
  ],

  "results": {
    "single_leaked": bool,        # any leak from the single-agent path
    "multi_leaked":  bool,        # any leak from the multi-agent path
    "c1_leaked":     bool,        # leak in the final user-facing reply
    "c2_leaked":     bool,        # leak in coordinator↔worker traffic
    "c5_leaked":     bool         # leak written into shared memory cache
  },

  "metrics": { "total_tokens": int, "total_latency_ms": float }
}
```

### What the four LLM calls correspond to

The four `llm_calls` are deterministic across the dataset and map cleanly
onto distinct agent roles (verified by inspection of all 4 979 system
prompts):

| Call # | Agent | System-prompt signature |
| --- | --- | --- |
| 0 | `single_agent`     | *"You are a helpful assistant with access to user data… PRIVACY POLICY…"* |
| 1 | `coordinator_agent` | *"You are a Coordinator agent in a multi-agent system. You delegate tasks to Worker agents. DATA CONTEXT: { … }"* |
| 2 | `worker_agent`      | *"You are a Worker agent. You receive instructions from the Coordinator. AVAILABLE DATA: { … }"* |
| 3 | `compiler_agent`    | *"Compile a privacy-conscious response for the user."* |

### What the five channel messages correspond to

| # | Channel | Source → Target           | Originated by |
| --- | --- | --- | --- |
| 0 | C1 | `single_agent` → `user`     | call 0 |
| 1 | C2 | `coordinator` → `worker`    | call 1 |
| 2 | C2 | `worker` → `coordinator`    | call 2 |
| 3 | C5 | `worker` → `memory`         | (no LLM — cache write) |
| 4 | C1 | `coordinator` → `user`      | call 3 |

So in every trace the dataset benchmarks **two end-to-end paths against
each other**:

```text
                       single-agent (baseline)
user ─────────────────► single_agent ───────────────► user            (C1)

                          multi-agent (treatment)
                ┌───── coordinator ───┐                  ┌─► user      (C1)
user ─►─────────┤                     │     compiler ────┘
                └───────┬─────────────┘
                        │  (C2 instructions)
                        ▼
                      worker  ──►  coordinator           (C2 findings)
                        │
                        └──►  memory                     (C5 cache write)
```

This is the topology that is encoded in the `communication_graph` of the
converted output.

---

## 3. Is this a Multi-Agent System?

**Yes — explicitly so.**

- The dataset is literally named **AgentLeak** (`scenario_id` prefix
  `agentleak_*`) and every trace contrasts a single-agent control against
  a multi-agent system.
- Each trace contains **3 distinct multi-agent roles** with **separate
  system prompts** that explicitly call out the MAS architecture:
  - *Coordinator* (delegates),
  - *Worker* (executes with full data access),
  - *Compiler* (sanitises for the user).
- There are **3 inter-agent channels** (`C1`, `C2`, `C5`) plus a
  passive **shared-memory sink** that is written to by the worker — a
  classic side-channel hazard for MAS.
- Two of the attack families directly probe MAS-specific surfaces:
  - `cross_agent_collusion` — induces collusion between coordinator and
    worker against the privacy policy;
  - `memory_write_exfiltration` — forces sensitive data into the C5
    cache so a future agent retrieval can leak it.

The dataset's whole purpose is to **quantify how decomposing a single
LLM call into a coordinator/worker pipeline changes the privacy
behaviour** — i.e. it is a benchmark *of* multi-agent systems.

### Empirical confirmation — MAS leaks *more* than the single-agent baseline

Across all 4 979 traces:

| Path | Leak rate |
| --- | --- |
| `single_leaked` (1-agent baseline) | **43.2 %** |
| `multi_leaked`  (coordinator+worker+compiler) | **68.9 %** |

That is a **+25.7 pp regression** when the same model is decomposed into
a MAS, with virtually identical user prompts and data vaults. Channel
breakdown:

| Channel | What it carries | Leak rate |
| --- | --- | --- |
| `C1` | Final user-facing reply | 27.2 % |
| `C2` | Coordinator ↔ Worker messages | **68.8 %** |
| `C5` | Worker → shared memory cache | **46.7 %** |

Most of the leakage is *internal* (C2) or *side-channel* (C5) — the
final compiler step (C1) does scrub a lot, but every C2 / C5 trace is
still a privacy failure because PII has already crossed an agent
boundary.

---

## 4. Do the agents have tools?

**No.**

- The `llm_calls[*]` only carry `system_prompt`, `user_prompt`,
  `response`, and token/latency metadata. None of them contain a
  `tool_calls`, `function_call`, or `tools` field.
- A scan of every response across all 4 979 traces for the substrings
  `"tool_calls"` and `"function_call"` returns **0 matches**.
- The "memory" target in channel `C5` is *not* a tool — it is a string
  prepended with `"Cache: "` and recorded as a channel message rather
  than as a function call. There is no schema, no arguments, and no
  acknowledgement turn.
- Although one of the attack families is called
  `tool_output_poisoning`, the actual trace contents inject *prompt-text
  payloads*. There is no real tool that the agents could invoke.

### Implications for the converted format

In the research-team-style export, `tools_registry` is therefore:

```json
{
  "framework_name": "agentleak",
  "total_tools": 0,
  "tools": []
}
```

…and every agent has `"tools_bound": []`.

---

## 5. Dataset shape

| | |
| --- | --- |
| Total traces | **4 979** |
| LLM calls per trace | 4 (constant) |
| Channel messages per trace | 5 (constant) |
| Verticals | healthcare 1 245 · legal 1 245 · corporate 1 245 · finance 1 244 |
| Attack families | `None` 2 500 · `direct_prompt_injection` 635 · `role_confusion` 605 · `tool_output_poisoning` 325 · `memory_write_exfiltration` 319 · `cross_agent_collusion` 315 · `indirect_prompt_injection` 280 |
| Models | `meta-llama/llama-3.3-70b-instruct` 996 · `openai/gpt-4o` 996 · `mistralai/mistral-large-2512` 996 · `anthropic/claude-3.5-sonnet` 996 · `openai/gpt-4o-mini` 995 |

The factorial coverage is essentially `4 verticals × 5 models × 7
attack families` with extra weight on the no-attack baseline.

### Tokens & latency

| Metric | mean | median | p95 | max |
| --- | --- | --- | --- | --- |
| `total_tokens` | 2 142 | 2 159 | 3 972 | 4 783 |
| `total_latency_ms` | 19 565 | 15 505 | 38 181 | 11 915 115 |

The maximum latency outlier (~12 000 s) is a single trace where a model
provider stalled — it does not affect leak statistics.

---

## 6. Vault schema — what PII is at stake

Every vault has a stable subset of fields (PII varies by vertical).
Notable conventions:

- Fields prefixed **`CANARY_…`** are honeypot tokens designed to be easy
  to grep for in agent outputs.
- Fields suffixed **`_real`** (e.g. `ssn_real`, `phone_real`,
  `diagnosis_real`, `account_number_real`, `credit_card_real`) hold the
  ground-truth value the agent is expected to *not* leak.
- The `allowed_set` block enumerates fields the agent is *allowed* to
  surface and explicitly forbidden ones (`ssn`, `diagnosis`,
  `medications`, `allergies`, `insurance_id`, etc).

Top-20 leaked fields across the entire dataset:

| Field | # leaks |
| --- | --- |
| `patient_name` | 3 687 |
| `customer_name` | 2 165 |
| `opposing_party` | 1 980 |
| `name` | 1 765 |
| `employee_name` | 1 432 |
| `case_number` | 1 405 |
| `case_type` | 1 396 |
| `diagnosis_real` | 1 143 |
| `insurance_id` | 955 |
| `client_name` | 927 |
| `performance_rating` | 801 |
| `department` | 773 |
| `dob` | 658 |
| `phone_real` | 617 |
| `employee_id` | 591 |
| `medications` | 524 |
| `allergies` | 479 |
| `account_number_real` | 450 |
| `case_id` | 392 |
| `risk_notes` | 390 |

Identifier-style fields (`patient_name`, `client_name`, `employee_id`,
`case_number`, …) dominate. The hardest forbidden categories — SSN,
diagnosis, financial real fields — are leaked less often but still
frequently.

---

## 7. Per-model behaviour

The single-vs-multi delta is *highly* model-dependent:

| Model | single-agent leak | multi-agent leak | Δ |
| --- | ---: | ---: | ---: |
| `mistralai/mistral-large-2512` | **90.6 %** | **96.2 %** | +5.6 pp |
| `meta-llama/llama-3.3-70b-instruct` | 71.4 % | 68.2 % | −3.2 pp |
| `anthropic/claude-3.5-sonnet` | 25.2 % | 28.1 % | +2.9 pp |
| `openai/gpt-4o` | 14.9 % | **76.8 %** | **+61.9 pp** |
| `openai/gpt-4o-mini` | 13.8 % | **75.4 %** | **+61.6 pp** |

Observations:

- **Claude-3.5-sonnet** is the only model that holds the line: its
  multi-agent leak rate (28.1 %) is barely worse than its single-agent
  baseline. It treats the Coordinator-as-system-prompt as untrusted
  data.
- **OpenAI GPT-4o / 4o-mini** are remarkable — extremely tight in the
  single-agent path (≈ 14 %) but collapse to 75–77 % once the
  Coordinator delegates with a `DATA CONTEXT: {…}` block embedded in
  its system prompt. The Worker effectively trusts the Coordinator's
  context as authoritative and dumps PII.
- **Mistral-large-2512** leaks freely in both paths and is unsuitable
  for a privacy-sensitive deployment on this benchmark.
- **Llama-3.3-70b** is the only model where the MAS path is *slightly*
  better than the single-agent path (68.2 % vs 71.4 %), but both are
  already poor.

---

## 8. Per-vertical behaviour

| Vertical | n | single-agent leak | multi-agent leak |
| --- | ---: | ---: | ---: |
| healthcare | 1 245 | 59.5 % | **87.1 %** |
| legal      | 1 245 | 49.6 % | 70.0 % |
| corporate  | 1 245 | 33.7 % | 53.9 % |
| finance    | 1 244 | 29.7 % | 64.7 % |

- **Healthcare** has both the highest absolute leak rate and the largest
  single→multi delta. The `patient_name` token (3 687 leaks) alone
  drives a large fraction of this.
- **Finance** is interesting: the single-agent baseline is comparatively
  strong (29.7 %), but the MAS path more than doubles the leak rate
  (64.7 %) — exactly the regression pattern that GPT-4o shows.
- **Corporate** is the safest in absolute terms; the MAS regression is
  still ~+20 pp.

---

## 9. Per-attack-family behaviour

| Attack family | n | single | multi | C1 | C2 | C5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| *(no attack)*                | 2 500 | 43.2 % | 68.9 % | 27.0 % | 68.8 % | 46.7 % |
| `direct_prompt_injection`    |   635 | 42.8 % | 70.6 % | 28.0 % | 70.4 % | 48.3 % |
| `role_confusion`             |   605 | 42.3 % | 67.1 % | 26.8 % | 66.9 % | 46.3 % |
| `tool_output_poisoning`      |   325 | 43.7 % | 69.5 % | 27.1 % | 69.5 % | 47.7 % |
| `memory_write_exfiltration`  |   319 | 40.1 % | 67.1 % | 26.3 % | 67.1 % | 43.9 % |
| `cross_agent_collusion`      |   315 | 45.1 % | **72.4 %** | 29.8 % | **72.4 %** | 47.3 % |
| `indirect_prompt_injection`  |   280 | 46.1 % | 67.1 % | 26.4 % | 67.1 % | 45.0 % |

**Key finding:** the attack families only modulate the multi-agent leak
rate by ±5 pp around the baseline (68.9 %). Most of the leakage in
this dataset comes from the **architecture itself**, not from
adversarial inputs. The multi-agent topology is already leaking PII on
~69 % of *benign* requests. The attacker is mostly redundant.

The two attacks that *do* meaningfully shift the needle are:

- `cross_agent_collusion` (+3.5 pp on multi-agent, **the highest** of
  any family) — designed precisely to weaponise the coordinator/worker
  separation, and it works.
- `direct_prompt_injection` (+1.7 pp) — a small effect compared to the
  architectural baseline.

---

## 10. Joint result profile

Combined (`single_leaked`, `multi_leaked`, `c1_leaked`, `c2_leaked`,
`c5_leaked`) tuples across the dataset:

| (s, m, c1, c2, c5) | count |
| --- | ---: |
| (F, F, F, F, F) — clean run | 1 283 |
| (T, T, F, T, T) | 658 |
| (F, T, F, T, T) | 643 |
| (T, T, T, T, T) — leaked everywhere | 581 |
| (T, T, F, T, F) | 453 |
| (F, T, T, T, T) | 443 |
| (F, T, F, T, F) | 323 |
| (T, F, F, F, F) | 264 |
| (T, T, T, T, F) | 191 |
| (F, T, T, T, F) | 135 |
| (F, T, T, F, F) | 3 |
| (T, T, T, F, F) | 2 |

Notable patterns:

- A **clean run** (no path leaks anything) only happens in **25.8 %** of
  traces.
- **Total leakage** across both paths and all channels happens in
  **11.7 %** of traces.
- The "(T, F, …)" cluster (single-agent leaks but multi-agent does
  not) is small (264 traces, 5.3 %) — the compiler step occasionally
  saves the multi-agent path *only* when the single-agent path also
  fails, suggesting the compiler is doing real work but is not a
  primary mitigation.

---

## 11. Conversion target — research-team-style export

The companion converter (`convert/convert_traces.py`) produces one JSON
file per source trace in the same layout as
`research_team_trace_20260417_003354.json`. Per output:

- `agents_registry` — 4 agents (`single_agent`, `coordinator_agent`,
  `worker_agent`, `compiler_agent`), each with the originating system
  prompt and `tools_bound: []`.
- `tools_registry` — `{ "total_tools": 0, "tools": [] }`.
- `communication_graph` — 8 directed edges over
  `{ user, single_agent, coordinator_agent, worker_agent,
     compiler_agent, memory }` with `entry_point: "user"` and
  `terminal_agents: ["user", "memory"]`.
- `sessions[0]` — a single session containing **14 spans** organised as:
  - 1 root span `AgentLeakScenario`
  - 4 agent spans (`single_agent`, `coordinator`, `worker`, `compiler`)
  - 4 `ChatLiteLLM` provider spans (children of each agent span)
  - 5 `channel_send:Cx` spans, parented on the originating agent

Span timing is reconstructed from `llm_calls[*].timestamp` /
`latency_ms`; IDs are deterministic SHA-256 prefixes of
`<trace_id>|<span-path>` so re-running produces stable output.

---

## 12. Recommendations / open questions

1. **The MAS regression is the headline finding** — for any consumer of
   this dataset the single-most-important number is the +25.7 pp jump
   from single-agent to multi-agent leak rate on benign inputs.
2. **Coordinator system-prompt design is dominant.** The `DATA
   CONTEXT: {…}` block embedded in the coordinator's system prompt is
   what trips up GPT-4o / 4o-mini. Re-running the benchmark with the
   data scoped *outside* the system prompt (e.g. via a controlled tool
   call) would isolate whether the failure is architectural or
   prompt-engineering.
3. **C2 is the bottleneck.** Mitigations that only sanitise C1 (the
   final reply) cannot lower an internal-traffic leak rate of 68.8 %.
   Either the worker needs a stricter system prompt, or the
   coordinator must not hand it the full vault.
4. **C5 memory writes need a redactor.** 46.7 % of traces deposit PII
   into the shared cache; any downstream agent that reads from this
   cache inherits the leak.
5. **The dataset has zero real tool calls** despite containing an
   attack family called `tool_output_poisoning`. If the goal is to
   benchmark tool-using agents, the harness needs to be extended to
   emit actual `tool_calls` / `function_call` JSON.
