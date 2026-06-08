"""
Style & Performance Agent — Naming conventions, complexity hints, time/space analysis.

Uses a combination of:
1. Rules-config-based naming convention checks (regex)
2. LLM-driven style and performance review
"""

from __future__ import annotations

import json
import re

from .base import (
    AgentType,
    ASTInfo,
    BaseAgent,
    CodeFile,
    Finding,
    Language,
    ReviewContext,
    Severity,
)

# Naming convention patterns
_NAMING_PATTERNS: dict[str, str] = {
    "snake_case": r"^[a-z][a-z0-9_]*$",
    "camelCase": r"^[a-z][a-zA-Z0-9]*$",
    "PascalCase": r"^[A-Z][a-zA-Z0-9]*$",
    "UPPER_SNAKE_CASE": r"^[A-Z][A-Z0-9_]*$",
}


class StyleAndPerfAgent(BaseAgent):
    """Reviews code for naming conventions, style issues, and performance hints."""

    agent_type = AgentType.STYLE

    SYSTEM_PROMPT = (
        "You are a principal engineer specializing in code quality, readability, "
        "and performance optimization. You give precise, actionable feedback "
        "with specific line numbers and concrete improvement suggestions."
    )

    async def run(self, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []

        for code_file in context.files:
            # Naming convention checks (fast, rule-based)
            if code_file.language == Language.PYTHON:
                findings.extend(self._check_naming(code_file, context))

            # LLM style & performance review
            findings.extend(await self._llm_style_review(code_file, context))

        return findings

    # ------------------------------------------------------------------
    # Naming Convention Checks
    # ------------------------------------------------------------------

    def _check_naming(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []
        ast_info: ASTInfo | None = context.ast_info.get(code_file.filename)
        if not ast_info:
            return findings

        naming_rules = self.rules.get("naming", {})
        fn_convention = naming_rules.get("functions", "snake_case")
        cls_convention = naming_rules.get("classes", "PascalCase")
        const_convention = naming_rules.get("constants", "UPPER_SNAKE_CASE")

        fn_pattern = _NAMING_PATTERNS.get(fn_convention, _NAMING_PATTERNS["snake_case"])
        cls_pattern = _NAMING_PATTERNS.get(cls_convention, _NAMING_PATTERNS["PascalCase"])
        const_pattern = _NAMING_PATTERNS.get(const_convention, _NAMING_PATTERNS["UPPER_SNAKE_CASE"])

        # Check function names
        for fn in ast_info.functions:
            name = fn["name"]
            # Skip dunder methods and private
            if name.startswith("__") and name.endswith("__"):
                continue
            clean_name = name.lstrip("_")
            if clean_name and not re.match(fn_pattern, clean_name):
                findings.append(
                    self.make_finding(
                        rule_id="STYLE-NAMING-FUNCTION",
                        title=f"Function `{name}` does not follow {fn_convention} convention",
                        description=(
                            f"Your team's naming convention for functions is `{fn_convention}`. "
                            f"The name `{name}` does not match pattern `{fn_pattern}`."
                        ),
                        severity=Severity.INFO,
                        filename=code_file.filename,
                        line_start=fn["line_start"],
                        code_snippet=f"def {name}(...):",
                        suggestion=f"Rename to `{self._to_convention(name, fn_convention)}`",
                        tags=["style", "naming"],
                    )
                )

        # Check class names
        for cls in ast_info.classes:
            name = cls["name"]
            if not re.match(cls_pattern, name):
                findings.append(
                    self.make_finding(
                        rule_id="STYLE-NAMING-CLASS",
                        title=f"Class `{name}` does not follow {cls_convention} convention",
                        description=(
                            f"Your team's naming convention for classes is `{cls_convention}`. "
                            f"The name `{name}` does not match pattern `{cls_pattern}`."
                        ),
                        severity=Severity.INFO,
                        filename=code_file.filename,
                        line_start=cls["line_start"],
                        code_snippet=f"class {name}:",
                        suggestion=f"Rename to `{self._to_convention(name, cls_convention)}`",
                        tags=["style", "naming"],
                    )
                )

        # Check for ALL_CAPS constants (any top-level assignment that isn't ALL_CAPS)
        if const_convention == "UPPER_SNAKE_CASE":
            lines = code_file.content.splitlines()
            for i, line in enumerate(lines, 1):
                # Detect module-level constants (heuristic: ALL_CAPS = ...)
                match = re.match(r'^([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\s*=', line)
                if match:
                    const_name = match.group(1)
                    if not re.match(const_pattern, const_name):
                        findings.append(
                            self.make_finding(
                                rule_id="STYLE-NAMING-CONSTANT",
                                title=f"Constant `{const_name}` should be {const_convention}",
                                description=f"Constants should follow {const_convention} naming convention.",
                                severity=Severity.INFO,
                                filename=code_file.filename,
                                line_start=i,
                                code_snippet=line.strip(),
                                tags=["style", "naming"],
                            )
                        )

        return findings

    # ------------------------------------------------------------------
    # LLM Style & Performance Review
    # ------------------------------------------------------------------

    async def _llm_style_review(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        content = code_file.content
        if len(content) < 20:
            return []
        if len(content) > 12_000:
            content = content[:12_000] + "\n... (truncated)"

        # Include docstring requirement from rules
        require_docs = self.rules.get("style", {}).get("require_docstrings", True)
        warn_globals = self.rules.get("style", {}).get("warn_on_global_state", True)

        prompt = f"""Review the following code for style and performance issues.

Focus on:
1. **Readability** — confusing variable names, magic numbers, overly dense expressions
2. **Docstrings** — public functions/classes missing docstrings (required: {require_docs})
3. **Performance** — O(n²) nested loops where O(n) is possible, repeated expensive calls, unnecessary copies
4. **Global state** — mutable module-level state, global variables (warn: {warn_globals})
5. **Pythonic style** — using `range(len(x))` instead of `enumerate`, not using list comprehensions, etc.
6. **DRY violations** — repeated code blocks that should be extracted
7. **Magic numbers** — unexplained numeric literals that should be named constants
8. **Deprecated patterns** — old-style string formatting, old-style class definitions

File: {code_file.filename}
Language: {code_file.language.value}

```
{content}
```

Respond ONLY with a JSON array of findings:
{{
  "rule_id": "STYLE-<TYPE>",
  "title": "concise title",
  "description": "detailed explanation",
  "severity": "warning|info",
  "line_start": <integer>,
  "line_end": <integer>,
  "code_snippet": "the relevant code",
  "suggestion": "improved version with example"
}}

Valid rule_id types: MISSING-DOCSTRING, PERFORMANCE, GLOBAL-STATE, NOT-PYTHONIC,
DRY-VIOLATION, MAGIC-NUMBER, DEPRECATED, READABILITY, OTHER

Only flag genuine issues. Return [] if the code is well-written.
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
                    rule_id=f"STYLE-{issue.get('rule_id', 'OTHER').replace('STYLE-', '')}",
                    title=issue.get("title", "Style issue"),
                    description=issue.get("description", ""),
                    severity=severity,
                    filename=code_file.filename,
                    line_start=issue.get("line_start", 0),
                    line_end=issue.get("line_end", 0),
                    code_snippet=issue.get("code_snippet", ""),
                    suggestion=issue.get("suggestion", ""),
                    confidence=0.8,
                    tags=["style", "performance"],
                )
            )

        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_convention(name: str, convention: str) -> str:
        """Best-effort name conversion."""
        # Split on non-alphanumeric
        words = re.split(r'[^a-zA-Z0-9]', name)
        words = [w for w in words if w]
        if convention == "snake_case":
            return "_".join(w.lower() for w in words)
        elif convention == "camelCase":
            return words[0].lower() + "".join(w.capitalize() for w in words[1:])
        elif convention == "PascalCase":
            return "".join(w.capitalize() for w in words)
        elif convention == "UPPER_SNAKE_CASE":
            return "_".join(w.upper() for w in words)
        return name
