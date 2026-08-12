"""Input guardrail #1: deterministic secret detection + redaction.

Runs before the prompt reaches the intent/injection classifier or the agent.
Redacts and continues rather than blocking — the request still proceeds,
just with secrets swapped out. No LLM involved: exact spans, zero secret
exposure to any model, deterministic and free.
"""

import asyncio
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import logger
from app.schemas.graph import GraphState


@dataclass
class SecretFinding:
    """Secret finding."""

    secret_type: str
    matched_text: str
    start: int
    end: int


@dataclass
class RedactionResult:
    """Redaction result."""

    redacted_text: str
    findings: list[SecretFinding] = field(default_factory=list)

    @property
    def had_secrets(self) -> bool:
        """Whether any secrets were found."""
        return len(self.findings) > 0


# --- Known secret patterns -------------------------------------------------
# Format-specific, near-zero false-positive rate. These run unconditionally.
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "aws_secret_key": re.compile(
        r"(?i)\b(?P<key>aws_secret_access_key)(?P<eq>\s*[:=]\s*)(?P<quote>['\"]?)(?P<value>[A-Za-z0-9/+=]{40})(?P=quote)"
    ),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "private_key_block": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "db_connection_string": re.compile(
        r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s'\"]+:[^\s'\"@]+@[^\s'\"]+"
    ),
    "generic_password_assignment": re.compile(
        r"(?i)\b(?P<key>(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token))"
        r"(?P<eq>\s*[:=]\s*)(?P<quote>['\"])(?P<value>[^'\"\s]{6,})(?P=quote)"
    ),
}

# Placeholder values are recognized only when the whole value is a
# placeholder (anchored match). Placeholder words nested inside a larger
# token — e.g. ``example`` inside ``db.example.com`` in a connection string —
# must not suppress redaction of a real-looking secret.
PLACEHOLDER_VALUE = re.compile(
    r"(?i)^[\s'\"]*(?:your[_-]?api[_-]?key|placeholder|example|xxxx+|dummy|changeme|<[^>]+>|\$\{[^}]+\})[\s'\"]*$"
)

# Our own substitution marker — never re-scan or re-redact it.
REDACTED_MARKER = re.compile(r"\[REDACTED_[A-Za-z_]+\]")


def _looks_like_placeholder(value: str) -> bool:
    """Check for common secret placeholders and our own redaction markers."""
    return bool(PLACEHOLDER_VALUE.match(value)) or bool(REDACTED_MARKER.search(value))


# --- Generic entropy fallback -----------------------------------------------
# High false-positive risk in a coding-mentor context (code pastes are full
# of UUIDs, hashes, minified output, random test fixtures). Gated two ways:
#   1. Only evaluated on tokens that sit in assignment-like context
#      (key = "...", key: "...", KEY=value) — not on any bare high-entropy
#      token anywhere in the text.
#   2. Denylist for common non-secret high-entropy shapes (UUIDs, git SHAs,
#      hex hashes) plus a non-secret key-name hint list, so those never
#      even reach the entropy check.

MIN_SECRET_LENGTH = 32  # raised from 24 — real keys are usually 32-64 chars
ENTROPY_THRESHOLD = 4.0  # bits/char; tune against real traffic

# Matches `<identifier-ish key> <=|:> "<value>"` or bare `KEY=value` (env-style).
ASSIGNMENT_CONTEXT = re.compile(
    r"""
    (?P<key>[A-Za-z_][A-Za-z0-9_.\-]{2,40})   # key/var name
    \s*[:=]\s*
    ['"]?(?P<value>[A-Za-z0-9_\-/+=]{%d,})['"]?  # value token
    """
    % MIN_SECRET_LENGTH,
    re.VERBOSE,
)

NON_SECRET_SHAPES = re.compile(
    r"""^(
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}  # UUID
        |[0-9a-f]{7,40}                                                # git SHA / hex hash
        |[0-9a-f]{32}                                                  # md5-style hash
        |[0-9a-f]{64}                                                  # sha256-style hash
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# Keys that indicate the assignment is very unlikely to be a secret even if
# the value is a long token (e.g. `id = "8f14e45f..."`, `sha = "..."`).
NON_SECRET_KEY_HINTS = re.compile(
    r"(?i)\b(id|uuid|guid|sha|hash|commit|checksum|digest|session_id|trace_id|request_id)\b"
)


def shannon_entropy(s: str) -> float:
    """Shannon entropy of a string."""
    if not s:
        return 0.0
    freq = {ch: s.count(ch) for ch in set(s)}
    length = len(s)
    return -sum((count / length) * math.log2(count / length) for count in freq.values())


def _find_entropy_secrets(text: str) -> list[SecretFinding]:
    """Find high-entropy secrets in assignment-like context."""
    findings: list[SecretFinding] = []
    for match in ASSIGNMENT_CONTEXT.finditer(text):
        key = match.group("key")
        value = match.group("value")

        if _looks_like_placeholder(value) or _looks_like_placeholder(key):
            continue
        if NON_SECRET_SHAPES.match(value):
            continue
        if NON_SECRET_KEY_HINTS.search(key):
            continue
        if shannon_entropy(value) < ENTROPY_THRESHOLD:
            continue

        # Exact span of the value within the match so redaction only
        # replaces the value, not the `key=` part.
        value_start = match.start("value")
        value_end = match.end("value")
        findings.append(
            SecretFinding(
                secret_type="generic_high_entropy",  # pragma: allowlist secret
                matched_text=value,
                start=value_start,
                end=value_end,
            )
        )
    return findings


def _replace_secret(match: re.Match, secret_type: str) -> str:
    """Return the replacement text for a single secret match.

    Patterns that capture a ``value`` group redact only the value so the
    surrounding ``key="..."`` syntax stays intact. Everything else is
    replaced wholesale. Placeholder-looking matches are left alone.
    """
    if "value" in match.re.groupindex:
        if _looks_like_placeholder(match.group("value")):
            return match.group(0)
        return (
            f"{match.group('key')}{match.group('eq')}{match.group('quote')}"
            f"[REDACTED_{secret_type.upper()}]"  # pragma: allowlist secret
            f"{match.group('quote')}"
        )
    if _looks_like_placeholder(match.group(0)):
        return match.group(0)
    return f"[REDACTED_{secret_type.upper()}]"  # pragma: allowlist secret


def detect_and_redact(text: str) -> RedactionResult:
    """Detect and redact secrets in text."""
    findings: list[SecretFinding] = []
    redacted = text

    # 1. Known vendor / format-specific patterns.
    for secret_type, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(redacted):
            if "value" in match.re.groupindex:
                if _looks_like_placeholder(match.group("value")):
                    continue
                matched_text = match.group("value")
                start, end = match.start("value"), match.end("value")
            else:
                if _looks_like_placeholder(match.group(0)):
                    continue
                matched_text = match.group(0)
                start, end = match.start(), match.end()
            findings.append(SecretFinding(secret_type=secret_type, matched_text=matched_text, start=start, end=end))
        redacted = pattern.sub(lambda m, _type=secret_type: _replace_secret(m, _type), redacted)

    # 2. Generic entropy fallback, gated behind assignment context + denylist.
    entropy_findings = _find_entropy_secrets(redacted)
    findings.extend(entropy_findings)
    # Apply in reverse order so earlier spans stay valid as we substitute.
    for f in sorted(entropy_findings, key=lambda f: f.start, reverse=True):
        redacted = redacted[: f.start] + "[REDACTED_SECRET]" + redacted[f.end :]

    return RedactionResult(redacted_text=redacted, findings=findings)


# --- LangGraph node wrapper --------------------------------------------------


async def secret_guardrail_node(state: GraphState) -> dict[str, Any]:
    """Redact secrets from the latest user prompt and any uploaded code.

    Runs first, before the intent classifier or agent. Prompts and code are
    scrubbed in place and the turn always proceeds — nothing is blocked and
    no secret value ever reaches an LLM. Findings are logged (type/count only).
    """
    update: dict[str, Any] = {}

    if state.messages and isinstance(state.messages[-1], HumanMessage):
        latest = state.messages[-1]
        if isinstance(latest.content, str):
            result = detect_and_redact(latest.content)
            if result.had_secrets:
                update["messages"] = [HumanMessage(content=result.redacted_text, id=latest.id)]
            logger.info(
                "secret_guardrail_prompt_scanned",
                secret_count=len(result.findings),
                secret_types=[f.secret_type for f in result.findings],
            )

    for attachment in state.pending_files:
        try:
            path = Path(attachment.stored_path)
            content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            result = detect_and_redact(content)
            if result.had_secrets:
                await asyncio.to_thread(path.write_text, result.redacted_text, encoding="utf-8")
                logger.info(
                    "secret_guardrail_code_redacted",
                    file_id=attachment.file_id,
                    file_name=attachment.original_name,
                    secret_count=len(result.findings),
                    secret_types=[f.secret_type for f in result.findings],
                )
        except Exception:
            logger.exception(
                "secret_guardrail_code_failed",
                file_id=attachment.file_id,
                file_name=attachment.original_name,
            )

    return update
