"""LLM-powered code review tool."""

from app.schemas.review import Category, Finding, Severity


def review_correctness(code: str) -> list[Finding]:
    """Review source code for basic correctness issues."""
    findings = []
    lines = code.splitlines()

    for i, line in enumerate(lines, start=1):
        if "/ 0" in line or "/0" in line:
            findings.append(
                Finding(
                    line=i,
                    severity=Severity.CRITICAL,
                    category=Category.CORRECTNESS,
                    message="Possible division by zero.",
                    rationale="Division by zero raises an exception at runtime.",
                )
            )

    return findings
