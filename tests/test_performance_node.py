"""Tests for the performance/best-practice review node.
 
Three kinds of tests here:
 
1. Mocked tests (fast, free, run every time) — verify prompt-building and
   parsing logic without spending API credits or needing network access.
   `llm_service.call` is patched to return a fake, already-valid
   PerformanceReviewDraft.
 
2. Enum-enforcement tests — prove PerformanceIssueType/StyleSubtype are
   actually validated, not just documentation. An invalid subtype string
   must raise a ValidationError.
 
3. Real integration tests, marked `slow` — actually call the LLM to prove
   the whole chain works end to end, including that the model reliably
   picks a valid enum value. Skipped automatically if no API key is set.
 
No pytest-asyncio dependency required: async test bodies are run manually
via asyncio.run() inside plain, synchronous test functions.
"""
 
from __future__ import annotations
 
import asyncio
import os
from unittest.mock import AsyncMock, patch
 
import pytest
from pydantic import ValidationError
 
from app.core.langgraph.nodes.performance_node import (
    Category,
    Finding,
    PerformanceFindingDraft,
    PerformanceIssueType,
    PerformanceReviewDraft,
    Severity,
    StyleSubtype,
    _number_lines,
    run_performance_review,
)
 
# ---------------------------------------------------------------------------
# Sample code snippets covering both categories this lane emits.
# ---------------------------------------------------------------------------
 
PERFORMANCE_ISSUE_CODE = """def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates
"""
 
CODE_DUPLICATION_CODE = """def get_active_users(users):
    result = []
    for u in users:
        if u.is_active and u.email_verified and not u.is_banned:
            result.append(u)
    return result
 
def get_active_admins(users):
    result = []
    for u in users:
        if u.is_active and u.email_verified and not u.is_banned and u.is_admin:
            result.append(u)
    return result
"""
 
NAMING_ISSUE_CODE = """def f(x, y, z):
    a = x + y
    b = a * z
    return b
"""
 
CLEAN_CODE = """def add(a: int, b: int) -> int:
    \"\"\"Return the sum of two integers.\"\"\"
    return a + b
"""
 
EMPTY_CODE = ""
 
 
def run_async(coro):
    """Small helper: run an async coroutine inside a plain sync test function."""
    return asyncio.run(coro)
 
 
# ---------------------------------------------------------------------------
# Unit-level tests — pure logic, no LLM call, no asyncio needed.
# ---------------------------------------------------------------------------
 
 
def test_number_lines_prefixes_each_line():
    """Each line of code should be prefixed with its 1-based line number."""
    result = _number_lines("a\nb\nc")
    assert result == "1: a\n2: b\n3: c"
 
 
def test_number_lines_handles_empty_string():
    """Empty code should number to an empty string, not error."""
    assert _number_lines("") == ""
 
 
def test_finding_requires_line_to_be_at_least_1():
    """Finding.line must be >= 1; zero or negative values should be rejected."""
    with pytest.raises(ValidationError):
        Finding(
            line=0,
            severity=Severity.LOW,
            category=Category.STYLE,
            message="x",
            rationale="x",
        )
 
 
# ---------------------------------------------------------------------------
# Enum-enforcement tests — the actual fix for the "enums are decorative"
# critique. These prove PerformanceIssueType/StyleSubtype are validated by
# Pydantic, not just referenced in a docstring/prompt.
# ---------------------------------------------------------------------------
 
 
def test_invalid_issue_type_is_rejected():
    """An issue_type not in the enum must fail validation.
 
    This is what makes the enum active, not decorative.
    """
    with pytest.raises(ValidationError):
        PerformanceFindingDraft(
            line=1,
            severity=Severity.LOW,
            category=Category.PERFORMANCE,
            issue_type="synergy_optimization",  # not a real enum value
            message="x",
            rationale="x",
        )
 
 
def test_invalid_style_subtype_is_rejected():
    """A style_subtype not in the enum (e.g. a misspelling) must be rejected."""
    with pytest.raises(ValidationError):
        PerformanceFindingDraft(
            line=1,
            severity=Severity.LOW,
            category=Category.STYLE,
            style_subtype="bestpractice",  # wrong spelling, not the real enum value
            message="x",
            rationale="x",
        )
 
 
def test_valid_issue_type_is_accepted():
    """A real PerformanceIssueType value should be accepted as-is."""
    draft = PerformanceFindingDraft(
        line=1,
        severity=Severity.LOW,
        category=Category.PERFORMANCE,
        issue_type=PerformanceIssueType.ALGORITHMIC_COMPLEXITY,
        message="x",
        rationale="x",
    )
    assert draft.issue_type == PerformanceIssueType.ALGORITHMIC_COMPLEXITY
 
 
def test_valid_style_subtype_is_accepted():
    """A real StyleSubtype value should be accepted as-is."""
    draft = PerformanceFindingDraft(
        line=1,
        severity=Severity.LOW,
        category=Category.STYLE,
        style_subtype=StyleSubtype.BEST_PRACTICE,
        message="x",
        rationale="x",
    )
    assert draft.style_subtype == StyleSubtype.BEST_PRACTICE
 
 
# ---------------------------------------------------------------------------
# Mocked tests — verify run_performance_review's plumbing without spending
# API credits. We patch llm_service.call to return a canned, already-valid
# PerformanceReviewDraft, and check the draft-to-Finding conversion.
# ---------------------------------------------------------------------------
 
 
def test_run_performance_review_folds_issue_type_into_message():
    """A performance finding's issue_type should be folded into the message as a tag."""
    fake_draft = PerformanceReviewDraft(
        findings=[
            PerformanceFindingDraft(
                line=3,
                severity=Severity.MEDIUM,
                category=Category.PERFORMANCE,
                issue_type=PerformanceIssueType.ALGORITHMIC_COMPLEXITY,
                message="Nested loop makes this O(n^2)",
                rationale="A set-based approach would be O(n) instead.",
            )
        ]
    )
 
    with patch(
        "app.core.langgraph.nodes.performance_node.llm_service.call",
        new=AsyncMock(return_value=fake_draft),
    ) as mock_call:
        findings = run_async(run_performance_review(PERFORMANCE_ISSUE_CODE, language="python"))
 
    assert len(findings) == 1
    assert isinstance(findings[0], Finding)  # dropped down to the shared shape
    assert findings[0].category == Category.PERFORMANCE
    assert findings[0].line == 3
    assert findings[0].message.startswith("[algorithmic_complexity]")
 
    # Confirm the node requests the VALIDATED draft shape, not the bare
    # shared Finding shape.
    _, kwargs = mock_call.call_args
    assert kwargs["response_format"] is PerformanceReviewDraft
 
 
def test_run_performance_review_folds_style_subtype_into_message():
    """A style finding's style_subtype should be folded into the message as a tag."""
    fake_draft = PerformanceReviewDraft(
        findings=[
            PerformanceFindingDraft(
                line=1,
                severity=Severity.MEDIUM,
                category=Category.STYLE,
                style_subtype=StyleSubtype.BEST_PRACTICE,
                message="Two near-duplicate functions could share one helper",
                rationale="Duplicated logic means bug fixes have to be made twice.",
            )
        ]
    )
 
    with patch(
        "app.core.langgraph.nodes.performance_node.llm_service.call",
        new=AsyncMock(return_value=fake_draft),
    ):
        findings = run_async(run_performance_review(CODE_DUPLICATION_CODE, language="python"))
 
    assert findings[0].category == Category.STYLE
    assert findings[0].message.startswith("[best_practice]")
 
 
def test_run_performance_review_handles_no_findings():
    """An empty findings list from the LLM should return an empty list, not error."""
    with patch(
        "app.core.langgraph.nodes.performance_node.llm_service.call",
        new=AsyncMock(return_value=PerformanceReviewDraft(findings=[])),
    ):
        findings = run_async(run_performance_review(CLEAN_CODE, language="python"))
 
    assert findings == []
 
 
def test_run_performance_review_on_empty_code_does_not_crash():
    """Passing an empty code string should not raise an exception."""
    with patch(
        "app.core.langgraph.nodes.performance_node.llm_service.call",
        new=AsyncMock(return_value=PerformanceReviewDraft(findings=[])),
    ):
        findings = run_async(run_performance_review(EMPTY_CODE))
 
    assert findings == []
 
 
# ---------------------------------------------------------------------------
# Real integration tests — actually call the LLM. Marked `slow`.
# Skipped automatically if no API key is configured.
# Run explicitly with: pytest -m slow
#
# NOTE: these go through llm_service (OpenAI-based registry). See
# run_baseline_with_openrouter.py to test against a free OpenRouter model
# directly instead.
# ---------------------------------------------------------------------------
 
requires_api_key = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — skipping real LLM call",
)
 
 
@requires_api_key
@pytest.mark.slow
def test_real_llm_flags_the_nested_loop():
    """A real LLM call on the O(n^2) sample should return a performance finding."""
    findings = run_async(run_performance_review(PERFORMANCE_ISSUE_CODE, language="python"))
 
    assert len(findings) >= 1, "expected at least one real finding for an O(n^2) snippet"
    assert any(f.category == Category.PERFORMANCE for f in findings)
 
    for f in findings:
        assert f.line >= 1
 
 
@requires_api_key
@pytest.mark.slow
def test_real_llm_flags_code_duplication_as_style():
    """A real LLM call on near-duplicate functions should return a style finding."""
    findings = run_async(run_performance_review(CODE_DUPLICATION_CODE, language="python"))
 
    assert len(findings) >= 1
    assert any(f.category == Category.STYLE for f in findings), (
        "expected at least one style finding for near-duplicate functions"
    )
 
 
@requires_api_key
@pytest.mark.slow
def test_real_llm_flags_bad_naming_as_style():
    """A real LLM call on single-letter variable names should return a style finding."""
    findings = run_async(run_performance_review(NAMING_ISSUE_CODE, language="python"))
 
    assert len(findings) >= 1
    assert any(f.category == Category.STYLE for f in findings), (
        "expected at least one style finding for single-letter variable names"
    )
 
 
@requires_api_key
@pytest.mark.slow
def test_real_llm_returns_little_or_nothing_for_clean_code():
    """A real LLM call on trivial, clean code should return few or no findings."""
    findings = run_async(run_performance_review(CLEAN_CODE, language="python"))
 
    # Not a hard assertion of zero findings (LLMs can be opinionated), but
    # clean, trivial code shouldn't generate a pile of noise.
    assert len(findings) <= 1
 