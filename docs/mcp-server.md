# MCP Server

This document describes the Model Context Protocol (MCP) server that exposes the
AI coding assistant's capabilities — including the full LangGraph `ReActAgent` —
to any MCP client (Claude Desktop, Cursor, custom frontends/backends, etc.).

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Exposed Tools](#exposed-tools)
- [Files Changed](#files-changed)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [Authentication](#authentication)
- [Deployment on Linux](#deployment-on-linux)
- [Connecting Clients](#connecting-clients)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Overview

The MCP server lives in `app/mcp/chunk_code_server.py` and is built with the
official `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`). It runs as an
independent process and speaks the MCP protocol over one of three transports:

| Transport          | CLI flag            | Use case                                      |
| ------------------ | ------------------- | --------------------------------------------- |
| `stdio`            | `--transport stdio` | Local clients, LangChain adapters             |
| `sse`              | `--transport sse`   | Remote clients over SSE (`/sse` endpoint)     |
| `streamable-http`  | `--transport streamable-http` | Remote clients over JSON-RPC HTTP (`/mcp` endpoint) |

It reuses the project's existing services, so there is **no duplicated
business logic**:

- `vector_store_service`-style pgvector queries for code search
- `DuckDuckGoSearchResults` for web search
- The full `ReActAgent` graph (`app/core/langgraph/ReAct_agent_graph.py`) for
  agent turns

---

## Architecture

```
                  ┌──────────────────────────────┐
                  │        MCP client            │
                  │  (frontend / backend / IDE)  │
                  └──────────────┬───────────────┘
                                 │ stdio | SSE | streamable-http
                                 ▼
                  ┌──────────────────────────────┐
                  │  app/mcp/chunk_code_server.py │
                  │  FastMCP("ai-coding-assistant")│
                  │  ──────────────────────────   │
                  │  lifespan: pre-warm graph     │
                  │  auth: Bearer token (HTTP)    │
                  │                              │
                  │  tools:                      │
                  │   • search_code (sync)       │
                  │   • web_search   (sync)      │
                  │   • ask_agent    (async)     │
                  └───────┬──────────────┬───────┘
                          │              │
                          ▼              ▼
             ┌────────────────┐   ┌──────────────────┐
             │ PostgreSQL     │   │ ReActAgent graph │
             │ (code_chunk    │   │ guardrails →      │
             │  pgvector,     │   │ intent → doc      │
             │  checkpoints,  │   │ pipeline → ReAct  │
             │  memory)       │   │ → outbound →      │
             └────────────────┘   │ store → summary   │
                                  └──────────────────┘
```

**Startup sequence** (`_mcp_lifespan`, `chunk_code_server.py:97`):
1. On server start, the FastMCP lifespan calls `ReActAgent.create_graph()`.
2. This opens the `AsyncPostgresSaver` connection pool and compiles the
   LangGraph `StateGraph` (same graph the FastAPI app pre-warms in
   `app/main.py:68`).
3. Failures are logged but **do not crash the server** — tools that need the DB
   return a descriptive error instead.

---

## Exposed Tools

### `search_code(query, session_id, user_id, file_name=None)` — sync

Semantic (pgvector) search over code chunks uploaded to the document pipeline.
Scoped to a `session_id` + `user_id`. Returns formatted code chunks or
`"No matching code found."`.

### `web_search(query)` — sync

DuckDuckGo web search via `DuckDuckGoSearchResults`. Returns up to 10 results
as text.

### `ask_agent(message, session_id, user_id, username=None)` — **async**

Runs the **full LangGraph `ReActAgent`** (`ReAct_agent_graph.py:258`):

```
secret_guardrail → inbound_intent → document_pipeline → agent (ReAct)
→ outbound → store_messages → summarization
```

- Loads conversation history from the Postgres checkpointer (`thread_id = session_id`)
- Pulls long-term memory (`mem0`) and the user's skill profile
- Builds the system prompt with `load_system_prompt(...)`
- Returns the assistant's final reply text

This is the tool that connects **your agent** to the MCP world: an MCP client
can carry on a full multi-turn conversation with the same agent that powers the
FastAPI `/chat` endpoint.

---

## Files Changed

### `app/mcp/chunk_code_server.py`

The core of this work. Changed from a one-tool server to a 3-tool server.

| Change | Location | Why |
| ------ | -------- | --- |
| Renamed server to `ai-coding-assistant` | `chunk_code_server.py:108` | It now does more than code search |
| Added `ReActAgent` import + module-level instance `_agent` | `chunk_code_server.py:34,43` | Shared instance reused by `ask_agent` |
| Added `_mcp_lifespan` | `chunk_code_server.py:97` | Pre-warms the LangGraph graph so the first call is fast |
| Added `ask_agent` tool | `chunk_code_server.py:207` | Exposes the full agent as an MCP tool |
| Added `_BearerAuthMiddleware` | `chunk_code_server.py:117` | Protects remote transports with a bearer token |
| Added `_suppress_stdout_fd` | `chunk_code_server.py:77` | Redirects fd 1 during DDG calls (see bug fix below) |
| Reworked `main()` + `_run_http` | `chunk_code_server.py:239` | Supports stdio / sse / streamable-http with auth |

### `app/core/logging.py`

```
- console_handler = logging.StreamHandler(sys.stdout)
+ console_handler = logging.StreamHandler(sys.stderr)
```

**Why:** The MCP stdio spec requires every line written to **stdout** to be a
JSON-RPC message. Previously the console log handler wrote to `sys.stdout`, so
structlog messages and third-party logs (e.g. the `mcp` package's own `Response
sent` debug line) polluted the protocol stream. stderr is the correct sink for
logs on a stdio server (uvicorn does the same). File-based JSON logging is
unaffected.

### `app/core/config.py`

Added three settings (after `MCP_SERVER_TRANSPORT`):

```python
self.MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
self.MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8100"))
self.MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
```

### `Makefile`

New targets:

```make
make mcp        # run over stdio
make mcp-sse    # run over SSE  (port 8100)
make mcp-http   # run over Streamable HTTP (port 8100)
```

### `docker-compose.yml`

Added a `mcp` service:

- Builds the same image as `app`
- Runs `uv run python -m app.mcp.chunk_code_server --transport sse`
- Exposes port `8100`
- Depends on a healthy `db`
- Reads `MCP_AUTH_TOKEN` from the environment file

### `.env.example`

Added:

```dotenv
MCP_SERVER_TRANSPORT=stdio
MCP_SERVER_HOST=0.0.0.0
MCP_SERVER_PORT=8100
MCP_AUTH_TOKEN=""
```

---

## Configuration

All settings come from the environment (see `app/core/config.py`).

| Variable               | Default      | Description                                    |
| ---------------------- | ------------ | ---------------------------------------------- |
| `MCP_SERVER_TRANSPORT` | `stdio`      | Default transport if no `--transport` flag     |
| `MCP_SERVER_HOST`      | `0.0.0.0`    | Bind address for HTTP transports               |
| `MCP_SERVER_PORT`      | `8100`       | Port for HTTP transports                       |
| `MCP_AUTH_TOKEN`       | *(empty)*    | Bearer token. When set, HTTP transports require `Authorization: Bearer <token>` |

---

## Running the Server

Requires PostgreSQL running (for `ask_agent` and `search_code`).

```bash
# stdio (local / LangChain adapter)
make mcp

# SSE — remote clients, port 8100
make mcp-sse

# Streamable HTTP — remote clients, port 8100
make mcp-http

# explicit invocation
uv run python -m app.mcp.chunk_code_server --transport sse --port 8100
```

---

## Authentication

`MCP_AUTH_TOKEN` only applies to the HTTP transports (`sse`, `streamable-http`).
When set, every request must include:

```
Authorization: Bearer <MCP_AUTH_TOKEN>
```

Clients without the token (or with a wrong token) get `401 unauthorized`.
`stdio` is inherently local and does not use token auth.

Verified behavior:

```
no token      → 401
wrong token   → 401
correct token → connection opened
```

---

## Deployment on Linux

### Option A — Docker Compose (recommended)

```bash
# 1. Set env vars (esp. MCP_AUTH_TOKEN for security)
vi .env.development

# 2. Start the stack (db + app + mcp + monitoring)
make stack-up

# 3. MCP server is now on port 8100
curl -H "Authorization: Bearer $MCP_AUTH_TOKEN" http://<host>:8100/sse
```

### Option B — Systemd / bare process

```bash
cd /home/pccv/ai-coding-helper-app
source scripts/set_env.sh development
uv run python -m app.mcp.chunk_code_server --transport sse
```

Run this behind a reverse proxy (nginx) that terminates TLS and forwards
`/sse` (or `/mcp`) to port 8100.

---

## Connecting Clients

Any MCP client can connect. Examples:

```python
# Python MCP client (SSE)
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    headers = {"Authorization": "Bearer YOUR_TOKEN"}
    async with sse_client("http://<host>:8100/sse", headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()      # -> search_code, web_search, ask_agent
            res = await session.call_tool(
                "ask_agent",
                {"message": "Explain this codebase",
                 "session_id": "my-thread-1", "user_id": 1},
            )
            print(res.content[0].text)
```

- **Frontend/backend**: your web app acts as the MCP client and proxies tool
  calls to `/sse` (or `/mcp`). The same bearer token protects it.
- **Cursor / Claude Desktop**: register the server as an SSE or command-based
  MCP server in their config; `ask_agent` then surfaces your agent in the IDE.

---

## Verification

The following were tested against the running server:

| Test | Result |
| ---- | ------ |
| `stdio` connect + `list_tools` | 3 tools: `search_code`, `web_search`, `ask_agent` |
| `web_search` call (stdio) | Returns results; **0 stdout parse errors** |
| `sse` connect + `list_tools` | 3 tools over HTTP |
| Auth: no/wrong/correct token | `401` / `401` / connection opened |
| `ruff check app/` | Passed |
| `pyright` (full project) | 0 errors, 0 warnings |

---

## Troubleshooting

### `couldn't get a connection after 30.00 sec`
PostgreSQL is not reachable. Start it (`make docker-up` / `make stack-up`) and
verify `POSTGRES_HOST`/`POSTGRES_PORT` in your env file match a running server.

### Client logs `Failed to parse JSONRPC message from server`
Something wrote non-JSON to stdout. Both known sources are fixed:
- App logs now go to **stderr** (`app/core/logging.py`)
- `primp`/ddgs debug output is suppressed by `_suppress_stdout_fd`
  (`chunk_code_server.py:77`)

If it recurs, find the culprit by looking for non-JSON lines on the server's
stdout and route the offending library's output to stderr.

### Tools listed but calls fail
- `search_code` / `ask_agent` need PostgreSQL with the `code_chunk` table and
  checkpointer tables (run migrations: `make migrate`).
- `ask_agent` needs `LITELLM_API_KEY` / `DEFAULT_LLM_MODEL` configured.

### Graph fails to pre-warm but server still starts
By design. `_mcp_lifespan` catches and logs failures; individual tool calls
will then return the underlying error until the DB is available.
