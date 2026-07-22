# Architecture Design Review — AI Coding Helper

**Reviewer:** Engineering  
**Date:** July 22, 2026  
**Version:** 1.0.0  
**Scope:** Black-box architecture audit of core system design, observability, and test infrastructure

---

## 1. Written Risk List

### RISK-1: Database Table Auto-Creation in Production [CRITICAL]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Critical |
| **Owner** | Backend |
| **File** | `app/services/database.py` line 57-61 |
| **Description** | `SQLModel.metadata.create_all()` runs on every startup. In production, this risks schema drift — if a migration removes a column, `create_all` will not drop it. If a migration adds a column with a non-nullable default, the auto-create may succeed while the real migration fails. |
| **Recommendation** | Guard table creation behind a `if settings.ENVIRONMENT != Environment.PRODUCTION` check. Use Alembic migrations as the sole schema management path for production. |
| **Detection** | This was caught during smoke test debugging — missing tables caused 500 errors on all DB operations. |

### RISK-2: Singleton DatabaseService Coupling [HIGH]

| Attribute | Detail |
|-----------|--------|
| **Severity** | High |
| **Owner** | Backend |
| **File** | `app/api/v1/auth.py` line 48, `app/services/database.py` line 253 |
| **Description** | `db_service = DatabaseService()` is a module-level singleton. The engine is created at import time, not during the FastAPI lifespan. If the database is unreachable at startup, the module-level instantiation crashes the entire process before the lifespan handler even runs. |
| **Recommendation** | Convert to lazy initialization inside the lifespan of the FastAPI app, or use a dependency injection pattern that creates the service on first request. |
| **Detection** | Trace: `DatabaseService.__init__` runs at import time, before any request handling. |

### RISK-3: Sync DB Session in Async Context [HIGH]

| Attribute | Detail |
|-----------|--------|
| **Severity** | High |
| **Owner** | Backend |
| **File** | `app/services/database.py` — all methods use `with Session(self.engine)` (sync) inside `async def` |
| **Description** | All database operations use synchronous SQLModel `Session()` inside async FastAPI route handlers. This blocks the event loop for every DB call, negating the benefits of async I/O under concurrent load. |
| **Recommendation** | Use `AsyncSession` from `sqlalchemy.ext.asyncio` with `async with` for all database operations, or wrap synchronous calls in `asyncio.to_thread()`. |
| **Detection** | Review of `DatabaseService` methods shows all use sync `Session()` despite being declared `async def`. |

### RISK-4: LLM Registry Key Hard-Coding [HIGH]

| Attribute | Detail |
|-----------|--------|
| **Severity** | High |
| **Owner** | DevOps / Backend |
| **File** | `app/services/llm/registry.py` lines 19-20 |
| **Description** | All 3 LLM model entries use the same `_API_KEY` and `_BASE_URL` from `settings.LITELLM_API_KEY` / `settings.LITELLM_BASE_URL`. If this key is rotated or expires (August 17, 2026 per the credential), every model fails simultaneously with no graceful degradation. There is no key rotation mechanism. |
| **Recommendation** | Support multiple API keys per model tier. Implement key health checks and automatic fallback to a backup provider. Add key expiry monitoring. |
| **Detection** | Error: `"Authentication Error, No api key passed in"` when `LITELLM_API_KEY` was missing. |

### RISK-5: Langfuse Tracing Lacks Per-Node Attribution [MEDIUM]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Medium |
| **Owner** | Backend |
| **File** | `app/core/observability.py`, `app/core/langgraph/graph.py` |
| **Description** | Langfuse callback handler is passed to the entire LangGraph graph via `config["callbacks"]`. This creates a single trace for the whole request. Individual node costs (chat node vs tool_call node) cannot be distinguished in Langfuse — token count and latency are aggregated into one span. |
| **Recommendation** | Wrap each graph node (`_chat`, `_tool_call`) with its own `get_langfuse_callback_handler()` to create per-node spans. Alternatively, use Langfuse's `@observe()` decorator on each node method. |
| **Detection** | Code review: `langfuse_callback_handler` is passed once at graph invocation level, not per-node. |

### RISK-6: No Structured Error Response Format [MEDIUM]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Medium |
| **Owner** | Backend |
| **File** | `app/api/v1/auth.py` — multiple `except Exception` handlers |
| **Description** | Endpoints return generic `"Internal server error"` strings to clients on unexpected failures. There is no structured error response (no error code, no request_id, no trace_id) which makes debugging production incidents difficult. |
| **Recommendation** | Define a standardized error response schema with fields: `error_code`, `detail`, `request_id`, `type`. Use a global exception handler that enriches errors with correlation IDs. |
| **Detection** | Smoke tests reveal `500: {"detail":"Internal server error"}` — no structured error format. |

### RISK-7: Memory Service Initialization Blocks Startup [MEDIUM]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Medium |
| **Owner** | Backend |
| **File** | `app/main.py` lines 66-70 |
| **Description** | `memory_service.initialize()` runs during the lifespan startup, making the app unavailable until mem0/pgvector initializes. If the memory store is unreachable, the app logs a warning but continues — however, the startup delay still occurs. |
| **Recommendation** | Move memory service initialization to lazy first-use pattern. Add a readiness probe endpoint that reports memory service status separately from the liveness probe. |
| **Detection** | Code review: `await memory_service.initialize()` runs synchronously in startup sequence. |

### RISK-8: Session Naming Hardcodes Model [LOW]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Low |
| **Owner** | Backend |
| **File** | `app/services/session_naming.py` line 63 |
| **Description** | Session auto-naming uses a hardcoded `model_name="kimi-k2.5"` string instead of reading from settings. If the default model changes, session naming won't follow unless separately updated. |
| **Recommendation** | Use `settings.DEFAULT_LLM_MODEL` or a dedicated `settings.SESSION_NAMING_MODEL` setting. |
| **Detection** | Search for hardcoded model strings: `model_name="kimi-k2.5"` found in session_naming.py. |

### RISK-9: No Rate Limiting on Session Creation [LOW]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Low |
| **Owner** | Backend |
| **File** | `app/api/v1/auth.py` — `create_session` endpoint |
| **Description** | The `POST /auth/session` endpoint has no `@limiter.limit` decorator. An attacker with a valid token can create unlimited sessions, potentially exhausting database storage or connection pool. |
| **Recommendation** | Add a rate limit, e.g., `@limiter.limit("50 per hour")` on the session creation endpoint. |
| **Detection** | Code review: Only `/register`, `/login`, `/chat`, `/chat/stream`, `/messages`, `/`, `/health` have rate limits. Session creation is unguarded. |

### RISK-10: LLM Retries on Non-Retriable Errors [LOW]

| Attribute | Detail |
|-----------|--------|
| **Severity** | Low |
| **Owner** | Backend |
| **File** | `app/services/llm/service.py` lines 171-177 |
| **Description** | The `@retry` decorator retries on `APIError`, which includes 401 (auth) errors. If credentials are invalid, retrying is wasteful and delays the eventual failure. Retry should be limited to transient errors (rate limits, timeouts, 5xx). |
| **Recommendation** | Replace `APIError` with a more specific retry predicate that excludes 4xx authentication/authorization errors. Use tenacity's `retry_if_exception` with a custom checker function. |
| **Detection** | Smoke tests show 3 retries on 401 "No api key passed in" errors before failing. |

---

## 2. Langfuse Tracing Analysis

### Current State

| Feature | Status | Detail |
|---------|--------|--------|
| Langfuse initialization | ✅ Done | `app/core/observability.py` — `langfuse_init()` runs at app startup |
| Auth check | ✅ Done | `langfuse.auth_check()` validates credentials |
| LangChain callback | ✅ Done | `get_langfuse_callback_handler()` returns a `CallbackHandler` |
| Graph-level tracing | ✅ Done | Passed as `callbacks` in `config` to `graph.ainvoke()` |
| Per-node tracing | ❌ Missing | Single trace for the whole request — cannot distinguish chat vs tool_call |
| Token cost per node | ❌ Missing | Aggregated at graph level |
| Latency per node | ❌ Missing | No per-node breakdown |
| User/session metadata | ✅ Done | `user_id`, `session_id`, `environment` passed in config metadata |
| Streaming traces | ❌ Missing | Stream endpoint doesn't use `langfuse_callback_handler` during streaming |

### Enhancement: Per-Node Tracing

To achieve the required per-node attribution, the `_chat` and `_tool_call` nodes should each create their own Langfuse trace. Here is the recommended approach:

```python
# In app/core/langgraph/graph.py, _chat method:
from app.core.observability import get_langfuse_callback_handler

async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
    node_callbacks = [get_langfuse_callback_handler()]
    # ... existing logic ...
    with llm_inference_duration_seconds.labels(model=model_name).time():
        response_message = await self.llm_service.call(
            dump_messages(messages),
            callbacks=node_callbacks,  # <-- per-node callbacks
        )
```

### Configuration Reference

| Env Variable | Required | Default | Purpose |
|-------------|----------|---------|---------|
| `LANGFUSE_PUBLIC_KEY` | Yes | - | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Yes | - | Langfuse project secret key |
| `LANGFUSE_HOST` | No | `https://cloud.langfuse.com` | Langfuse server URL |
| `LANGFUSE_TRACING_ENABLED` | No | `true` | Master toggle for tracing |

---

## 3. Smoke Test Suite

### Current Test Inventory

| Test Name | Module | Type | Status | Coverage |
|-----------|--------|------|--------|----------|
| `test_register_user_returns_201` | auth | Registration | ✅ Passes | User creation, token response shape |
| `test_register_duplicate_email_returns_400` | auth | Registration | ✅ Passes | Duplicate email rejection |
| `test_register_weak_password_returns_422` | auth | Registration | ✅ Passes | Password strength validation |
| `test_register_invalid_email_returns_422` | auth | Registration | ✅ Passes | Email format validation |
| `test_login_valid_credentials_returns_token` | auth | Login | ✅ Passes | Successful login, token shape |
| `test_login_wrong_password_returns_401` | auth | Login | ✅ Passes | Wrong password rejection |
| `test_create_session_returns_session_token` | auth | Session | ✅ Passes | Session creation |
| `test_list_sessions_returns_user_sessions` | auth | Session | ✅ Passes | Session listing |
| `test_missing_token_returns_401` | auth | Guardrails | ✅ Passes | Missing auth header |
| `test_invalid_token_returns_401` | auth | Guardrails | ✅ Passes | Malformed token |
| `test_chat_basic_message` | chat | Chat | ✅ Passes | Send message, get AI response |
| `test_chat_with_code_review` | chat | Chat | ✅ Passes | Code context handling |
| `test_chat_with_empty_messages_returns_422` | chat | Chat | ✅ Passes | Empty payload validation |
| `test_chat_without_auth_returns_401` | chat | Guardrails | ✅ Passes | Unauthorized chat |
| `test_chat_stream_returns_events` | chat | Streaming | ✅ Passes | SSE streaming |
| `test_get_messages_returns_history` | chat | Persistence | ✅ Passes | History retrieval |
| `test_clear_messages_removes_history` | chat | Persistence | ✅ Passes | History clearing |
| `test_root_endpoint_returns_healthy` | health | Health | ✅ Passes | Root endpoint |
| `test_health_endpoint_returns_up` | health | Health | ✅ Passes | Health check |
| `test_api_v1_health_returns_ok` | health | Health | ✅ Passes | API health |

### Coverage Gaps

| Missing Test | Risk | Priority |
|-------------|------|----------|
| **Rate limit enforcement** — verifies that hitting rate limits returns 429 | Attack surface unguarded without verification | High |
| **Concurrent session creation** — validates multi-user isolation | No regression protection for session scoping | Medium |
| **LLM fallback on model failure** — verifies circular fallback works when primary model fails | No verification of the resilience mechanism | Medium |
| **Large message payload** — tests 8000-character message boundary | No boundary testing for schema validation | Low |
| **Session token expiry** — tests that expired tokens are rejected | JWT expiry not tested | Medium |
| **CORS headers** — validates CORS configuration | Deployment integration risk | Low |
| **Database connection failure recovery** — tests degraded mode behavior | Production resilience untested | Medium |

### Running the Suite

```bash
# Install dependencies
cd ai-coding-helper-app
uv sync

# Start the server
uv run uvicorn app.main:app --reload --port 8000

# Run all smoke tests
uv run pytest tests/smoke/ -v --server-url=http://localhost:8000

# Run with HTML report
uv run pytest tests/smoke/ -v --server-url=http://localhost:8000 --html=smoke-report.html

# Run as deployment gate (exit code 0 = pass, non-zero = block)
uv run pytest tests/smoke/ -v --server-url=http://localhost:8000 --strict-markers
```

### CI/CD Gate Integration

```yaml
# .github/workflows/smoke-tests.yml (example)
smoke-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.13"
    - run: |
        uv sync
        uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 &
        sleep 5
        uv run pytest tests/smoke/ -v --server-url=http://localhost:8000 --junitxml=smoke-results.xml
    - if: failure()
      run: echo "❌ Smoke tests failed — deployment blocked" && exit 1
```

---

## Summary of Defects Caught

| # | Defect | Where Caught | Status |
|---|--------|-------------|--------|
| 1 | Missing database tables → 500 on all CRUD operations | Smoke test `test_register_user_returns_201` → 500 | Fixed |
| 2 | Token validation returning 422 instead of 401 | Smoke test `test_invalid_token_returns_401` | Fixed |
| 3 | Missing auth header returning 403 instead of 401 | Smoke test `test_chat_without_auth_returns_401` | Fixed |
| 4 | Unhandled exceptions returning plain text "Internal Server Error" | Smoke tests (gap in error handling) | Fixed |
| 5 | LLM model names not matching available provider models | Smoke tests `test_chat_basic_message` → 500 | Fixed |
| 6 | Hardcoded GPT model names in registry | Architecture review | Fixed |

**Total real defects caught before production: 6**

---

## Deliverables Checklist

- [x] Written Risk List with 10 prioritized findings, severity levels, and owners
- [x] Langfuse Tracing: Current state analysis and per-node enhancement recommendation
- [x] Smoke Test Suite: 20 passing tests covering auth, chat, streaming, messages, health, guardrails, persistence
- [x] Coverage gap analysis for future test expansion
- [x] CI/CD integration template for deployment gating