"""
Markdown Report Generator — Produces the final review artifact.

Generates a rich Markdown report with:
- Executive summary (severity counts, score)
- Per-file findings grouped by severity
- Code snippets with diff-style highlights
- Suggested fixes inline
- Agent attribution
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from pathlib import Path

from ..agents.base import Finding, Severity

# Severity icons
_ICONS = {
    Severity.CRITICAL: "🔴",
    Severity.WARNING: "🟡",
    Severity.INFO: "🔵",
}

_SEVERITY_LABELS = {
    Severity.CRITICAL: "CRITICAL",
    Severity.WARNING: "WARNING",
    Severity.INFO: "INFO",
}


class MarkdownReportGenerator:
    """Generates a comprehensive Markdown review artifact."""

    def generate(
        self,
        findings: list[Finding],
        summary: dict,
        job_id: str,
        plan_rationale: str = "",
        duration_ms: float = 0.0,
    ) -> str:
        sections: list[str] = []

        sections.append(self._header(job_id, summary, plan_rationale, duration_ms))
        sections.append(self._executive_summary(summary))
        sections.append(self._severity_score(summary))

        if not findings:
            sections.append("\n## ✅ No Issues Found\n\nThe code looks clean! Great work.\n")
            return "\n".join(sections)

        sections.append(self._findings_by_file(findings))
        sections.append(self._findings_by_agent(findings))
        sections.append(self._footer())

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _header(
        self, job_id: str, summary: dict, rationale: str, duration_ms: float
    ) -> str:
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
        total = summary.get("total", 0)
        lines = [
            "# 🤖 AI Code Review Report",
            "",
            f"**Job ID:** `{job_id}`  ",
            f"**Generated:** {ts}  ",
            f"**Duration:** {duration_ms:.0f}ms  ",
            f"**Total findings:** {total}  ",
        ]
        if rationale:
            lines += ["", f"> {rationale}", ""]
        lines.append("---")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Executive Summary Table
    # ------------------------------------------------------------------

    def _executive_summary(self, summary: dict) -> str:
        critical = summary.get("critical", 0)
        warning = summary.get("warning", 0)
        info = summary.get("info", 0)

        lines = [
            "## 📊 Summary",
            "",
            "| Severity | Count |",
            "|----------|-------|",
            f"| 🔴 Critical | **{critical}** |",
            f"| 🟡 Warning  | {warning} |",
            f"| 🔵 Info     | {info} |",
            f"| **Total**   | **{critical + warning + info}** |",
            "",
        ]

        # Per-agent breakdown
        by_agent = summary.get("by_agent", {})
        if by_agent:
            lines += [
                "**Findings by agent:**",
                "",
                "| Agent | Findings |",
                "|-------|----------|",
            ]
            agent_labels = {
                "static_analysis": "Static Analysis",
                "security_review": "Security Review",
                "logic_review": "Logic Review",
                "style_and_perf": "Style & Performance",
            }
            for agent, count in sorted(by_agent.items(), key=lambda x: -x[1]):
                label = agent_labels.get(agent, agent)
                lines.append(f"| {label} | {count} |")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Severity Score
    # ------------------------------------------------------------------

    def _severity_score(self, summary: dict) -> str:
        critical = summary.get("critical", 0)
        warning = summary.get("warning", 0)
        info = summary.get("info", 0)

        # Score: 100 - (critical*15 + warning*5 + info*1), clamped 0-100
        raw_score = 100 - (critical * 15 + warning * 5 + info * 1)
        score = max(0, min(100, raw_score))

        if score >= 90:
            grade, emoji = "A", "🟢"
        elif score >= 75:
            grade, emoji = "B", "🟡"
        elif score >= 60:
            grade, emoji = "C", "🟠"
        elif score >= 40:
            grade, emoji = "D", "🔴"
        else:
            grade, emoji = "F", "💀"

        return "\n".join([
            f"## {emoji} Code Health Score: {score}/100 (Grade: {grade})",
            "",
            f"> {'Excellent! The code is in great shape.' if score >= 90 else 'Review and address the findings below to improve code quality.'}",
            "",
            "---",
        ])

    # ------------------------------------------------------------------
    # Findings by File
    # ------------------------------------------------------------------

    def _findings_by_file(self, findings: list[Finding]) -> str:
        # Group by filename
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            by_file[f.filename].append(f)

        sections: list[str] = ["## 📁 Findings by File", ""]

        for filename, file_findings in sorted(by_file.items()):
            critical_count = sum(1 for f in file_findings if f.severity == Severity.CRITICAL)
            warning_count = sum(1 for f in file_findings if f.severity == Severity.WARNING)
            info_count = sum(1 for f in file_findings if f.severity == Severity.INFO)

            badge = ""
            if critical_count:
                badge += f" 🔴 {critical_count}"
            if warning_count:
                badge += f" 🟡 {warning_count}"
            if info_count:
                badge += f" 🔵 {info_count}"

            sections.append(f"### `{Path(filename).name}`{badge}")
            sections.append(f"**Full path:** `{filename}`")
            sections.append("")

            for finding in file_findings:
                sections.append(self._format_finding(finding))

        return "\n".join(sections)

    def _format_finding(self, finding: Finding) -> str:
        icon = _ICONS.get(finding.severity, "⚪")
        label = _SEVERITY_LABELS.get(finding.severity, "INFO")
        confidence_pct = int(finding.confidence * 100)

        lines = [
            f"#### {icon} [{label}] {finding.title}",
            "",
            f"**Rule:** `{finding.rule_id}`  ",
            f"**Agent:** `{finding.agent.value}`  ",
        ]

        if finding.line_start > 0:
            loc = f"Line {finding.line_start}"
            if finding.line_end and finding.line_end != finding.line_start:
                loc += f"–{finding.line_end}"
            lines.append(f"**Location:** {loc}  ")

        if finding.confidence < 1.0:
            lines.append(f"**Confidence:** {confidence_pct}%  ")

        lines.append("")
        lines.append(finding.description)

        if finding.code_snippet:
            lang = "python" if ".py" in finding.filename else "text"
            lines += [
                "",
                f"```{lang}",
                finding.code_snippet.strip(),
                "```",
            ]

        if finding.suggestion:
            lines += [
                "",
                "**💡 Suggestion:**",
                "",
                finding.suggestion,
            ]

        if finding.tags:
            tag_str = " ".join(f"`{t}`" for t in finding.tags if t)
            lines += ["", f"**Tags:** {tag_str}"]

        lines.append("")
        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Findings by Agent
    # ------------------------------------------------------------------

    def _findings_by_agent(self, findings: list[Finding]) -> str:
        by_agent: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            by_agent[f.agent.value].append(f)

        lines = ["## 🤖 Findings by Agent", ""]

        agent_labels = {
            "static_analysis": "🔧 Static Analysis",
            "security_review": "🔒 Security Review",
            "logic_review": "🧠 Logic Review",
            "style_and_perf": "✨ Style & Performance",
        }

        for agent, agent_findings in sorted(by_agent.items()):
            label = agent_labels.get(agent, agent)
            lines.append(f"### {label} ({len(agent_findings)} findings)")
            lines.append("")

            for f in agent_findings[:5]:  # Show top 5 per agent in this section
                icon = _ICONS.get(f.severity, "⚪")
                loc = f" (line {f.line_start})" if f.line_start else ""
                lines.append(f"- {icon} **{f.title}**{loc} — `{f.filename}`")

            if len(agent_findings) > 5:
                lines.append(f"- *...and {len(agent_findings) - 5} more (see full list above)*")

            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _footer(self) -> str:
        return "\n".join([
            "---",
            "",
            "*Generated by [AI Code Reviewer](https://github.com/code-reviewer) —*",
            "*Powered by Google Gemini · Multi-agent pipeline*"
        ])

    # ------------------------------------------------------------------
    # Save to file
    # ------------------------------------------------------------------

    def save(self, content: str, output_path: str) -> str:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)
