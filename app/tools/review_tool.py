"""Basic rule-based correctness review tool."""

import ast
import builtins
import re

from app.schemas.review import Category, Finding, Severity


DIV_ZERO_PATTERN = re.compile(r"/\s*0\b")


def _undefined_name_findings(code: str) -> list[Finding]:
    """Find names used without a definition in the submitted Python code."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [
            Finding(
                line=error.lineno or 1,
                severity=Severity.HIGH,
                category=Category.CORRECTNESS,
                message="The code contains a syntax error.",
                rationale=str(error),
            )
        ]

    defined_names = set(dir(builtins))

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            defined_names.add(node.id)

        elif isinstance(node, ast.arg):
            defined_names.add(node.arg)

        elif isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            defined_names.add(node.name)

        elif isinstance(node, ast.alias):
            defined_names.add(node.asname or node.name.split(".")[0])

    findings: list[Finding] = []
    reported: set[str] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in defined_names
            and node.id not in reported
        ):
            reported.add(node.id)
            findings.append(
                Finding(
                    line=node.lineno,
                    severity=Severity.HIGH,
                    category=Category.CORRECTNESS,
                    message=f"Undefined name '{node.id}'.",
                    rationale=(
                        f"If this code executes, '{node.id}' may raise "
                        "a NameError because it is not defined."
                    ),
                )
            )

    return findings


def review_correctness(
    code: str,
    language: str | None = None,
) -> list[Finding]:
    """Review source code for basic correctness issues."""
    findings: list[Finding] = []

    if language and language.lower() in {"python", "py"}:
        findings.extend(_undefined_name_findings(code))

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