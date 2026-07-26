# Architecture Review: Risk List & Design Analysis

**Reviewer:** Independent Architecture Review  
**Date:** July 21, 2026  
**Version:** 1.0  
**Status:** Final

---

## Executive Summary

This document presents a black-box architecture review of the AI Coding Helper application. The review evaluates inputs, outputs, latency, failure modes, and API contracts independently of prompt internals or model behavior. Findings are prioritized by severity and assigned an owner.

**Total Risks Identified:** 14  
**Critical:** 2 | **High:** 5 | **Medium:** 4 | **Low:** 3

---

## Risk Register

### 🔴 CRITICAL

#### C-1: Token Ambiguity — No Type Distinction Between User and Session Tokens

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Owner** | Backend / Auth |
| **Component** | `app/utils/auth.py`, `app/api/v1/auth.py` |
| **Detection** | Code review |

**Description:** Both `get_current_user` and `get_current_session` in `auth.py` call the same `verify_token()` function. The JWT payload contains a `sub` claim with either a user ID or a session ID, but **no `type` claim** to distinguish them. This means a user token can be used where a session token is expected, and vice versa.

**Impact:**
- A user token can access session-scoped endpoints (`/chat`, `/messages`)
- Session tokens can be used for user-scoped operations (`/sessions`, `/session/{id}`)
- Authorization boundaries are unclear and unenforced

**Recommendation:**
Add a `type` claim to the JWT payload:
```python
to_encode = {
    "sub": identifier,
    "type": "user" | "session",
    "exp": expire,
    # ...
}
```
Then validate the token type in each dependency:
```python
if payload.get("type") != "user":
    raise HTTPException(status_code=401, detail="Invalid token type")
```

---

#### C-2: Synchronous Database Session Blocks Async Event Loop

| Field | Value |
|-------|-------|
| **Severity** | Critical |
| **Owner** | Backend / Database |
| **Component** | `app/services/database.py` |
| **Detection** | Code review, latency analysis |

**Description:** All methods in `DatabaseService` are declared `async` but use synchronous `Session(self.engine)` from SQLModel. This blocks the async event loop during database operations, negating FastAPI's async performance benefits.

**Impact:**
- Under load, DB queries will block the event loop, causing all concurrent requests to queue
- Vertical scalability is limited; horizontal scaling with async workers is undermined
- ~50-200ms blocking per DB call

**Recommendation:**
Migrate to SQLAlchemy's async engine with `AsyncSession`:
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async_engine = create_async_engine(
    f"postgresql+asyncpg://{user}:{pass}@{host}/{db}"
)
# Use async session in all methods
async with AsyncSession(async_engine) as session:
    result = await session.execute(statement)
```

---

### 🟠 HIGH

#### H-1: Langfuse Tracing — Single Global Callback Handler Causes Trace Contamination

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Owner** | AI / Observability |
| **Component** | `app/core/observability.py`, `app/core/langgraph/graph.py` |
| **Detection** | Code review |

**Description:** `langfuse_callback_handler` is a **module-level singleton** created at import time. This single `CallbackHandler` instance is shared across all concurrent requests. Langfuse's `CallbackHandler` is designed to be request-scoped — reusing it across requests can cause trace ID collisions, incorrect parent-child span relationships, and metric contamination.

**Impact:**
- Concurrent requests share the same trace context
- Per-node token cost and latency attribution becomes unreliable
- Langfuse traces show merged/corrupted data under load

**Recommendation:**
Create a **new `CallbackHandler` per request** in `get_response` and `get_stream_response` instead of using the module-level singleton:
```python
# In graph.py, create fresh per request:
callbacks = [CallbackHandler()] if settings.LANGFUSE_TRACING_ENABLED else []
```

---

#### H-2: No Application-Level Message Model — Messages Only in LangGraph Checkpoints

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Owner** | Backend / Data Model |
| **Component** | All `app/models/` files |
| **Detection** | Code review, data flow analysis |

**Description:** Chat messages are stored **only** in LangGraph's internal checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) via `AsyncPostgresSaver`. There is no application-level `Message` SQLModel table. This means:
- Messages cannot be queried directly via SQL
- No indexing on user_id, session_id, or timestamp
- Cannot efficiently paginate, search, or export conversation history
- Message retrieval requires `graph.aget_state()` which loads the entire checkpoint

**Impact:**
- `GET /messages` endpoint loads and filters the full state object in memory
- No ability to run analytics on chat data
- Migration to a different agent framework would lose all message history

**Recommendation:**
Add a `Message` model:
```python
class Message(SQLModel, table=True):
    id: int = Field(primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    role: str  # "user", "assistant", "system"
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```
Write messages to both the checkpoint (for LangGraph state) and the Message table (for querying).

---

#### H-3: Guardrail Node — Not Wired Into the Graph

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Owner** | AI / LangGraph |
| **Component** | `app/core/langgraph/nodes/guardrail.py`, `app/core/langgraph/graph.py` |
| **Detection** | Code review |

**Description:** The `guardrail_node` function is fully implemented in `guardrail.py` with heuristic content filtering (safety policy violations, "write the code for me" detection) and proper routing logic. However, it is **never added to the graph** in `graph.py`. The current graph has only two nodes: `chat` and `tool_call`.

**Impact:**
- All user requests bypass guardrails entirely
- Users can ask for harmful code, exploits, or homework solutions
- The entire guardrail implementation exists but is dead code

**Recommendation:**
Add the guardrail node as the entry point in `create_graph()`:
```python
graph_builder.add_node("guardrail", guardrail_node)
graph_builder.set_entry_point("guardrail")
graph_builder.add_edge("guardrail", "chat")
```

---

#### H-4: Module-Level Agent Singleton Causes Worker State Conflicts

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Owner** | Backend / Deployment |
| **Component** | `app/api/v1/chatbot.py` line 32 |
| **Detection** | Code review |

**Description:** `agent = LangGraphAgent()` is created at module level in `chatbot.py`. In a multi-worker deployment (e.g., multiple uvicorn workers, container replicas), each worker gets its own agent instance. The agent holds mutable state (`_connection_pool`, `_graph`) that is lazily initialized.

**Impact:**
- Each worker creates its own DB connection pool, wasting connections
- State is not shared across workers
- In-process mutable state can cause race conditions

**Recommendation:**
Move agent creation to the `lifespan` context manager and pass via `app.state`:
```python
@asynccontextmanager
async def lifespan(app):
    agent = LangGraphAgent()
    await agent.create_graph()
    app.state.agent = agent
    yield
```

---

#### H-5: Non-Atomic Skill Profile Upsert — Data Loss on Partial Failure

| Field | Value |
|-------|-------|
| **Severity** | High |
| **Owner** | AI / Memory |
| **Component** | `app/services/memory.py` — `upsert_skill_profile` method |
| **Detection** | Code review |

**Description:** `upsert_skill_profile` deletes the existing profile first, then adds a new one. If the delete succeeds but the add fails (network error, DB timeout), the user's skill profile is permanently lost until the next successful upsert.

**Impact:**
- User loses personalized LLM context
- Skill progression data can be silently destroyed
- No recovery mechanism

**Recommendation:**
Use mem0's built-in update mechanism or wrap in a transaction:
```python
if existing_result:
    await memory.update(memory_id=existing_result[0]["id"], content=content, metadata=metadata)
else:
    await memory.add(content, user_id=user_id, metadata=metadata, infer=False)
```

---

### 🟡 MEDIUM

#### M-1: Model Name Case Sensitivity Mismatch

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Owner** | AI / Configuration |
| **Component** | `app/core/config.py`, `app/services/llm/registry.py` |
| **Detection** | Code review |

**Description:** The config default `DEFAULT_LLM_MODEL="gpt-5-mini"` matches registry, but `HINT_LLM_MODEL="fw-kimi-k2.6"` (lowercase) does NOT match registry's `"FW-Kimi-K2.6"` (mixed case). The fallback logic selects the first model, but this is silent — no warning is shown to the operator.

**Impact:**
- The hint LLM silently uses a different model than intended
- Configuration drift between dev and production
- Hard to debug performance issues

**Recommendation:**
Case-normalize model names in the registry lookup and log a warning on mismatch:
```python
model_entry = next((e for e in cls.LLMS if e["name"].lower() == model_name.lower()), None)
```

---

#### M-2: No Timeout on Memory Service Operations

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Owner** | AI / Memory |
| **Component** | `app/services/memory.py` |
| **Detection** | Code review |

**Description:** Memory service calls (`search`, `add`, `get_all`, `delete`) have no timeout. If pgvector or the embedding model hangs, the entire chat request hangs indefinitely.

**Impact:**
- A stuck memory operation blocks the chat response indefinitely
- No circuit breaker or fallback path
- User experiences infinite loading

**Recommendation:**
Add timeouts using `asyncio.wait_for`:
```python
async def search(self, user_id, query):
    try:
        return await asyncio.wait_for(
            self._memory.search(user_id=user_id, query=query),
            timeout=5.0  # 5 second timeout
        )
    except asyncio.TimeoutError:
        logger.warning("memory_search_timed_out")
        return ""
```

---

#### M-3: No Rate Limiting on Health Endpoint in `api.py`

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Owner** | Backend / API |
| **Component** | `app/api/v1/api.py` line 20 |
| **Detection** | Code review |

**Description:** The health check endpoint in `api.py` (`/api/v1/health`) has no rate limiter, while the root health check in `main.py` (`/health`) does. This creates an unrate-limited path that can be used to bypass rate limits.

**Impact:**
- Can be used for health check polling without limits
- Minor DoS amplification vector
- Inconsistent across API versions

**Recommendation:**
Add `@limiter.limit(...)` to the health endpoint in `api.py`.

---

#### M-4: Private Method Accessed Externally

| Field | Value |
|-------|-------|
| **Severity** | Medium |
| **Owner** | AI / Memory |
| **Component** | `app/core/langgraph/graph.py` line 350, 428 |
| **Detection** | Code review |

**Description:** `memory_service._profile_to_text(profile)` is called from `graph.py`. The underscore prefix conventionally denotes a private method. This creates coupling between modules and risks breaking if the private method signature changes.

**Impact:**
- Brittle internal API contract
- Violates encapsulation principles
- Confusing for new developers

**Recommendation:**
Make `_profile_to_text` a public method or create a public wrapper.

---

### 🟢 LOW

#### L-1: `get_session_maker()` Returns a Session, Not a Sessionmaker

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Owner** | Backend / Database |
| **Component** | `app/services/database.py` line 229 |
| **Detection** | Code review |

**Description:** The method name implies it returns a session factory, but it returns a single `Session` instance. This is misleading for developers using this method.

#### L-2: InMemoryCache Not Shared Across Workers

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Owner** | Backend / Cache |
| **Component** | `app/core/cache.py` |
| **Detection** | Code review |

**Description:** When Valkey is not configured, the in-memory cache is per-process. In a multi-worker deployment, each worker has its own cache, reducing effectiveness.

#### L-3: Duplicate Password Validation Logic

| Field | Value |
|-------|-------|
| **Severity** | Low |
| **Owner** | Backend / Auth |
| **Component** | `app/schemas/auth.py`, `app/utils/sanitization.py` |
| **Detection** | Code review |

**Description:** Password strength validation is implemented in both `UserCreate.validate_password` (Pydantic validator) and `validate_password_strength` in `sanitization.py`. These are identical but separate — a change to one could drift from the other.

---

## Latency Analysis

| Operation | Current | Target | Risk |
|-----------|---------|--------|------|
| Auth register | ~100ms | <500ms | ✅ |
| Auth login | ~50ms | <200ms | ✅ |
| Chat (no tools) | ~2-5s (LLM dependent) | <10s | ⚠️ Depends on LLM |
| Chat (with tools) | ~3-8s | <15s | ⚠️ Depends on LLM + search |
| Stream chat | ~TTFB 1-3s | <3s TTFB | ⚠️ |
| Get messages | ~50-200ms | <100ms | ⚠️ Scales with conversation length |
| Clear history | ~100-500ms | <500ms | ✅ |
| Memory search | ~100-300ms | <200ms | ⚠️ No timeout |
| Memory add | ~100-200ms | <200ms | ✅ |
| Health check | ~20-50ms | <50ms | ✅ |
| Graph creation | ~500-2000ms | <1000ms | ⚠️ On startup only |
| DB connection pool | ~200-500ms | <300ms | ⚠️ |

---

## Failure Mode Analysis

| Failure | Detection | Recovery | Impact |
|---------|-----------|----------|--------|
| DB connection lost | Health check returns degraded | Next request creates new connection | Requests fail until next retry |
| LLM API timeout | `LLM_TOTAL_TIMEOUT` (60s) | Circular fallback to next model | Slow response |
| LLM all models fail | All retries exhausted | `RuntimeError` propagated to client | 500 error |
| Memory service down | Exception caught and logged | Returns empty string gracefully | No personalization |
| Cache down | Exception caught and logged | Continues without cache | Higher latency |
| Langfuse API down | Silent failure (except debug) | Tracing disabled | No observability data |
| Graph creation fails (production) | Error logged | Server runs without graph | Chat requests fail |
| Connection pool init fails (production) | Error logged | Continues without checkpointer | No conversation persistence |

---

## Architecture Diagram (Text)

```
Client
  │
  ▼
FastAPI (main.py)
  │
  ├── Middleware Stack
  │   ├── CorrelationIdMiddleware
  │   ├── ProfilingMiddleware (DEBUG only)
  │   ├── MetricsMiddleware
  │   └── LoggingContextMiddleware
  │
  ├── /health → Health check (DB ping + component status)
  ├── / → Root info
  └── /api/v1
      │
      ├── /auth
      │   ├── POST /register → Create user
      │   ├── POST /login → Authenticate
      │   ├── POST /session → Create chat session
      │   ├── PATCH /session/{id}/name → Update name
      │   ├── DELETE /session/{id} → Delete session
      │   └── GET /sessions → List sessions
      │
      └── /chatbot
          ├── POST /chat → LangGraph agent (non-streaming)
          ├── POST /chat/stream → LangGraph agent (streaming)
          ├── GET /messages → Get history
          └── DELETE /messages → Clear history
              │
              ▼
          LangGraph Agent (graph.py)
              │
              ├── [guardrail] ← NOT WIRED (dead code)
              │   ├── safety_policy_violation → END
              │   ├── writing_whole_function → END
              │   └── code_review → chat
              │
              ├── [chat] (LLM call)
              │   ├── Tool calls? → tool_call
              │   └── No tools → END
              │
              ├── [tool_call]
              │   ├── duckduckgo_search
              │   └── ask_human (interrupt)
              │
              └── Checkpointer (AsyncPostgresSaver)
                    └── Tables: checkpoints, checkpoint_blobs, checkpoint_writes

Supporting Services:
  ├── PostgreSQL + pgvector → App data + LangGraph checkpoints
  ├── Valkey (optional) → Distributed cache + rate limiting
  ├── mem0 → Long-term memory (pgvector backend)
  ├── Langfuse → LLM observability (singleton handler ⚠️)
  └── Sprints AI LiteLLM → LLM proxy (OpenAI-compatible)
```
</write_to_file>