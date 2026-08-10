

## 1. What this branch is about

From a base ReAct agent, this branch turned the app into a **production-style AI
coding mentor**: two LangGraph chat agents (Reasoning + Fast), a layered
LLM-judge guardrail pipeline, a standalone **MCP server that I refactored and
integrated** (not written from scratch), a full Angular frontend, and a smoke
test suite.

## 2. My commits in this range

| Hash                 | Message                                  | What it does                                                        |
| -------------------- | ---------------------------------------- | ------------------------------------------------------------------- |
| `e7bc686`            | Create Frontend                          | Angular chat app built from scratch (auth, chat, composer, sidebar) |
| `7236192`            | enhance inbound intent judge             | LLM intent judge classifies + routes requests                       |
| `31af76f`            | enhance outbound guardrail               | Output judge with a bounded **regenerate loop**                     |
| `0c435b2`, `662e8ff` | secret detection / redaction             | PII + secret scrubbing across components                            |
| `3bfca76`            | streaming + session naming               | SSE `/chat/stream`, auto session naming                             |
| `f887684`            | conversational guardrails + msg deletion | Guardrails aware of conversation context                            |
| `f21f443`            | Integrate MCP                            | Wired the MCP server into the ReAct agent                           |
| `29a1f9e`            | Tavily search tool                       | `web_search` tool + `langchain-tavily` dep                          |
| `3f54842`, `f4a7f35` | Refactor MCP server + tools              | Guardrail-wrapped tool registry, scope forcing, reconnect           |
| `940b177`            | Merge branch                             | Sync with remote                                                    |
| `8e8f8db`            | agent mode selection                     | **Reasoning / Fast** toggle end-to-end (API + UI)                   |

*Collaboration note:* initial MCP scaffolding (`2908d07`), the graph.py refactor
(`1edb61c`) and first streamlit guardrails (`c40c072`, `ab53ac6`, `9850ad9`) were
started by Youssef Wael. I took the MCP server, **refactored and hardened it and
integrated it into the system**, and built the guardrail judges, frontend, and
agent-mode selection on top.

## 3. Two chat agents (shared state, shared guardrails)

Both use `StateGraph(GraphState)`, the same `AsyncPostgresSaver` checkpointer
(`thread_id = session_id`), mem0 long-term memory, and the skill profile.

### 3.1 Reasoning agent (ReAct) — `ReAct_agent_graph.py`

Full node-by-node flow (retries via tenacity, LLM calls traced to Langfuse):

```
                        ENTRY: /chat, /chat/stream (multipart: message + files + mode)
                                           |
                                           v
 +---------------------------------------------------------------------------------+
 |  [1] secret_guardrail  (deterministic, NO LLM)                                   |
 |      regex redaction: aws_access_key, openai/anthropic keys, github/slack tokens,|
 |      jwt, private-key blocks, db conn strings, password= assignments            |
 |      -> REDACTS secrets in-place and continues (never blocks)                    |
 +----------------------------------------+-----------------------------------------+
                                          v
 +---------------------------------------------------------------------------------+
 |  [2] inbound_intent  (LLM judge, structured output, retry 2x, timeout 60s)       |
 |      input: user query + redacted uploaded code (<=15k chars/file)              |
 |      decision: is_safe_intent + inbound_trigger_reason                          |
 +-------------------+--------------------------+----------------------------------+
        safe intent  |   blocked:                |  judge error / timeout
                     |   SOLUTION_EXTRACTION,    |  (fail-closed)
                     |   OFF_TOPIC,              |
                     |   HARMFUL_ILLEGAL         |
                     v                          v
 +--------------------------------+  +---------------------------+
 | [3] document_pipeline          |  | [6] store_messages         |
 |  for each pending file:        |  |  (redirect reply stored    |
 |   read_file(stored_path)       |  |   via AIMessage in state)  |
 |   CodeSplitter: chunk_lines=50,|  +------------+--------------+
 |     overlap=10, max_chars=1500 |               |
 |   build CodeChunk rows (user_id|               |
 |     /session_id/file_id/file_  |               |
 |     name/language/content)     |               |
 |   vector_store.store_chunks    |               |
 |   -> pgvector                  |               |
 |  no files -> skip straight on  |               |
 +---------------+----------------+               |
                 v                                |
 +--------------------------------------------+   |
 | [4] agent  (ReAct sub-agent: langchain create_agent)   |
 |     tools (MCP_AGENT_TOOLS_ENABLED):        |   |
 |        MCP server tools + ask_human         |   |
 |     else: duckduckgo_search_tool +          |   |
 |        search_code + ask_human              |   |
 |     internal loop: LLM -> tool_call ->      |   |
 |        ToolMessage -> LLM ... until final   |   |
 |     search_code returns student chunks      |   |
 |        scoped by session/user (contextvars) |   |
 +---------------------+----------------------+   |
                       v                           |
 +--------------------------------------------+   |
 | [5] outbound  (LLM output judge, retry 2x, timeout 10s)  |
 |     input: user query + search_code ToolMessage   |
 |        results (<=15k chars) + draft answer      |
 |     decision: is_safe_output                      |
 +----------+-----------------------------+---------+
    safe    |           unsafe & attempts < 4 (regenerate)
            v                              |
   +--------+--------+                     |
   | [6] store_messages |                  |
   |  persist user + approved final msg    |
   |  -> messages table (async task)       |
   |  -> mem0 memory.add (async)           |
   |  -> skill_profile.update (async)      |
   +--------+--------+                     |
            |                              +---> back to [4] agent with
            v                                   REGENERATE_INSTRUCTION system msg
 +------------------------------------+
 | [7] summarization                  |
 |  tiktoken count; if > MAX_TOKENS:  |
 |   summarize oldest 2/3 messages,   |
 |   RemoveMessage + append summary   |
 +------------------+-----------------+
                    v
                   END
```

Notes:
- The ReAct node is a **tool-loop sub-agent** (`langchain.agents.create_agent`).
  When `MCP_AGENT_TOOLS_ENABLED=true` its tools are the **MCP server tools +
  `ask_human`**; otherwise local tools (`duckduckgo_search_tool`, `search_code`,
  `ask_human`).
- `outbound` bounces unsafe drafts back to the agent up to **4 attempts**
  (`MAX_OUTBOUND_ATTEMPTS`), then fails closed with a safe redirect.
- `store_messages` persists **only** the sanitized user turn + the final
  approved assistant response (never a rejected draft).

### 3.2 Fast agent (progressive hints) — `graph.py`

Full node-by-node flow (shares the guardrail stages with the ReAct graph):

```
                            ENTRY: /chat, /chat/stream (message + files + mode="fast")
                                              |
                                              v
 +--------------------------------------------+-------------------------------+
 | [1] secret_guardrail  (same deterministic redaction as ReAct agent)          |
 +--------------------------------------------+-------------------------------+
                                              v
 +--------------------------------------------+-------------------------------+
 | [2] inbound_intent  (LLM judge, retry 2x, timeout 60s)                       |
 +---------------------+------------------------------+------------------------+
      safe intent      | blocked / judge error        |
                       v                              v
 +-------------------------------------+  +-------------------------+
 | [3] document_pipeline               |  | store_messages          |
 |  CodeSplitter(50/10/1500) ->        |  |  (redirect reply stored)|
 |  CodeChunk -> pgvector              |  +-----------+------------+
 |  (wrapper rewrites goto:            |              |
 |   context_retrieval, not "agent")   |              |
 +------------------+------------------+              |
                    v                                 |
 +--------------------------------------------+       |
 | [4] context_retrieval  (concurrent gather) |       |
 |  - vector_store.search(query, session/user,|       |
 |      top_k=15) -> code_context             |       |
 |  - memory_service.search(user_id)          |       |
 |  - skill_profile.render_for_prompt_async() |       |
 |  -> all three written into state           |       |
 +------------------+-------------------------+       |
                    v                                 |
 +--------------------------------------------+       |
 | [5] generate_hint                          |       |
 |  ChatOpenAI(HINT_LLM_MODEL via LiteLLM),   |       |
 |  progressive-hint system prompt, retry 3x  |       |
 |  -> draft_response in state                |       |
 +------------------+-------------------------+       |
                    v                                 |
 +--------------------------------------------+       |
 | [6] outbound wrapper  (check_outbound_     |       |
 |      guardrails(draft, query, code_context))|      |
 |      retry 2x, fail-closed                 |       |
 +---------+----------------------------+-----+       |
    safe   |        unsafe & attempts < 4 (regenerate)|
           v                                 |        |
   +-------+---------+                       |        |
   | [7] store_messages  |                   |        |
   |  messages + mem0 + skill profile        |        |
   +-------+---------+                       |        |
           |                                 |        |
           v                                 +------> back to [5] generate_hint
 +--------------------------------------------+       |  (bounded by outbound_attempts)
 | [8] summarization                         |       |
 |  tiktoken budget check; summarize older   |       |
 |  2/3 messages when over MAX_TOKENS        |       |
 +------------------+-------------------------+       |
                    v                                 |
                   END                                |
```

- The fast agent has **no tool loop** — it is a linear pipeline: vector
  retrieval → one-shot hint generation → judged delivery. It does **not** use
  MCP tools.
- `outbound` regenerates the hint up to **4 attempts** by looping back to
  `generate_hint` (not the agent node).

## 4. MCP — refactored & integrated, not built from scratch

```
ReAct agent (app)
   │ MCPToolConnection — one long-lived stdio subprocess, spawned once at startup
   ▼
mcp_server.server (FastMCP, stdio transport)
   │ register_all_tools() → tool_registry.py
   │   · per-tool GuardrailPolicy sidecar (keyed by tool name)
   │   · dynamic async wrapper: schema → inspect.Signature → FastMCP JSON schema
   │   · sync tools run in a thread; errors returned as JSON, never crash
   ▼
Tools:  web_search (Tavily) · search_code (pgvector) · memory_search (mem0) · server_status
```

Key work I did here:
- **Tool registry** (`mcp_server/tool_registry.py`) — guardrail-wrapped dynamic registration; adding a tool to `TOOLS` auto-registers it and it appears in the Reasoning agent automatically.
- **Scope forcing** (`app/core/langgraph/mcp_tool_connection.py`) — subprocess spawned unbound (`MCP_USER_ID=""`); an interceptor injects `user_id`/`session_id` from backend contextvars; agent-visible schemas are scrubbed of scope args so the model never chooses them.
- **Resilience** — dead-session detection with one automatic reconnect + MCP subprocess restart; persistent connection instead of a throwaway subprocess per call.
- **Guardrails** (`mcp_server/guardrails.py`) — injection/PII/rate-limit/audit-log for the MCP tools; RATE_LIMITS per tool.

## 5. Guardrail pipeline (both agents)

- **Inbound stage 1** — secret/PII redaction before anything else sees the input.
- **Inbound stage 2** — LLM intent judge classifies (safe / solution-extraction / off-topic / harmful / judge-error) and routes: safe → document pipeline, everything else → redirect reply. **Fail-closed**: judge timeouts/errors never reach the agent.
- **Outbound** — LLM judge checks the draft against the query + retrieved code; full-solution leaks trigger regeneration (max 4); judge errors return a safe timeout reply.

## 6. Angular frontend (built from scratch)

`Frontend/` — auth page, chat page with header (stream toggle), sidebar (sessions), message list + bubbles (markdown/highlighting), composer (file attach, send/stop, **agent-mode pill**), Angular Material, auth interceptor, chat + stream services (SSE parsing).

## 7. Agent mode selection (latest feature)

`mode` form field (`reasoning` | `fast`) on `/chat` + `/chat/stream` (`app/api/v1/chatbot.py`); `get_agent(mode)` singleton factory; both agents pre-warmed at startup, MCP lifecycle only for the ReAct agent. Frontend: Gemini-style **Reasoning / Fast** pill in the composer, persisted in `localStorage`, sent with every request.

## 8. Other backend work

- SSE streaming endpoint + metrics (`llm_stream_duration_seconds`)
- Auto session naming (`SESSION_NAMING_ENABLED`)
- mem0 memory search + skill-profile rendering injected into the system prompt
- Tavily web search tool and `code_search` tool

## 9. Latest session work (uncommitted, still on this branch)

- **Similarity threshold** (`CHUNK_SIMILARITY_THRESHOLD`, default 0.75) wired into both vector stores (`app/` + `mcp_server/`)
- **Upload hardening** — `MAX_FILES_PER_REQUEST`, null-byte sniffing
- **Intent-judge timeout fix** — 10 s → 60 s (root cause of lost uploads)
- **Smoke tests** — `tests/smoke/` (health, auth, chat, upload-with-metadata assertions) + `make smoke`/`smoke-upload`
- **Docker file fixes** — `.dockerignore`, compose env/healthcheck, entrypoint migrations (files-only audit)

## 10. Quality gates

- `make check` (ruff + pyright) — **passing**.
- Smoke auth: **9/10 pass**; `test_invalid_token_returns_401` expects 401 but app returns 422 for malformed tokens (fix pending).
- Smoke chat: needs rewrite to the current multipart API (written for an older JSON contract).
- Smoke upload: `code_chunk` metadata contract asserted per chunk; E2E re-run pending.
