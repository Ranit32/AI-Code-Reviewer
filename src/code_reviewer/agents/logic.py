"""
Logic Review Agent — LLM-driven bug detection, null checks, edge cases.

Uses the LLM to reason about:
- Off-by-one errors
- Null / None dereferences
- Unreachable code paths
- Incorrect algorithm logic
- Missing error handling
- Edge case blindspots
"""

from __future__ import annotations

import json
import re

from .base import (
    AgentType,
    BaseAgent,
    CodeFile,
    Finding,
    ReviewContext,
    Severity,
)


class LogicReviewAgent(BaseAgent):
    """LLM-driven logic and correctness review."""

    agent_type = AgentType.LOGIC

    SYSTEM_PROMPT = (
        "You are a senior software engineer with 15+ years of experience "
        "reviewing code for correctness, edge cases, and logical bugs. "
        "You are thorough, precise, and always reference exact line numbers."
    )

    async def run(self, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []

        for code_file in context.files:
            findings.extend(await self._review_file(code_file, context))

        return findings

    async def _review_file(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        if len(code_file.content.strip()) < 10:
            return []

        # Build context summary for the LLM
        ast_info = context.ast_info.get(code_file.filename)
        context_summary = ""
        if ast_info:
            fn_names = [f["name"] for f in ast_info.functions[:10]]
            cls_names = [c["name"] for c in ast_info.classes[:5]]
            context_summary = (
                f"Functions defined: {', '.join(fn_names)}\n"
                f"Classes defined: {', '.join(cls_names)}\n"
                f"Imports: {', '.join(ast_info.imports[:15])}\n"
            )

        content = code_file.content
        if len(content) > 15_000:
            content = content[:15_000] + "\n... (truncated)"

        prompt = f"""Perform a deep logic and correctness review of the following code.

Focus on:
1. **Off-by-one errors** — loop bounds, index access, slice ranges
2. **Null / None dereferences** — accessing attributes/methods on potentially None values
3. **Missing error handling** — uncaught exceptions, missing finally blocks
4. **Incorrect algorithm logic** — wrong conditions, wrong operator, wrong data structure usage
5. **Edge cases** — empty input, zero, negative numbers, empty lists/dicts
6. **Unreachable code** — code after return/break/continue/raise
7. **Race conditions** — shared state without locking
8. **Type errors** — calling methods on wrong types, comparing incompatible types
9. **Resource leaks** — files/connections opened but not closed

File: {code_file.filename}
Language: {code_file.language.value}
{context_summary}

```
{content}
```

Respond ONLY with a JSON array of findings. Each finding must have:
{{
  "rule_id": "LOGIC-<TYPE>",
  "title": "concise title",
  "description": "detailed explanation of the bug or risk",
  "severity": "critical|warning|info",
  "line_start": <integer>,
  "line_end": <integer>,
  "code_snippet": "the problematic code",
  "suggestion": "how to fix it with example code"
}}

Valid rule_id types: OFF-BY-ONE, NULL-DEREF, MISSING-ERROR-HANDLING, WRONG-LOGIC,
EDGE-CASE, UNREACHABLE-CODE, RACE-CONDITION, TYPE-ERROR, RESOURCE-LEAK, OTHER

Only report genuine issues with high confidence. If no issues found, return [].
Do not include any text outside the JSON array."""

        try:
            response = await self.ask_llm(prompt, system=self.SYSTEM_PROMPT)
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                return []
            issues = json.loads(json_match.group(0))
        except (json.JSONDecodeError, Exception):
            return []

        findings: list[Finding] = []
        for issue in issues:
            sev_map = {
                "critical": Severity.CRITICAL,
                "warning": Severity.WARNING,
                "info": Severity.INFO,
            }
            severity = sev_map.get(issue.get("severity", "info").lower(), Severity.INFO)

            findings.append(
                self.make_finding(
                    rule_id=f"LOGIC-{issue.get('rule_id', 'OTHER').replace('LOGIC-', '')}",
                    title=issue.get("title", "Logic issue"),
                    description=issue.get("description", ""),
                    severity=severity,
                    filename=code_file.filename,
                    line_start=issue.get("line_start", 0),
                    line_end=issue.get("line_end", 0),
                    code_snippet=issue.get("code_snippet", ""),
                    suggestion=issue.get("suggestion", ""),
                    confidence=0.8,
                    tags=["logic", "correctness"],
                )
            )

        return findings
