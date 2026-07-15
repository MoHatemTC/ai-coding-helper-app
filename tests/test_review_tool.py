from app.tools.review_tool import review_correctness
from app.schemas.review import Category, Severity


def test_detects_division_by_zero():
    code = """x = 10
    y = x / 0
    """

    findings = review_correctness(code)

    assert len(findings) == 1

    finding = findings[0]

    assert finding.line == 2
    assert finding.category == Category.CORRECTNESS
    assert finding.severity == Severity.CRITICAL


def test_correct_code_returns_no_findings():
    code = """
    x = 10
    y = x / 2
    print(y)
    """

    findings = review_correctness(code)

    assert findings == []