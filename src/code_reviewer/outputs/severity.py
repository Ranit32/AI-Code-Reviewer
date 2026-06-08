"""
Severity Scorer — Computes per-finding and overall severity scores.
"""
from __future__ import annotations

from ..agents.base import Finding, Severity

_SEVERITY_WEIGHTS = {
    Severity.CRITICAL: 15,
    Severity.WARNING: 5,
    Severity.INFO: 1,
}


def compute_overall_score(findings: list[Finding]) -> int:
    """Returns a 0–100 code health score (100 = perfect)."""
    penalty = sum(
        _SEVERITY_WEIGHTS.get(f.severity, 0) * f.confidence
        for f in findings
    )
    return max(0, min(100, int(100 - penalty)))


def grade(score: int) -> str:
    if score >= 90:
        return "A"
    elif score >= 75:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 40:
        return "D"
    return "F"
