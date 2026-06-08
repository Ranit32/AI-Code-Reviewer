"""Tests for output generators."""

from __future__ import annotations

import pytest

from code_reviewer.agents.base import AgentType, Finding, Severity
from code_reviewer.outputs.artifact import MarkdownReportGenerator
from code_reviewer.outputs.severity import compute_overall_score, grade
from code_reviewer.outputs.suggestions import findings_to_pr_comments


def _make_finding(
    rule_id: str,
    severity: Severity,
    filename: str = "test.py",
    line: int = 10,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=f"Test: {rule_id}",
        description="Test description",
        severity=severity,
        agent=AgentType.STATIC,
        filename=filename,
        line_start=line,
        line_end=line,
        code_snippet="    some_code()",
        suggestion="Fix it like this",
        confidence=0.9,
        tags=["test"],
    )


# ---------------------------------------------------------------------------
# Severity Scorer
# ---------------------------------------------------------------------------

def test_score_perfect() -> None:
    assert compute_overall_score([]) == 100


def test_score_critical_lowers_score() -> None:
    findings = [_make_finding("CRIT", Severity.CRITICAL)]
    score = compute_overall_score(findings)
    assert score < 100
    # confidence=0.9, weight=15 → penalty=13.5 → score=86 (int)
    assert score <= 100 - 13


def test_score_clamped_to_zero() -> None:
    findings = [_make_finding(f"CRIT-{i}", Severity.CRITICAL) for i in range(20)]
    assert compute_overall_score(findings) == 0


def test_grade_a() -> None:
    assert grade(95) == "A"
    assert grade(90) == "A"


def test_grade_f() -> None:
    assert grade(0) == "F"
    assert grade(39) == "F"


# ---------------------------------------------------------------------------
# Markdown Report Generator
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_findings() -> list[Finding]:
    return [
        _make_finding("SEC-HARDCODED-SECRET", Severity.CRITICAL, "auth.py", 42),
        _make_finding("LOGIC-NULL-DEREF", Severity.WARNING, "service.py", 15),
        _make_finding("STYLE-MISSING-DOCSTRING", Severity.INFO, "utils.py", 5),
    ]


@pytest.fixture
def sample_summary(sample_findings) -> dict:
    return {
        "total": len(sample_findings),
        "critical": 1,
        "warning": 1,
        "info": 1,
        "by_agent": {"static_analysis": 3},
        "by_file": {"auth.py": 1, "service.py": 1, "utils.py": 1},
    }



def test_report_contains_job_id(sample_findings, sample_summary) -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate(sample_findings, sample_summary, job_id="test-job-123")
    assert "test-job-123" in report


def test_report_contains_severity_counts(sample_findings, sample_summary) -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate(sample_findings, sample_summary, job_id="job")
    assert "1" in report  # At least one count
    assert "CRITICAL" in report
    assert "WARNING" in report


def test_report_contains_filenames(sample_findings, sample_summary) -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate(sample_findings, sample_summary, job_id="job")
    assert "auth.py" in report
    assert "service.py" in report
    assert "utils.py" in report


def test_report_contains_suggestions(sample_findings, sample_summary) -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate(sample_findings, sample_summary, job_id="job")
    assert "Fix it like this" in report


def test_empty_report() -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate([], {"total": 0, "critical": 0, "warning": 0, "info": 0, "by_agent": {}}, job_id="empty")
    assert "No Issues Found" in report


def test_report_grade_shown(sample_findings, sample_summary) -> None:
    gen = MarkdownReportGenerator()
    report = gen.generate(sample_findings, sample_summary, job_id="graded")
    assert "Grade:" in report


# ---------------------------------------------------------------------------
# PR Comment Builder
# ---------------------------------------------------------------------------

def test_pr_comments_skips_no_line_findings() -> None:
    finding_no_line = _make_finding("INFO", Severity.INFO, line=0)
    finding_with_line = _make_finding("CRIT", Severity.CRITICAL, line=10)

    comments = findings_to_pr_comments([finding_no_line, finding_with_line])
    assert len(comments) == 1
    assert comments[0].line == 10


def test_pr_comment_severity_label() -> None:
    finding = _make_finding("SEC-CRIT", Severity.CRITICAL, line=5)
    comments = findings_to_pr_comments([finding])
    assert "CRITICAL" in comments[0].body


def test_pr_comment_contains_suggestion() -> None:
    finding = _make_finding("WARN", Severity.WARNING, line=1)
    comments = findings_to_pr_comments([finding])
    assert "Fix it like this" in comments[0].body
