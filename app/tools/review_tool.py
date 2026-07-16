"""Basic rule-based correctness review tool."""

import re
from app.schemas.review import Category, Finding, Severity

DIV_ZERO_PATTERN = re.compile(r"/\s*0\b")


def review_correctness(code: str) -> list[Finding]:
    """Review source code for basic correctness issues."""
    findings: list[Finding] = []

    for line_number, line in enumerate(code.splitlines(), start=1):
        code_line = line.split("#", 1)[0]

        if DIV_ZERO_PATTERN.search(code_line):
            findings.append(
                Finding(
                    line=line_number,
                    severity=Severity.CRITICAL,
                    category=Category.CORRECTNESS,
                    message="Possible division by zero.",
                    rationale="Division by zero raises an exception at runtime.",
                )
            )

    return findings
