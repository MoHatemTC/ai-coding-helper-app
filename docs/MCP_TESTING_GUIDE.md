# MCP Server Tools - Live Testing Guide

This guide explains how to test the MCP server tools using real LLM calls.

## Overview

The MCP server exposes 3 tools that can be tested:

1. **`web_search`** - Tavily web search (no LLM required)
2. **`memory_search`** - Search long-term memory (uses LLM for embeddings)
3. **`ask_human`** - Interactive human-in-the-loop (requires manual input)

## Prerequisites

### 1. Environment Configuration

Ensure your `.env.development` file has the following variables set:

```env
# Required for LLM-powered features (memory tools)
LITELLM_API_KEY=your-api-key-here
LITELLM_BASE_URL=https://learner-os.sprints.ai/litellm
DEFAULT_LLM_MODEL=fw-kimi-k2.6

# Database (required for memory tools)
POSTGRES_HOST=localhost
POSTGRES_DB=mydb
POSTGRES_USER=myuser
POSTGRES_PASSWORD=mypassword
POSTGRES_PORT=5432
```

### 2. Install Dependencies

```bash
cd ai-coding-helper-app
uv run make install
```

### 3. Start Database (for memory tools)

```bash
# Start PostgreSQL with pgvector extension
uv run make docker-up

# Apply database migrations
uv run make migrate
```

## Running the Tests

### Option 1: Direct Python Script (Recommended)

```bash
# From the project root
uv run python test_mcp_tools_live.py
```

This will run all tests sequentially:
- Code review with buggy Python code
- Web search for Python asyncio best practices
- Memory add/search with LLM embeddings
- Guardrail enforcement tests
- Human-in-the-loop tool (informational only)

### Option 2: Using pytest

```bash
# Run the integration test that uses real LLM calls
uv run pytest tests/integration/integration_test.py -s
```

### Option 3: Test Individual Tools via MCP Client

You can also test tools individually using an MCP client like Claude Desktop or Cline:

#### Example: Test review_code

```json
{
  "tool": "review_code",
  "parameters": {
    "code": "def hello():\n    print('world')\n    return 1 / 0",
    "language": "python"
  }
}
```

#### Example: Test web_search

```json
{
  "tool": "web_search",
  "parameters": {
    "query": "Python asyncio best practices"
  }
}
```

#### Example: Test memory_add

```json
{
  "tool": "memory_add",
  "parameters": {
    "user_id": "user-123",
    "content": "User prefers Python for backend development",
    "metadata": "{\"type\": \"preference\", \"category\": \"programming\"}"
  }
}
```

#### Example: Test memory_search

```json
{
  "tool": "memory_search",
  "parameters": {
    "user_id": "user-123",
    "query": "What programming languages does the user prefer?"
  }
}
```

## Understanding the Test Output

### review_code Output

```json
{
  "findings": [
    {
      "line": 5,
      "severity": "high",
      "category": "correctness",
      "message": "Potential division by zero",
      "rationale": "Division by zero will occur if numbers is empty"
    }
  ],
  "finding_count": 1
}
```

### web_search Output

```json
{
  "results": [
    {
      "title": "Python Asyncio Best Practices",
      "url": "https://example.com/...",
      "snippet": "..."
    }
  ]
}
```

### memory_add Output

```json
{
  "status": "ok",
  "message": "Memory stored successfully."
}
```

### memory_search Output

```json
{
  "results": [
    {
      "memory": "User prefers Python over JavaScript...",
      "metadata": {
        "type": "preference",
        "category": "programming"
      }
    }
  ]
}
```

## Testing Guardrails

The test script includes guardrail tests that verify:

1. **SQL Injection Detection** - Malicious SQL in code is detected
2. **Prompt Injection Detection** - Attempts to override system prompts are blocked
3. **PII Detection** - Sensitive data (emails, API keys) is blocked or redacted
4. **Rate Limiting** - Excessive calls are throttled
5. **Size Limits** - Oversized inputs are rejected

### Example Guardrail Test

```python
# This will be blocked by the SQL injection guardrail
malicious_code = "query = f'SELECT * FROM users WHERE id = {user_id}; DROP TABLE users;'"
result = await review_code(code=malicious_code, language="python")
# Expected: Error response indicating SQL injection detected
```

## Monitoring LLM Calls

### Using Langfuse

If `LANGFUSE_TRACING_ENABLED=true` and Langfuse credentials are configured, you can monitor all LLM calls:

```env
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_PUBLIC_KEY=your-public-key
LANGFUSE_SECRET_KEY=your-secret-key
LANGFUSE_HOST=https://cloud.langfuse.com
```

View traces at: https://cloud.langfuse.com

### Using Audit Log

The MCP server maintains an audit log of all tool calls:

```python
from mcp_server.guardrails import get_audit_log

# Get recent audit entries
entries = get_audit_log()
for entry in entries[-10:]:
    print(entry)
```

## Troubleshooting

### Issue: "Missing LITELLM_API_KEY"

**Solution:** Ensure `.env.development` exists with valid API credentials:
```bash
cp .env.example .env.development
# Edit .env.development and add your API key
```

### Issue: "Memory tools fail with database connection error"

**Solution:** Start PostgreSQL:
```bash
uv run make docker-up
# Wait for database to be ready, then:
uv run make migrate
```

### Issue: "LLM calls timeout"

**Solution:** Check your network connection and LiteLLM proxy status. The default timeout is 60 seconds.

### Issue: "No memories found"

**Solution:** Ensure:
1. Database migrations are applied
2. pgvector extension is enabled
3. LLM API key is valid (mem0 uses LLM for embeddings)

## Advanced Testing

### Testing with Different LLM Models

Modify the test script to use different models:

```python
from app.services.llm.registry import LLMRegistry

# Use a different model
llm = LLMRegistry.get("kimi-k2.6")
response = await llm.ainvoke([{"role": "user", "content": "Hello!"}])
```

### Load Testing

Test rate limiting by making many rapid calls:

```python
import asyncio

async def test_rate_limiting():
    for i in range(35):  # Limit is 30 per 60s
        result = await web_search(query=f"test {i}")
        print(f"Call {i+1}: {result[:50]}...")
```

### Testing Memory Isolation

Verify that user memories are properly isolated:

```python
# Add memory for user A
await memory_add(user_id="user-a", content="User A secret")

# Try to access from user B (should not find it)
result = await memory_search(user_id="user-b", query="secret")
# Expected: "No relevant memories found."
```

## Performance Benchmarks

Typical execution times (may vary based on network and LLM provider):

- **review_code**: <100ms (rule-based, no LLM)
- **web_search**: 1-3s (Tavily API)
- **memory_add**: 2-5s (includes LLM embedding generation)
- **memory_search**: 1-3s (includes LLM embedding generation)
- **ask_human**: N/A (requires human response)

## Next Steps

1. **Add custom tools** - See `app/core/langgraph/tools/` for examples
2. **Configure rate limits** - Edit `RATE_LIMITS` in `mcp_server/guardrails.py`
3. **Add custom guardrails** - Extend the guardrail functions
4. **Monitor with Langfuse** - Enable tracing for production debugging
5. **Run full test suite** - `uv run make test`

## Additional Resources

- [MCP Protocol Specification](https://modelcontextprotocol.io/)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [LangChain Tools Guide](https://python.langchain.com/docs/modules/tools/)
- [mem0 Documentation](https://docs.mem0.ai/)
- [Project README](README.md)