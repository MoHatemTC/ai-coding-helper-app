# Tool Guardrails Research

## Overview

Guardrails are safety constraints that sit between the agent (or user) and tool execution. They enforce **input validation**, **output constraints**, **disallowed actions**, and **fail-closed behavior** to prevent abuse, resource exhaustion, data leakage, and unintended side effects.

---

## 1. Input Guardrails

### 1.1 Injection Prevention
| Attack Vector | Guardrail | Example Blocked |
|--------------|-----------|----------------|
| Shell injection | Block shell metacharacters (`;`, `|`, `&`, `` ` ``, `$()`, `$(command)`) | `query = "file.txt; rm -rf /"` |
| Python code injection | Block `eval(`, `exec(`, `__import__`, `compile(`, `getattr(`) | `code = "eval('__import__(\"os\").system(\"ls\")')"` |
| Path traversal | Block `../`, `..\\`, absolute paths on Windows (`C:\`) | `user_id = "../../etc/passwd"` |
| SQL injection | Block SQL keywords in unexpected contexts | `query = "1; DROP TABLE users"` |
| NoSQL injection | Block `$gt`, `$ne`, `$where` MongoDB operators | `query = "{\"$ne\": null}"` |
| LDAP injection | Block LDAP special chars (`*`, `()`, `&`, `|`) | `query = "*)(uid=*))"` |
| XML/HTML injection | Block `<script>`, `<!ENTITY`, `CDATA` sections | `content = "<script>alert('xss')</script>"` |
| Prompt injection | Block delimiter tokens, system prompt overrides | `query = "Ignore previous instructions and..."` |

### 1.2 Size & Resource Limits
| Constraint | Limit | Rationale |
|-----------|-------|-----------|
| Max input length | 100,000 chars | Prevent memory exhaustion |
| Max code length | 50,000 chars | Code reviews on reasonably-sized files |
| Max query length | 5,000 chars | Search queries should be concise |
| Max user_id length | 256 chars | Database key length limit |
| Max metadata JSON depth | 5 levels | Prevent deeply nested objects |
| Max metadata size | 10,000 chars | Prevent oversized metadata blobs |

### 1.3 Type & Schema Validation
- All parameters must match declared types (str, int, bool, etc.)
- Optional parameters must have sensible defaults
- Enums must be validated against allowed values
- JSON strings must be parseable before processing

### 1.4 Content Filtering (PII / Secrets)
| Pattern | Example |
|---------|---------|
| API keys | `sk-[a-zA-Z0-9]{20,}`, `ghp_[a-zA-Z0-9]{36}` |
| AWS keys | `AKIA[0-9A-Z]{16}` |
| JWT tokens | `eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+` |
| Email addresses | `user@example.com` |
| IP addresses | `10.x.x.x`, `192.168.x.x` (private) |
| Phone numbers | `+1-555-...` |
| Social security numbers | `\d{3}-\d{2}-\d{4}` |
| Database connection strings | `postgresql://user:pass@host/db` |

---

## 2. Output Guardrails

### 2.1 Size Limits
| Constraint | Limit | Behavior |
|-----------|-------|----------|
| Max output length | 50,000 chars | Truncate with notice |
| Max search results | 20 items | Truncate result list |
| Max findings per review | 100 items | Cap at reasonable maximum |

### 2.2 Sensitive Data Leakage
- Strip credentials, API keys, tokens from output
- Redact internal paths and server information
- Remove stack traces in production (return generic error)
- Filter out environment variable values

### 2.3 Content Safety
- Block harmful code patterns in generated output
- Prevent full-solution leaks (educational constraint)
- Flag dangerous system commands in output

---

## 3. Behavioral Guardrails

### 3.1 Fail-Closed Principle
> **If a guardrail check fails, the operation is BLOCKED — not allowed through.**

This is the most important principle. A guardrail that fails open (allowing the operation) is worse than no guardrail at all because it creates a false sense of security.

| Scenario | Fail-Closed Behavior |
|----------|---------------------|
| Input validation fails | Return error, do NOT execute tool |
| Output exceeds limits | Truncate, do NOT return raw output |
| Guardrail itself errors | Return error, do NOT execute tool |
| Rate limit exceeded | Return 429, do NOT execute tool |

### 3.2 Audit Logging
- Log every tool call: tool name, parameters (redacted), timestamp
- Log every guardrail violation: reason, field, input snippet
- Log every tool error: exception type, message, traceback
- All logs use structured format (JSON) for machine parsing

### 3.3 Rate Limiting
| Tool | Limit | Window |
|------|-------|--------|
| `web_search` | 30 calls | per minute |
| `memory_search` | 60 calls | per minute |
| `memory_add` | 30 calls | per minute |
| `review_code` | 30 calls | per minute |
| `ask_human` | 10 calls | per minute |

### 3.4 Disallowed Action Chains
- Prevent calling `ask_human` in a loop without user consent
- Prevent storing raw credentials into memory via `memory_add`
- Prevent searching memory with injection payloads

---

## 4. Implementation Architecture

```
Agent/User
    │
    ▼
┌─────────────────────────────┐
│  Input Guardrails           │  ← Injection check, size check, type check, PII scan
│  (fail-closed)              │     If FAIL → return error, STOP
└──────────┬──────────────────┘
           │ (pass)
           ▼
┌─────────────────────────────┐
│  Tool Execution             │  ← The actual tool logic
└──────────┬──────────────────┘
           │ (result)
           ▼
┌─────────────────────────────┐
│  Output Guardrails          │  ← Size truncation, PII redaction, content safety
│  (fail-closed)              │     If FAIL → return sanitized error, STOP
└──────────┬──────────────────┘
           │ (pass)
           ▼
     Agent/User ← Sanitized result
```

---

## 5. References

- [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- [OWASP Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_Cheat_Sheet.html)
- [LangGraph Security Guidelines](https://langchain-ai.github.io/langgraph/security/)
- [MCP Security Considerations](https://modelcontextprotocol.io/docs/concepts/security)
- [OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
