import pytest
from pydantic import ValidationError
from app.schemas.review import Category, Finding, Severity


def test_valid_finding():
    finding = Finding(
        line=10,
        severity=Severity.HIGH,
        category=Category.CORRECTNESS,
        message="Division by zero",
        rationale="Variable x may be zero.",
    )

    assert finding.line == 10
    assert finding.severity == Severity.HIGH
    assert finding.category == Category.CORRECTNESS


def test_invalid_line():
    with pytest.raises(ValidationError):
        Finding(
            line=0,
            severity=Severity.HIGH,
            category=Category.CORRECTNESS,
            message="Invalid",
            rationale="Invalid line number.",
        )


def test_invalid_severity():
    with pytest.raises(ValidationError):
        Finding(
            line=5,
            severity="extreme",
            category=Category.CORRECTNESS,
            message="Invalid",
            rationale="Invalid severity.",
        )


def test_invalid_category():
    with pytest.raises(ValidationError):
        Finding(
            line=5,
            severity=Severity.HIGH,
            category="bug",
            message="Invalid",
            rationale="Invalid category.",
        )