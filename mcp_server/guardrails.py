"""Tool guardrails for MCP server.

Provides input/output validation, disallowed action checks, PII/secret
detection, rate limiting, audit logging, and fail-closed behavior for all
MCP-exposed tools.

Architecture::

    Agent/User
        │
        ▼
    ┌─────────────────────────────┐
    │  Input Guardrails           │  ← Injection, size, type, PII checks
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
    │  Output Guardrails          │  ← Size truncation, PII redaction
    │  (fail-closed)              │     If FAIL → return sanitized error
    └──────────┬──────────────────┘
               │ (pass)
               ▼
         Agent/User ← Sanitized result
"""

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# =========================================================================
# 1. INJECTION PREVENTION
# =========================================================================

# Shell injection patterns
SHELL_METACHARACTERS = re.compile(r"[;|&`$()\[\]{}<>!]")

# Python code injection patterns
PYTHON_INJECTION = re.compile(
    r"(?:\b|_)"
    r"(?:"
    r"eval\s*\("
    r"|exec\s*\("
    r"|__import__\s*\("
    r"|compile\s*\("
    r"|getattr\s*\("
    r"|setattr\s*\("
    r"|delattr\s*\("
    r"|globals\s*\("
    r"|locals\s*\("
    r"|vars\s*\("
    r"|open\s*\("
    r"|__builtins__"
    r"|__class__"
    r"|__subclasses__"
    r"|__globals__"
    r")",
    re.IGNORECASE,
)

# Path traversal patterns
PATH_TRAVERSAL = re.compile(
    r"(?:"
    r"\.\.[/\\]"  # Unix: ../ or ..\
    r"|\.\.%2f"  # URL-encoded: ..%2f
    r"|%2e%2e%2f"  # Double URL-encoded: %2e%2e%2f
    r"|~\.\."  # Tilde-dot-dot
    r"|\.\.\\\\"  # Windows: ..\\
    r")",
    re.IGNORECASE,
)

# SQL injection patterns (for unexpected contexts like user_id, query)
SQL_INJECTION = re.compile(
    r"(?:\b|_)"
    r"(?:"
    r"DROP\s+TABLE"
    r"|DELETE\s+FROM"
    r"|INSERT\s+INTO"
    r"|UPDATE\s+\w+\s+SET"
    r"|ALTER\s+TABLE"
    r"|CREATE\s+TABLE"
    r"|TRUNCATE\s+TABLE"
    r"|EXEC\s*\("
    r"|EXECUTE\s*\("
    r"|UNION\s+SELECT"
    r"|--\s"  # SQL comment injection
    r"|;\s*DROP"
    r")",
    re.IGNORECASE,
)

# NoSQL injection patterns
NOSQL_INJECTION = re.compile(
    r"(?:"
    r"\$gt\b"
    r"|\$ne\b"
    r"|\$lt\b"
    r"|\$gte\b"
    r"|\$lte\b"
    r"|\$in\b"
    r"|\$nin\b"
    r"|\$where\b"
    r"|\$regex\b"
    r")",
    re.IGNORECASE,
)

# HTML/XML injection patterns
HTML_INJECTION = re.compile(
    r"(?:"
    r"<script[^>]*>"
    r"|<!ENTITY"
    r"|<!\[CDATA\["
    r"|<!DOCTYPE"
    r"|onerror\s*="
    r"|onload\s*="
    r"|onclick\s*="
    r"|javascript\s*:"
    r")",
    re.IGNORECASE,
)

# Prompt injection patterns
PROMPT_INJECTION = re.compile(
    r"(?:"
    r"Ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|directions|prompts)"
    r"|Forget\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|directions|prompts)"
    r"|Disregard\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|directions|prompts)"
    r"|You\s+are\s+(?:now\s+)?(?:an?\s+)?(?:free|unbounded|unrestricted|unconstrained)"
    r"|You\s+don'?t\s+(?:have\s+to|need\s+to)\s+(?:follow|obey|abide\s+by)"
    r"|SYSTEM\s*(?::|PROMPT)"
    r")",
    re.IGNORECASE,
)

# Combined disallowed patterns for general input
DISALLOWED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (SHELL_METACHARACTERS, "shell_metacharacters"),
    (PYTHON_INJECTION, "python_code_injection"),
    (PATH_TRAVERSAL, "path_traversal"),
    (SQL_INJECTION, "sql_injection"),
    (NOSQL_INJECTION, "nosql_injection"),
    (HTML_INJECTION, "html_injection"),
    (PROMPT_INJECTION, "prompt_injection"),
]

# =========================================================================
# 2. PII / SECRET DETECTION
# =========================================================================

# Patterns for detecting sensitive data in inputs and outputs
PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # API keys
    (re.compile(r"\b(?:sk|pk|rk)_[a-zA-Z0-9]{20,}\b"), "openai_api_key"),
    (re.compile(r"\bgh[pors]_[a-zA-Z0-9]{36,}\b"), "github_token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"), "jwt_token"),
    (re.compile(r"\b(?:api[-_]?key|apikey)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", re.IGNORECASE), "generic_api_key"),
    # Database connection strings
    (re.compile(r"(?:postgresql|mysql|mongodb|redis|amqp)://[^\s]+"), "database_url"),
    # Private IPs
    (
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
        "private_ip",
    ),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "email_address"),
    # Phone numbers
    (re.compile(r"\b\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}\b"), "phone_number"),
    # Social security numbers (US)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
    # Credit card numbers (Luhn-checkable)
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "credit_card"),
    # Authorization headers
    (re.compile(r"(?:Authorization|Bearer)\s*:\s*[A-Za-z0-9_\-\.]+", re.IGNORECASE), "auth_header"),
]

# =========================================================================
# 3. SIZE & RESOURCE LIMITS
# =========================================================================

MAX_INPUT_LENGTH = 100_000  # 100k chars for general input
MAX_CODE_LENGTH = 50_000  # 50k chars for code submissions
MAX_QUERY_LENGTH = 5_000  # 5k chars for search queries
MAX_USER_ID_LENGTH = 256  # 256 chars for user identifiers
MAX_METADATA_SIZE = 10_000  # 10k chars for metadata JSON
MAX_METADATA_DEPTH = 5  # 5 levels deep for nested metadata
MAX_OUTPUT_LENGTH = 50_000  # 50k chars for tool output
MAX_SEARCH_RESULTS = 20  # Max search results to return
MAX_FINDINGS = 100  # Max findings per review

# =========================================================================
# 4. RATE LIMITING
# =========================================================================

# Rate limits per tool: (max_calls, window_seconds)
RATE_LIMITS: dict[str, tuple[int, float]] = {
    "web_search": (30, 60.0),
    "memory_search": (60, 60.0),
    "memory_add": (30, 60.0),
    "review_code": (30, 60.0),
    "ask_human": (10, 60.0),
}

# In-memory rate limit tracker: tool_name -> list of timestamps.
#
# NOTE: This tracker is in-memory only. Rate limits will reset when the
# server restarts and are not shared across multiple server instances.
# For a single stdio MCP server (the typical deployment), this is
# sufficient. For multi-instance deployments, consider using Redis or
# another shared store.
#
# MAX_RATE_LIMIT_ENTRIES caps the number of timestamps kept per tool to
# prevent unbounded memory growth in pathological cases.
MAX_RATE_LIMIT_ENTRIES = 10_000

_rate_limit_tracker: dict[str, list[float]] = defaultdict(list)


def _clean_expired(tool_name: str, window: float) -> None:
    """Remove timestamps outside the current window and cap the list size.

    Args:
        tool_name: The tool whose timestamps to clean.
        window: The rolling window in seconds; older timestamps are removed.
    """
    now = time.time()
    cutoff = now - window
    entries = [t for t in _rate_limit_tracker[tool_name] if t > cutoff]
    # Cap the list to prevent unbounded growth
    if len(entries) > MAX_RATE_LIMIT_ENTRIES:
        entries = entries[-MAX_RATE_LIMIT_ENTRIES:]
    _rate_limit_tracker[tool_name] = entries


# =========================================================================
# 5. AUDIT LOGGING
# =========================================================================

_audit_log: list[dict[str, Any]] = []


def _log_audit(
    event: str,
    tool_name: str,
    details: dict[str, Any],
    redacted_params: dict[str, str] | None = None,
) -> None:
    """Append a structured audit log entry.

    Args:
        event: The event type (e.g. 'tool_call', 'guardrail_blocked', 'tool_error').
        tool_name: The name of the tool being called.
        details: Event-specific details.
        redacted_params: The tool parameters with sensitive values redacted.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "tool": tool_name,
        "details": details,
    }
    if redacted_params:
        entry["params"] = redacted_params
    _audit_log.append(entry)


def get_audit_log() -> list[dict[str, Any]]:
    """Return the full audit log (for debugging/monitoring)."""
    return list(_audit_log)


def clear_audit_log() -> None:
    """Clear the audit log."""
    _audit_log.clear()


def clear_rate_limits() -> None:
    """Clear all rate limit trackers.

    Useful for testing or resetting rate limits without restarting the server.
    """
    _rate_limit_tracker.clear()


# =========================================================================
# 6. EXCEPTIONS
# =========================================================================


class GuardrailError(Exception):
    """Raised when a guardrail check fails.

    Attributes:
        message: Human-readable error description.
        reason: Machine-readable error reason code.
        field: The input field that triggered the guardrail.
    """

    def __init__(self, message: str, reason: str, field: str = "input") -> None:
        """Initialize the guardrail error.

        Args:
            message: Human-readable error description.
            reason: Machine-readable error reason code.
            field: The input field that triggered the guardrail.
        """
        self.reason = reason
        self.field = field
        super().__init__(message)


# =========================================================================
# 7. INPUT GUARDRAIL FUNCTIONS
# =========================================================================


def check_injection_safety(text: str, field_name: str = "input") -> None:
    """Check input text for injection patterns across all categories.

    Args:
        text: The input text to check.
        field_name: Name of the field being checked (for error messages).

    Raises:
        GuardrailError: If an injection pattern is detected.
    """
    if not isinstance(text, str) or not text:
        return

    for pattern, category in DISALLOWED_PATTERNS:
        match = pattern.search(text)
        if match:
            raise GuardrailError(
                f"{field_name} contains {category.replace('_', ' ')}: '{match.group()[:50]}'",
                reason=category,
                field=field_name,
            )


def check_pii(text: str, field_name: str = "input") -> list[dict[str, str]]:
    """Scan text for PII/secret patterns.

    This is a detection-only check. It returns the list of detected patterns
    but does NOT block by default (blocking is configured per-tool).

    Args:
        text: The text to scan.
        field_name: Name of the field being scanned.

    Returns:
        List of dicts with 'pattern' and 'type' keys for each detection.
    """
    detections: list[dict[str, str]] = []
    if not isinstance(text, str) or not text:
        return detections

    for pattern, pii_type in PII_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            detections.append(
                {
                    "pattern": match[:20] + "..." if len(match) > 20 else match,
                    "type": pii_type,
                    "field": field_name,
                }
            )
    return detections


def check_size_limit(
    text: str,
    max_length: int,
    field_name: str = "input",
) -> None:
    """Check that text does not exceed a maximum length.

    Args:
        text: The text to check.
        max_length: Maximum allowed length.
        field_name: Name of the field being checked.

    Raises:
        GuardrailError: If the text exceeds the maximum length.
    """
    if not isinstance(text, str):
        return
    if len(text) > max_length:
        raise GuardrailError(
            f"{field_name} exceeds maximum length of {max_length} characters (got {len(text)})",
            reason="size_limit_exceeded",
            field=field_name,
        )


def check_metadata_safety(metadata_json: str) -> dict[str, Any]:
    """Validate and parse a metadata JSON string.

    Args:
        metadata_json: The JSON string to validate.

    Returns:
        Parsed metadata dictionary.

    Raises:
        GuardrailError: If the metadata is invalid or exceeds limits.
    """
    if not metadata_json or metadata_json == "{}":
        return {}

    check_size_limit(metadata_json, MAX_METADATA_SIZE, field_name="metadata")

    try:
        parsed = json.loads(metadata_json)
    except json.JSONDecodeError as e:
        raise GuardrailError(
            f"metadata is not valid JSON: {e!s}",
            reason="invalid_json",
            field="metadata",
        )

    if not isinstance(parsed, dict):
        raise GuardrailError(
            "metadata must be a JSON object",
            reason="invalid_metadata_type",
            field="metadata",
        )

    # Check nesting depth
    def _check_depth(obj: Any, depth: int = 0) -> None:
        if depth > MAX_METADATA_DEPTH:
            raise GuardrailError(
                f"metadata exceeds maximum nesting depth of {MAX_METADATA_DEPTH}",
                reason="metadata_too_deep",
                field="metadata",
            )
        if isinstance(obj, dict):
            for v in obj.values():
                _check_depth(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                _check_depth(v, depth + 1)

    _check_depth(parsed)
    return parsed


def check_user_id(user_id: str) -> None:
    """Validate a user identifier.

    Args:
        user_id: The user ID to validate.

    Raises:
        GuardrailError: If the user ID is invalid.
    """
    if not isinstance(user_id, str) or not user_id:
        raise GuardrailError(
            "user_id must be a non-empty string",
            reason="invalid_user_id",
            field="user_id",
        )
    check_size_limit(user_id, MAX_USER_ID_LENGTH, field_name="user_id")
    check_injection_safety(user_id, field_name="user_id")


def check_query(query: str) -> None:
    """Validate a search query.

    Args:
        query: The query to validate.

    Raises:
        GuardrailError: If the query is invalid.
    """
    if not isinstance(query, str) or not query:
        raise GuardrailError(
            "query must be a non-empty string",
            reason="invalid_query",
            field="query",
        )
    check_size_limit(query, MAX_QUERY_LENGTH, field_name="query")
    check_injection_safety(query, field_name="query")


def check_code_safety(code: str) -> None:
    """Check submitted code for safety concerns.

    Code is a special case: we allow shell metacharacters and Python
    injection patterns because they're valid in source code. We only
    check size limits and path traversal.

    Args:
        code: The code to check.

    Raises:
        GuardrailError: If the code fails safety checks.
    """
    if not isinstance(code, str):
        return

    check_size_limit(code, MAX_CODE_LENGTH, field_name="code")

    # Only check path traversal in code (not shell or Python injection)
    pt_match = PATH_TRAVERSAL.search(code)
    if pt_match:
        raise GuardrailError(
            f"code contains path traversal: '{pt_match.group()[:50]}'",
            reason="path_traversal",
            field="code",
        )


# =========================================================================
# 8. OUTPUT GUARDRAIL FUNCTIONS
# =========================================================================


def redact_pii(text: str) -> str:
    """Redact PII/secret patterns from output text.

    Args:
        text: The text to redact.

    Returns:
        Text with sensitive patterns replaced by '[REDACTED]'.
    """
    if not isinstance(text, str) or not text:
        return text

    for pattern, pii_type in PII_PATTERNS:
        text = pattern.sub(f"[REDACTED:{pii_type}]", text)
    return text


def truncate_output(output: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    """Truncate output to a safe maximum length.

    Args:
        output: The output string to truncate.
        max_length: Maximum allowed length.

    Returns:
        Truncated output string with a truncation notice.
    """
    if not isinstance(output, str) or len(output) <= max_length:
        return output
    return output[:max_length] + "\n\n[...output truncated at {} characters]".format(max_length)


def limit_search_results(results: list[Any], max_results: int = MAX_SEARCH_RESULTS) -> list[Any]:
    """Limit search results to a safe maximum.

    Args:
        results: The search results list.
        max_results: Maximum number of results to return.

    Returns:
        Truncated results list.
    """
    if not isinstance(results, list):
        return results
    return results[:max_results]


def sanitize_output(output: str, tool_name: str = "unknown") -> str:
    """Apply all output guardrails: truncation + PII redaction.

    Args:
        output: The raw tool output.
        tool_name: The name of the tool (for audit logging).

    Returns:
        Sanitized output string.
    """
    # Step 1: Redact PII
    sanitized = redact_pii(output)
    # Step 2: Truncate
    sanitized = truncate_output(sanitized)
    return sanitized


# =========================================================================
# 9. BEHAVIORAL GUARDRAIL FUNCTIONS
# =========================================================================


def check_rate_limit(tool_name: str) -> None:
    """Check if a tool has exceeded its rate limit.

    Args:
        tool_name: The name of the tool being called.

    Raises:
        GuardrailError: If the rate limit has been exceeded.
    """
    if tool_name not in RATE_LIMITS:
        return

    max_calls, window = RATE_LIMITS[tool_name]
    _clean_expired(tool_name, window)

    if len(_rate_limit_tracker[tool_name]) >= max_calls:
        raise GuardrailError(
            f"Rate limit exceeded for {tool_name}: {max_calls} calls per {window:.0f}s",
            reason="rate_limit_exceeded",
            field=tool_name,
        )

    _rate_limit_tracker[tool_name].append(time.time())


def redact_sensitive_params(params: dict[str, Any]) -> dict[str, str]:
    """Redact sensitive parameter values for audit logging.

    Args:
        params: The original tool parameters.

    Returns:
        Parameters with sensitive values redacted.
    """
    SENSITIVE_KEYS = {"code", "content", "query", "question", "metadata"}
    redacted: dict[str, str] = {}
    for key, value in params.items():
        if key in SENSITIVE_KEYS and isinstance(value, str) and len(value) > 50:
            redacted[key] = value[:50] + "..."
        else:
            redacted[key] = str(value) if not isinstance(value, str) else value
    return redacted


# =========================================================================
# 10. COMPOSITE GUARDRAIL
# =========================================================================


def apply_input_guardrails(
    tool_name: str,
    params: dict[str, Any],
    *,
    check_pii_flag: bool = False,
) -> dict[str, Any]:
    """Apply all input guardrails for a tool call.

    This is the main entry point for input validation. It runs:
    1. Rate limit check
    2. Injection safety checks (per-field)
    3. Size limit checks (per-field)
    4. PII detection (optional, configurable per-tool)
    5. Audit logging

    Args:
        tool_name: The name of the tool being called.
        params: The tool parameters.
        check_pii_flag: Whether to scan for PII and block if found.

    Returns:
        The validated parameters (metadata is parsed from JSON if present).

    Raises:
        GuardrailError: If any guardrail check fails.
    """
    # 1. Rate limit
    check_rate_limit(tool_name)

    # 2-3. Per-field checks
    for field_name, value in params.items():
        if not isinstance(value, str):
            continue

        if field_name == "code":
            check_code_safety(value)
        elif field_name in ("query", "question"):
            check_query(value)
        elif field_name == "user_id":
            check_user_id(value)
        elif field_name == "metadata":
            # metadata is validated and parsed separately
            pass
        else:
            check_size_limit(value, MAX_INPUT_LENGTH, field_name=field_name)
            check_injection_safety(value, field_name=field_name)

        # 4. PII detection (optional)
        if check_pii_flag:
            detections = check_pii(value, field_name=field_name)
            if detections:
                types = ", ".join(d["type"] for d in detections)
                raise GuardrailError(
                    f"{field_name} contains sensitive data: {types}",
                    reason="pii_detected",
                    field=field_name,
                )

    # 5. Audit log
    _log_audit(
        event="tool_call",
        tool_name=tool_name,
        details={"status": "passed_guardrails"},
        redacted_params=redact_sensitive_params(params),
    )

    return params


def apply_output_guardrails(
    output: str,
    tool_name: str,
    *,
    redact: bool = True,
) -> str:
    """Apply all output guardrails.

    Args:
        output: The raw tool output.
        tool_name: The name of the tool (for audit logging).
        redact: Whether to redact PII from output.

    Returns:
        Sanitized output string.
    """
    sanitized = sanitize_output(output, tool_name=tool_name) if redact else truncate_output(output)

    _log_audit(
        event="tool_output",
        tool_name=tool_name,
        details={
            "original_length": len(output),
            "sanitized_length": len(sanitized),
            "was_truncated": len(sanitized) < len(output),
        },
    )

    return sanitized
