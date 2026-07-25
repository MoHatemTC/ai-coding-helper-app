# Agentic System Rebuild — State Contract, Node Boundaries, Findings Schema, Model Registry

Week 2 deliverable (Jana). This document is the reference for everything the
LangGraph state machine in `app/core/langgraph/graph.py` does: what each
node is allowed to read and write, how the parallel review lanes stay safe
under concurrency, how the model registry's fallback and time budget work,
and the handful of deliberate design decisions made while wiring the
guardrail (Yousef), review (Aly), and hint (Yousef/Haitham-adjacent) nodes
into one graph.

## 1. Graph shape

```
                    User message (+ optional code)
                                │
                                ▼
                        ┌───────────────┐
                        │  inbound_dlp  │   Stage 1 — regex/entropy, no LLM, <5ms
                        └───────┬───────┘
                       safe ────┴──── secret found
                        │                │
                        ▼                ▼
                ┌────────────────┐   ┌──────────────────┐
                │ inbound_intent │   │ guardrail_redirect│──► END
                └───────┬────────┘   └──────────────────┘
                safe ───┴─── blocked
                 │              │
                 ▼              ▼
      ┌──────────────────────┐ (same guardrail_redirect node, reused)
      │ correctness│security │
      │ │performance (parallel)│
      └──────────┬───────────┘
                 ▼
              ┌───────┐
              │ hints │   skipped (no-op) when there's no code and no findings
              └───┬───┘
                  ▼
              ┌───────┐   tool call ┌───────────┐
              │ chat  │────────────►│ tool_call │
              └───┬───┘◄────────────└───────────┘
                  │ draft ready
                  ▼
             ┌──────────┐
             │ outbound │──► END
             └──────────┘
```

Nine nodes, each with exactly one job. `chat` is the only node that produces
user-facing prose from an LLM in the normal path; `guardrail_redirect` is the
only node that produces user-facing prose for a *blocked* turn, and it never
calls an LLM itself (see §3).

## 2. Why `GraphState` is a `TypedDict`, not a Pydantic model

The previous `GraphState` was a Pydantic `BaseModel`. Every guardrail, review,
and hint node already written by the team (`inbound_dlp_node`,
`inbound_intent_node`, `outbound_node`, `security_review_node`,
`generate_hint_node`) reads state via `state.get("key")` / `state["key"]` —
plain dict access, which a Pydantic `BaseModel` doesn't support. Rather than
rewrite five files owned by three different teammates, `GraphState` is now a
`TypedDict(total=False)`. This is also the more idiomatic LangGraph pattern
for this kind of graph — it's what every node in this codebase was already
written against.

`total=False` means every node receives and returns a *partial* dict — no
field is guaranteed present on every read, and every node's return value is
merged into the running state by field:

- Fields with no reducer: plain overwrite, last writer wins.
- `messages`: `add_messages` reducer — appends by default, replaces in place
  when a returned message shares an `id` with an existing one (used by
  `_outbound` — see §3).
- `findings`: `operator.add` reducer — concurrent appends from the three
  review nodes merge safely instead of racing to overwrite each other. This
  is what makes running Correctness/Security/Performance in the same
  parallel step safe, per the Week 2 requirement.

## 3. Per-node contract

| Node | Reads | Writes | Routes to |
|---|---|---|---|
| `inbound_dlp` (Yousef) | `user_query`, `code` | `is_safe_sensitive`, `detected_secret_types`, `sanitized_query`, `sanitized_code`, `inbound_trigger_reason` | `inbound_intent` / `guardrail_redirect` |
| `inbound_intent` (Yousef) | `sanitized_query`, `sanitized_code`, `problem_id` | `is_safe_intent`, `inbound_trigger_reason`, `constructive_redirect` | `[correctness, security, performance]` / `guardrail_redirect` |
| `guardrail_redirect` | `inbound_trigger_reason`, `constructive_redirect` | `messages` (1 AIMessage), `final_response` | `END` |
| `correctness` | `sanitized_code`, `code` | `findings` (append) | `hints` (after join) |
| `security` (Yousef) | `code`/`sanitized_code`, `language` | `findings` (append) | `hints` (after join) |
| `performance` | `sanitized_code`, `code`, `language` | `findings` (append) | `hints` (after join) |
| `hints` | `code`, `findings`, `problem_id`, `hint_state`, `user_query` | `latest_hint`, `hint_state` (no-op if no code/findings) | `chat` |
| `chat` | `messages`, `code`, `language`, `long_term_memory`, `skill_profile`, `findings`, `latest_hint` | `messages` (1 AIMessage), `draft_response` | `tool_call` / `outbound` |
| `tool_call` | `messages` (last message's tool_calls) | `messages` (1 ToolMessage per call) | `chat` |
| `outbound` (Yousef, wrapped) | `draft_response`, `sanitized_query`, `sanitized_code` | `is_safe_output`, `outbound_trigger_reason`, `constructive_redirect`, `final_response`, `messages` (replaced in place if blocked) | `END` |

Two things worth calling out explicitly:

**Findings-only-appear-once-DLP-passes.** `correctness`/`security`/`performance`
only ever run after `inbound_dlp` has already confirmed `is_safe_sensitive`.
By that point `sanitized_code` and `code` are identical (redaction is a
no-op when nothing was found), so it doesn't matter which one a review node
reads — no secret ever reaches the review lanes to begin with.

**`_outbound` repairs the transcript, not just the delivered text.**
`outbound_node` (as written) only decides what text should be *delivered*
this turn — it doesn't touch `messages`. Left as-is, a blocked draft (e.g. a
full-solution leak) would still sit in `state["messages"]` forever:
checkpointed, fed back to the model as conversation history on the next
turn, and pushed into mem0 long-term memory. `graph.py`'s `_outbound` wraps
the raw node and, only when blocked, replaces the draft `AIMessage` with the
safe text using the *same message id* — the `add_messages` reducer treats a
same-id message as a replacement, not an append.

## 4. `findings` schema

`Finding` (`app/schemas/review.py`) is unchanged:

```python
class Finding(BaseModel):
    line: int             # 1-based source line
    severity: Severity    # low | medium | high | critical
    category: Category    # correctness | security | performance | style
    message: str           # one-sentence, user-facing
    rationale: str          # why it's a problem
```

All three review nodes now return `finding.model_dump()` dicts (not raw
`Finding` objects) so everything appended to `GraphState["findings"]` is
uniformly JSON-serializable dicts — safer for the Postgres checkpointer and
for `convert_to_openai_messages`/logging, and it's what `generate_hint_node`
already defensively re-validates (`Finding.model_validate(raw_finding)`) for
each entry, so this direction was already the path of least resistance.

`correctness_node` had a real bug fixed as part of this rebuild: it was
reading `state.messages[-1].content` (the latest **chat message**) instead
of the submitted code, so its div-by-zero regex was checking the user's
prose, not their code. It now reads `sanitized_code`/`code` like the other
two review lanes.

`performance_node.py` previously only exposed `run_performance_review(code,
language)` — a plain function, not a graph node. Added
`performance_review_node(state)` as a thin wrapper so all three review lanes
share the same node signature and can be wired into the parallel fan-out
identically.

## 5. Model registry & latency strategy

Unchanged from the existing implementation — it already met every relevant
requirement, so this rebuild reuses it as-is:

- **Registry** (`app/services/llm/registry.py`): a static list of
  pre-initialized `ChatOpenAI` instances, all pointed at the LiteLLM proxy
  (`settings.LITELLM_BASE_URL`). `LLMRegistry.get(name)` looks one up by
  name; `get_model_at_index` wraps around (`% len(LLMS)`), which is what
  makes the fallback circular. As of 22 Jul this is 2 entries —
  `fw-kimi-k2.6` (name kept for `settings.DEFAULT_LLM_MODEL` compatibility;
  its underlying `model=` was repointed to `kimi-k2.5`, the confirmed-working
  name under the current LiteLLM access grant) and `kimi-k2.6`. A third
  entry that duplicated `kimi-k2.5` under a second name was removed — see
  §10.1 below, it was a fallback *slot* without a fallback *model*.
- **Circular fallback** (`app/services/llm/service.py`,
  `_switch_to_next_model` / `_fallback_loop`): on a primary-model failure
  (`RateLimitError`, `APITimeoutError`, `APIError`), the service advances to
  the next registry entry and retries, wrapping back to index 0 if it runs
  off the end, until every model has been tried once or one succeeds. Tool
  bindings are preserved across the switch (`bind_tools` is re-applied to
  the new instance).
- **Total-time budget** (`settings.LLM_TOTAL_TIMEOUT`, default 60s): the
  entire fallback loop — not each individual model — is wrapped in
  `asyncio.wait_for(...)`. A model that's merely slow (not erroring) can
  still exhaust the whole budget; that's an intentional ceiling on worst-case
  turn latency, not a per-model timeout.
- **Per-model retry**: each individual model call gets its own
  exponential-backoff retry (`tenacity`, `MAX_LLM_CALL_RETRIES`, default 3)
  before the service gives up on that model and advances to the next one.

Parallelization: the three review nodes run concurrently (LangGraph fan-out,
§1), which is the graph-level parallel-LLM-call requirement. `tool_call`
also runs multiple simultaneously-requested tool calls concurrently via
`asyncio.gather` (unchanged from the original implementation).

## 6. Streaming vs. the outbound guardrail — a deliberate trade-off

The previous 2-node graph (`chat` → `tool_call` → `chat`) could safely
forward raw provider tokens to the client via
`graph.astream(..., stream_mode="messages")`, because nothing downstream of
`chat` could veto its output.

That's no longer true. Two things break naive token forwarding once
guardrails, review, and hints are in the loop:

1. **Every internal LLM call would leak onto the stream.** `stream_mode="messages"`
   surfaces `AIMessage`/`AIMessageChunk` tokens from *any* chat-model call
   made inside *any* node during that graph run — not just `chat`'s. The
   inbound intent judge, the outbound judge, the security/performance
   reviewers, and the hint generator all make their own structured-output
   LLM calls. Forwarding raw token chunks would mean the user sees judge
   JSON fragments and review-model output interleaved with their actual
   reply.
2. **The outbound guardrail can't veto what's already been shown.** If
   `chat`'s tokens stream to the client as they're generated, the outbound
   check — which only runs *after* `chat` finishes — is evaluating content
   the user has already seen in full. A full-solution leak would already be
   on screen by the time `outbound` decides to block it.

Given this is a mentoring product whose core constraint is "never leak a
complete solution," safety has to win over raw-token latency. `get_stream_response`
now runs the graph to completion with `ainvoke` (not `astream`), reads the
single, already-vetted `final_response` the graph produced, and re-chunks
*that* into incremental pieces for the client. The client still receives the
response as a sequence of incremental pieces over the wire — satisfying
"streams token-by-token" from the outside — but nothing is ever shown before
both guardrail stages and the outbound check have all had a chance to veto
it.

**Trade-off being made explicitly, not hidden:** this costs the
time-to-first-token latency benefit of true provider-level streaming (the
full response is generated before the client sees anything). Given the
product's own stated priority ("never hand over the finished solution" is
the one constraint every other design choice protects), this is the right
default for today. If first-token latency becomes a measured problem later,
the fix is to stream `chat`'s tokens live but hold outbound's *replacement*
in reserve, only substituting if it blocks — a UX-visible "message being
corrected" flash. Not implemented today; flagging as a known follow-up.

## 7. Other things resolved while wiring this together

- **`user_query` and `problem_id` are new state fields**, populated once at
  graph invocation (`_build_graph_input`), not derived redundantly inside
  each node. `user_query` is the latest human message's text (several nodes
  — DLP, intent, hints — already expected this key). `problem_id` is
  `sha256(code)[:16]` when code is present, else `None`; it's what
  `generate_hint_node` already uses to detect "this is a different code
  submission than last turn" and reset hint escalation back to `NUDGE`
  — this was already built into `hints.py`, it just needed a real value fed
  in.
- **DLP-blocked turns never reach long-term memory.** `memory_service.add()`
  (mem0) is skipped whenever `is_safe_sensitive is False` for that turn —
  the raw-credential short-circuit's entire purpose is defeated if the
  redacted-but-still-recently-real secret becomes a searchable long-term
  memory. (LangGraph's own Postgres checkpointer still records the turn —
  removing that too would need a bigger change than today's scope; see the
  open item below.)
- **`hints` no-ops on turns with no code and no findings**, instead of
  generating a hint about nothing on every plain question. Matches the
  original design principle directly: "hints run after findings exist."

## 8. Known open items (raised by the team, not solved today — intentionally out of scope)

- **Findings accumulate across code submissions within one session**
  (`operator.add` never resets). Flagged by Haitham: if a user submits a
  second, different piece of code, old findings from the first submission
  never leave `state["findings"]`, and there's no way yet to tell which
  finding belongs to which submission. `problem_id` (added in this rebuild)
  is the natural scoping key for a future fix, but changing `findings` from
  append-only to submission-scoped isn't done here — the Week 2 requirement
  explicitly asks for append-not-overwrite for concurrency safety, and
  changing the accumulation model is a separate design decision for the
  team meeting, not a unilateral change to make hours before a deadline.
  **Confirmed live, 23 Jul:** reproduced end-to-end against a real running
  agent (Postgres + compiled graph + live LLM) — after reviewing one
  problem, submitting a second, different problem still returned findings
  and stale hint-fallback content mixed in from the first. This is the same
  root cause surfacing in two places at once: neither `findings` nor
  `hint_state`'s delivered content is scoped to `problem_id` changing mid-session
  (hint *level* escalation is correctly scoped via `problem_id` — see
  `generate_hint_node`'s reset check — but the findings a hint is generated
  from are not). Not fixed tonight for the same reason given above: it's a
  reducer/state-shape change, not a call-site fix, and shouldn't be made
  hours before a deadline without the team's sign-off. Recording this here
  as empirical confirmation of an already-known gap, not a new finding.
- **Review-as-node vs. review-as-tool** is still an open question per
  Haitham/Ahmed's discussion — this rebuild keeps review as three narrow
  nodes (matches the existing implementations and the Week 2 task text
  verbatim: "parallel review nodes for correctness, security, and
  performance-style").
- **Whether DLP-blocked turns should also skip the Postgres checkpoint**,
  not just mem0. Currently the checkpointer still records the turn (with
  the secret already redacted to `sanitized_query`/`sanitized_code` before
  it's written, since DLP runs first) — but the very first checkpoint at
  invocation time briefly includes the *raw* `code`/`messages` input before
  `inbound_dlp` has run. Fully eliminating that would mean running DLP
  scanning before the graph is even invoked, outside LangGraph's
  checkpointing entirely — a bigger structural change than today's scope.
  Flagging for the team, not fixing silently.

## 9. Files changed in this rebuild

| File | Change |
|---|---|
| `app/schemas/graph.py` | `GraphState`: Pydantic `BaseModel` → `TypedDict`; added `user_query`, `problem_id`, and every guardrail/hint/outbound field the existing node code already expected |
| `app/core/langgraph/graph.py` | Full rebuild: 2-node graph → 9-node graph wiring in inbound DLP/intent, parallel review, hints, outbound; `_chat` restructured to route through `outbound` instead of terminating; streaming rewritten (see §6); `user_query`/`problem_id` computed at invocation |
| `app/core/langgraph/nodes/correctness.py` | Bug fix: now reads submitted code instead of the last chat message |
| `app/core/langgraph/nodes/performance_node.py` | Added `performance_review_node(state)` graph-node wrapper around the existing `run_performance_review` |
| `app/core/langgraph/nodes/inbound_first_stage.py`, `inbound_intent.py`, `outbound.py`, `security_review.py` | **Unchanged** — wired in as-is; the state contract was designed to match what they already expected |
| `app/core/langgraph/nodes/hints.py` | Bug fix (23 Jul, found during live testing): `invoke_structured_llm_with_retry` built its own `ChatOpenAI` directly from `settings.HINT_LLM_MODEL`, sending that value straight through as the API `model=` field. `HINT_LLM_MODEL` defaults to `"fw-kimi-k2.6"`, which is only a registry *lookup key* (see §5/§11's note on `fw-kimi-k2.6`'s name-vs-model split) — not a real model id any provider recognizes — so every dedicated hint call was silently failing and falling back to the canned "system disruption" `MentorResponse`. Fixed by routing through `LLMRegistry.get(settings.HINT_LLM_MODEL)` instead, like every other LLM call in this app. This bug would also have failed against the real production LiteLLM proxy, not just tonight's local Cerebras workaround — `"fw-kimi-k2.6"` was never a real model id anywhere. Verified: 0 pyright errors; live logs show a successful call and genuine (non-fallback) hint content post-fix. |
| `tests/test_graph_state.py` | Rewritten — the old tests asserted Pydantic-style attribute access/defaults that no longer exist on a `TypedDict`; now exercises dict access, `total=False` (no auto-defaults), and the `findings` reducer's append-not-overwrite behavior |
| `tests/test_correctness_node.py` | Rewritten — the old tests fed code through a chat message (matching the pre-fix bug); now exercises `code`/`sanitized_code` directly, including the sanitized-over-raw precedence and the no-code-submitted case |

## 10. Static type-checking pass (pyright)

VS Code's Problems panel surfaced 29 real Pylance/pyright errors after the
rebuild — 23 in `graph.py` (plus the schemas/node files it calls into) and 6
in the then-stale `test_graph_state.py`. All 29 are resolved; `pyright` now
reports zero errors on every file this rebuild touches. Two categories of
fix, kept deliberately separate:

- **Real bugs, fixed properly** (no casts): the `inbound_trigger_reason`
  lookup in `_guardrail_redirect` now narrows `None` before indexing the
  template dict; `_tool_call` narrows the last message with
  `isinstance(last_message, AIMessage)` before reading `.tool_calls` instead
  of assuming every message type has it; `_execute_tool` is typed against
  LangChain's actual `ToolCall` shape instead of a bare `dict`; and both
  `_chat` and `_tool_call` read `state.get("messages")` instead of
  `state["messages"]`, since `messages` is optional on a `total=False`
  `GraphState`.
- **Pre-existing type-shape looseness, cast rather than rewritten**: five
  node functions (`inbound_dlp_node`, `inbound_intent_node`,
  `security_review_node`, `performance_review_node`, `generate_hint_node`)
  and `outbound_node` predate this rebuild and are typed against
  `dict[str, Any]`. A `TypedDict(total=False)` *is* a plain `dict` at
  runtime, so calling them with `GraphState` is behaviorally identical to
  calling them with a dict — the mismatch is static-only. Rather than
  changing five teammates' function signatures (and cascading into
  `test_hints_scaffold.py`/`test_inbound_nodes.py`'s local type
  annotations) hours before a deadline, these call sites use a narrow,
  commented `cast(dict[str, Any], state)` / `cast(Any, node_fn)`. Same
  reasoning applies to `state["messages"]` passed into `prepare_messages`
  (`app/utils/graph.py`, unchanged) — that utility is typed against the
  API's `Message` schema while the graph's checkpointed state holds
  LangChain `BaseMessage` objects; both are pydantic models whose dumped
  shape LangChain's own message conversion already accepts, so this is a
  pre-existing, type-only mismatch, not a behavior change introduced here.
  Flagging for whoever owns `app/utils/graph.py` next, not fixing silently.
- **Out of scope, left alone**: `python3 -m pyright app tests` (not just the
  files this task touches) also surfaces pre-existing errors in
  `app/services/memory.py` (an `AsyncMemory`/mem0 typing issue) and a few
  `pytest.raises(ValidationError)` tests that intentionally pass an invalid
  literal to prove Pydantic validation rejects it. Neither is part of this
  rebuild's node/graph/state ownership; noted here rather than touched.

Verification commands (run from the repo root, `uv run` prefix optional if
your venv is already active):

```bash
uv run pyright app/core/langgraph/graph.py app/schemas/graph.py
uv run pytest tests/test_graph_state.py tests/test_correctness_node.py -v
uv run ruff check app/core/langgraph/graph.py tests/test_graph_state.py tests/test_correctness_node.py
```

## 11. Known risk: no fallback survives a LiteLLM outage (flagged 22 Jul, not fixed today)

The team was notified on 22 Jul that the shared LiteLLM proxy
(`settings.LITELLM_BASE_URL`, currently
`https://learner-os.sprints.ai/litellm`) may go down soon, and that the team
should evaluate alternatives (e.g. OpenRouter) over the next two weeks.

This matters for the model registry described in §5: every entry in
`LLMRegistry.LLMS` — currently `fw-kimi-k2.6` and `kimi-k2.6` — is a
`ChatOpenAI` instance pointed at that same `_BASE_URL`/`_API_KEY`. The
circular fallback in `_switch_to_next_model`/`_fallback_loop` protects
against one *model* misbehaving (rate limits, timeouts, a bad response) —
it does not protect against the *proxy itself* being unreachable, because
every fallback attempt routes through the identical connection. If LiteLLM
goes down, all registry entries fail together, at once, and
`_call_with_fallback` exhausts every "fallback" without ever reaching a
model that isn't also behind the same dead proxy.

The same is true of the guardrail judge calls (`inbound_intent_node`,
`outbound_node`) — `settings.EVALUATION_BASE_URL`/`EVALUATION_API_KEY`
default to the same LiteLLM values (`app/core/config.py`), so a LiteLLM
outage takes down guardrail evaluation too. Because both guardrails fail
closed by design (§ inbound/outbound guardrail architecture — an evaluator
error blocks the turn rather than allowing it through), a full LiteLLM
outage would make the whole assistant unavailable rather than insecure —
the failure mode is "nothing responds," not "unreviewed content leaks
through." That's the safer of two bad outcomes, but it's still full
unavailability, not resilience.

**What was done today (22 Jul):** the registry's `fw-kimi-k2.6` entry's
underlying `model=` was repointed to `kimi-k2.5` (confirmed working under
the current LiteLLM access grant), and a redundant third entry that
duplicated that same model under the name `kimi-k2.5` was removed, since it
added a retry slot without adding a distinct model — see §5 and §10. This
keeps today's 2-model fallback honest; it does not add resilience against
a LiteLLM outage itself.

**What this rebuild deliberately did not do:** add a second provider (e.g.
OpenRouter) as a true failover path. That requires a team decision on which
provider, whose API key, and how it's funded/rate-limited — not something
to wire in unilaterally hours before a deadline. Flagging it here so it's
visible in the deliverable rather than discovered later.
