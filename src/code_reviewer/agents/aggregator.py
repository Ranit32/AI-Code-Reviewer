"""
Result Aggregator Agent — Merges, deduplicates, and ranks all findings.

Receives findings from all specialist agents, removes duplicates,
and ranks them by severity + confidence for the final report.
"""

from __future__ import annotations

import contextlib
from collections import defaultdict

from .base import (
    AgentType,
    BaseAgent,
    Finding,
    ReviewContext,
    Severity,
)

# Severity sort order (lower = more severe)
_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
}


class AggregatorAgent(BaseAgent):
    """
    Merges findings from all specialist agents:
    1. Deduplicates overlapping findings (same file + line + category)
    2. Applies severity overrides from rules.yaml
    3. Sorts by severity → confidence → line number
    4. Returns a clean, ranked list
    """

    agent_type = AgentType.AGGREGATOR

    async def run(self, context: ReviewContext) -> list[Finding]:
        # This agent is called differently — via aggregate()
        return []

    def aggregate(self, all_findings: list[Finding]) -> list[Finding]:
        """Main entry point. Takes raw findings from all agents."""
        # Apply rules-based severity overrides
        all_findings = self._apply_overrides(all_findings)

        # Deduplicate
        deduplicated = self._deduplicate(all_findings)

        # Sort: severity → confidence (desc) → filename → line
        deduplicated.sort(
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity, 99),
                -f.confidence,
                f.filename,
                f.line_start,
            )
        )

        return deduplicated

    # ------------------------------------------------------------------
    # Severity Overrides
    # ------------------------------------------------------------------

    def _apply_overrides(self, findings: list[Finding]) -> list[Finding]:
        overrides: dict[str, str] = self.rules.get("severity_overrides", {})
        if not overrides:
            return findings

        override_map: dict[str, Severity] = {}
        for rule_id, sev_str in overrides.items():
            with contextlib.suppress(ValueError):
                override_map[rule_id.upper()] = Severity(sev_str.lower())

        for finding in findings:
            # Match by full rule_id or a keyword in rule_id
            for rule_key, new_severity in override_map.items():
                if rule_key in finding.rule_id.upper():
                    finding.severity = new_severity
                    break

        return findings

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """
        Remove duplicates. Two findings are duplicates if they share:
        - Same filename
        - Same line range (within ±3 lines)
        - Same rule_id category (first segment e.g. SEC, STYLE, LOGIC)
        """
        seen: dict[tuple, Finding] = {}

        for finding in findings:
            category = finding.rule_id.split("-")[0]
            # Bucket by (filename, category, line_bucket)
            line_bucket = finding.line_start // 5  # 5-line buckets
            key = (finding.filename, category, line_bucket)

            if key not in seen:
                seen[key] = finding
            else:
                # Keep the higher-severity / higher-confidence one
                existing = seen[key]
                existing_order = _SEVERITY_ORDER.get(existing.severity, 99)
                new_order = _SEVERITY_ORDER.get(finding.severity, 99)

                if new_order < existing_order or (
                    new_order == existing_order and finding.confidence > existing.confidence
                ):
                    seen[key] = finding

        return list(seen.values())

    # ------------------------------------------------------------------
    # Summary Statistics
    # ------------------------------------------------------------------

    def summarize(self, findings: list[Finding]) -> dict:
        """Returns a summary dict for report headers."""
        counts: dict[str, int] = defaultdict(int)
        by_file: dict[str, int] = defaultdict(int)
        by_agent: dict[str, int] = defaultdict(int)

        for f in findings:
            counts[f.severity.value] += 1
            by_file[f.filename] += 1
            by_agent[f.agent.value] += 1

        return {
            "total": len(findings),
            "critical": counts.get("critical", 0),
            "warning": counts.get("warning", 0),
            "info": counts.get("info", 0),
            "by_file": dict(by_file),
            "by_agent": dict(by_agent),
        }
