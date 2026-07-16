"""LangGraph node for reviewing submitted code for security issues."""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.schemas.review import Category, Finding, Severity
from app.services.llm import llm_service

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "security_review.md"


class SecurityIssueType(str, Enum):
    """Security vulnerability classes targeted by this review lane."""

    SECRET_EXPOSURE = "secret_exposure"  # pragma: allowlist secret
    INJECTION = "injection"
    BROKEN_AUTHENTICATION = "broken_authentication"
    BROKEN_AUTHORIZATION = "broken_authorization"
    INSECURE_DESERIALIZATION = "insecure_deserialization"
    INSECURE_CONFIGURATION = "insecure_configuration"
    SENSITIVE_DATA_EXPOSURE = "sensitive_data_exposure"


class SecurityFindingDraft(BaseModel):
    """LLM response shape with a validated security taxonomy label."""

    line: int = Field(ge=1, description="1-based source line containing the issue")
    severity: Severity
    issue_type: SecurityIssueType
    message: str = Field(description="Concise description of the security issue")
    rationale: str = Field(description="Exploit path and conceptual mitigation guidance")


class SecurityReviewResponse(BaseModel):
    """Structured LLM response for the security review lane."""

    findings: List[SecurityFindingDraft] = Field(default_factory=list)


def _load_security_prompt() -> str:
    """Load the security-specific system prompt bundled with the application."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _number_lines(code: str) -> str:
    """Prefix source lines so findings can reference a stable location."""
    return "\n".join(f"{line_number}: {line}" for line_number, line in enumerate(code.splitlines(), start=1))


def _get_code(state: Dict[str, Any]) -> str:
    """Extract submitted code while accepting the legacy user_code state key."""
    raw_code: Any = state.get("code", state.get("user_code", ""))
    return raw_code if isinstance(raw_code, str) else ""


def _to_finding(draft: SecurityFindingDraft) -> Finding:
    """Convert a taxonomy-validated draft into the shared finding schema."""
    return Finding(
        line=draft.line,
        severity=draft.severity,
        category=Category.SECURITY,
        message=f"[{draft.issue_type.value}] {draft.message}",
        rationale=draft.rationale,
    )


async def security_review_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Review code for security vulnerabilities and return shared typed findings."""
    code: str = _get_code(state)
    if not code.strip():
        logger.info("security_review_skipped_empty_code")
        return {"findings": []}

    language: Any = state.get("language")
    language_hint: str = f"Language: {language}\n\n" if isinstance(language, str) and language else ""
    user_payload: str = f"{language_hint}Numbered code:\n{_number_lines(code)}"

    logger.info("security_review_started", code_length=len(code))
    try:
        response: SecurityReviewResponse = await llm_service.call(
            [
                SystemMessage(content=_load_security_prompt()),
                HumanMessage(content=user_payload),
            ],
            response_format=SecurityReviewResponse,
            temperature=0,
        )
    except Exception:
        logger.exception("security_review_failed")
        return {"findings": []}

    findings: List[Finding] = [_to_finding(draft) for draft in response.findings]
    logger.info("security_review_completed", finding_count=len(findings))
    return {"findings": [finding.model_dump() for finding in findings]}
