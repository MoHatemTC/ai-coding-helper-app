"""Inbound data-loss-prevention guardrail for user supplied content."""

import math
import re
import time
from collections import Counter
from typing import Any

import structlog

from app.schemas.review import InboundTriggerReason

logger: Any = structlog.get_logger(__name__)

_REDACTED_SECRET = "[REDACTED_SECRET]"
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>\b(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|credential|private[_-]?key)\b)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"'`,;#}]+)(?P=quote)",
    re.IGNORECASE,
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
    re.DOTALL,
)
_KNOWN_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox(?:a|b|p|r|s)-[A-Za-z0-9-]{10,}\b")),
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
)
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9+/=_-]{8,}(?![A-Za-z0-9_])")
_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_DATA_URI_PATTERN = re.compile(r"data:[^\s,;]+(?:;[^\s,;]+)*;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_PYTHON_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENTROPY_THRESHOLD = 3.5


def _shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of a candidate secret value."""
    if not value:
        return 0.0

    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _luhn_valid(digits: str) -> bool:
    """Return whether a digit-only value passes the Luhn checksum."""
    if not digits.isdigit():
        return False

    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        value = int(digit)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _is_benign_high_entropy(value: str, text: str, start: int, end: int) -> bool:
    """Exclude known harmless opaque values from entropy-based detection."""
    if _UUID_PATTERN.fullmatch(value) or _GIT_COMMIT_PATTERN.fullmatch(value):
        return True
    if _PYTHON_IDENTIFIER_PATTERN.fullmatch(value):
        return True
    return any(match.start() <= start and end <= match.end() for match in _DATA_URI_PATTERN.finditer(text))


def _redact_match(text: str, start: int, end: int) -> str:
    """Replace one sensitive value without changing adjacent source text."""
    return f"{text[:start]}{_REDACTED_SECRET}{text[end:]}"


def _is_comment_match(text: str, start: int) -> bool:
    """Return whether a match begins in a Python-style comment span."""
    line_start = text.rfind("\n", 0, start) + 1
    return text[line_start:start].lstrip().startswith("#")


def _scan_and_sanitize(text: str) -> tuple[str, list[str]]:
    """Detect secrets in text and redact only their sensitive values."""
    secret_types: list[str] = []
    replacements: list[tuple[int, int]] = []

    def record(secret_type: str, start: int, end: int) -> None:
        overlaps = [
            (existing_start, existing_end)
            for existing_start, existing_end in replacements
            if existing_start < end and start < existing_end
        ]
        if any(existing_start <= start and end <= existing_end for existing_start, existing_end in overlaps):
            return
        if overlaps:
            start = min([start, *(existing_start for existing_start, _ in overlaps)])
            end = max([end, *(existing_end for _, existing_end in overlaps)])
            replacements[:] = [span for span in replacements if span not in overlaps]
        replacements.append((start, end))
        if secret_type not in secret_types:
            secret_types.append(secret_type)

    for match in _PRIVATE_KEY_PATTERN.finditer(text):
        record("private_key", match.start(), match.end())

    for secret_type, pattern in _KNOWN_SECRET_PATTERNS:
        for match in pattern.finditer(text):
            record(secret_type, match.start(), match.end())

    for match in _SECRET_ASSIGNMENT_PATTERN.finditer(text):
        if _is_comment_match(text, match.start()):
            continue
        record("credential_assignment", match.start("value"), match.end("value"))

    for match in _CREDIT_CARD_PATTERN.finditer(text):
        digits = re.sub(r"[ -]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            record("credit_card_number", match.start(), match.end())

    for match in _HIGH_ENTROPY_TOKEN_PATTERN.finditer(text):
        value = match.group()
        if _is_benign_high_entropy(value, text, match.start(), match.end()):
            continue
        if _shannon_entropy(value) >= _ENTROPY_THRESHOLD:
            record("high_entropy_token", match.start(), match.end())

    sanitized = text
    for start, end in sorted(replacements, reverse=True):
        sanitized = _redact_match(sanitized, start, end)
    return sanitized, secret_types


async def inbound_dlp_node(state: dict[str, Any]) -> dict[str, Any]:
    """Sanitize inbound text and fail closed when DLP evaluation cannot complete."""
    start_time = time.perf_counter()
    code: str | None = None
    problem_id: Any = None

    try:
        problem_id = state.get("problem_id")
        raw_query = state.get("user_query", "")
        raw_code = state.get("code")
        user_query = raw_query if isinstance(raw_query, str) else ""
        code = raw_code if isinstance(raw_code, str) and raw_code.strip() else None
        sanitized_query, query_secret_types = _scan_and_sanitize(user_query)
        sanitized_code = code
        code_secret_types: list[str] = []
        if code is not None:
            sanitized_code, code_secret_types = _scan_and_sanitize(code)

        detected_secret_types = list(dict.fromkeys([*query_secret_types, *code_secret_types]))
        if detected_secret_types:
            logger.warning(
                "inbound_dlp_sensitive_data_detected",
                detected_secret_types=detected_secret_types,
                query_length=len(user_query),
                code_length=len(code or ""),
                problem_id=problem_id,
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )
            return {
                "is_safe_sensitive": False,
                "inbound_trigger_reason": InboundTriggerReason.SENSITIVE_DATA_EXPOSURE,
                "detected_secret_types": detected_secret_types,
                "sanitized_query": sanitized_query,
                "sanitized_code": sanitized_code,
            }

        logger.info(
            "inbound_dlp_completed",
            query_length=len(user_query),
            code_scanned=code is not None,
            problem_id=problem_id,
            latency_ms=(time.perf_counter() - start_time) * 1000,
        )
        return {
            "is_safe_sensitive": True,
            "detected_secret_types": [],
            "sanitized_query": sanitized_query,
            "sanitized_code": sanitized_code,
        }
    except Exception:
        logger.exception(
            "inbound_dlp_failed_closed",
            problem_id=problem_id,
            latency_ms=(time.perf_counter() - start_time) * 1000,
        )
        return {
            "is_safe_sensitive": False,
            "inbound_trigger_reason": InboundTriggerReason.DLP_SCANNER_ERROR,
            "detected_secret_types": [],
            "sanitized_query": _REDACTED_SECRET,
            "sanitized_code": _REDACTED_SECRET if code is not None else None,
        }
