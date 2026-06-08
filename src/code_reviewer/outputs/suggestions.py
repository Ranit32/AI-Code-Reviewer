"""
Inline Suggestions — Builds GitHub/GitLab PR review comment payloads.

Converts findings into GitHub Review API-compatible comment objects
and optionally posts them directly via the GitHub REST API.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

import httpx

from ..agents.base import Finding, Severity


@dataclass
class PRComment:
    """A single inline PR comment in GitHub Review API format."""
    path: str
    line: int
    body: str
    side: str = "RIGHT"


def findings_to_pr_comments(findings: list[Finding]) -> list[PRComment]:
    """Convert findings into GitHub PR inline comment objects."""
    comments: list[PRComment] = []

    for finding in findings:
        if finding.line_start == 0:
            continue  # Skip file-level findings (no line number)

        severity_prefix = {
            Severity.CRITICAL: "🔴 **CRITICAL**",
            Severity.WARNING: "🟡 **WARNING**",
            Severity.INFO: "🔵 **INFO**",
        }.get(finding.severity, "⚪")

        body_lines = [
            f"{severity_prefix}: {finding.title}",
            "",
            finding.description,
        ]

        if finding.suggestion:
            body_lines += ["", f"**💡 Suggestion:** {finding.suggestion}"]

        body_lines += ["", f"*Rule: `{finding.rule_id}` · Agent: `{finding.agent.value}`*"]

        comments.append(
            PRComment(
                path=finding.filename,
                line=finding.line_start,
                body="\n".join(body_lines),
            )
        )

    return comments


async def post_github_review(
    owner: str,
    repo: str,
    pull_number: int,
    commit_sha: str,
    findings: list[Finding],
    token: str | None = None,
) -> dict:
    """
    Post findings as a GitHub Pull Request review with inline comments.
    Requires a GitHub token with `pull_requests` write permission.
    """
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("GitHub token required. Set GITHUB_TOKEN env var.")

    comments = findings_to_pr_comments(findings)

    # Build review body summary
    critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
    warning = sum(1 for f in findings if f.severity == Severity.WARNING)
    info = sum(1 for f in findings if f.severity == Severity.INFO)

    review_body = (
        f"## 🤖 AI Code Review\n\n"
        f"🔴 {critical} critical · 🟡 {warning} warnings · 🔵 {info} info\n\n"
        f"*Automated review by AI Code Reviewer*"
    )

    event = "REQUEST_CHANGES" if critical > 0 else ("COMMENT" if warning > 0 else "APPROVE")

    payload = {
        "commit_id": commit_sha,
        "body": review_body,
        "event": event,
        "comments": [
            {
                "path": c.path,
                "line": c.line,
                "side": c.side,
                "body": c.body,
            }
            for c in comments[:50]  # GitHub limits inline comments
        ],
    }

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=30.0)
        response.raise_for_status()
        return response.json()


def comments_to_json(comments: list[PRComment]) -> str:
    """Serialize PR comments to JSON string."""
    return json.dumps([asdict(c) for c in comments], indent=2)
