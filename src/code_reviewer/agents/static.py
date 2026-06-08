"""
Static Analysis Agent — Lint, AST scan, dead-code detection.

Runs ruff (fast Python linter) as a subprocess and converts its JSON
output into structured Finding objects. Also performs AST-based checks
for unused imports and shadowed variables using data from the Context Agent.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import (
    AgentType,
    BaseAgent,
    CodeFile,
    Finding,
    Language,
    ReviewContext,
    Severity,
)

# Ruff severity mapping
_RUFF_CODE_SEVERITY: dict[str, Severity] = {
    "E": Severity.WARNING,   # pycodestyle errors
    "W": Severity.INFO,      # pycodestyle warnings
    "F": Severity.WARNING,   # pyflakes
    "N": Severity.INFO,      # pep8-naming
    "C": Severity.WARNING,   # mccabe complexity
    "B": Severity.WARNING,   # flake8-bugbear
    "S": Severity.CRITICAL,  # bandit security (ruff mirrors)
    "UP": Severity.INFO,     # pyupgrade
    "ANN": Severity.INFO,    # flake8-annotations
}


def _ruff_severity(code: str) -> Severity:
    for prefix, sev in _RUFF_CODE_SEVERITY.items():
        if code.startswith(prefix):
            return sev
    return Severity.INFO


class StaticAnalysisAgent(BaseAgent):
    """
    Runs ruff on Python files and produces lint findings.
    Falls back to regex-based checks for non-Python files.
    """

    agent_type = AgentType.STATIC

    async def run(self, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []

        for code_file in context.files:
            if code_file.language == Language.PYTHON and not code_file.is_diff:
                findings.extend(await self._run_ruff(code_file))
                findings.extend(self._check_complexity(code_file, context))
                findings.extend(self._check_dead_code(code_file, context))
            else:
                findings.extend(self._generic_checks(code_file))

        return findings

    # ------------------------------------------------------------------
    # Ruff runner
    # ------------------------------------------------------------------

    async def _run_ruff(self, code_file: CodeFile) -> list[Finding]:
        """Run ruff with JSON output on a temp file."""
        findings: list[Finding] = []

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(code_file.content)
            tmp_path = tmp.name

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable, "-m", "ruff", "check",
                    "--output-format=json",
                    "--select=ALL",
                    "--ignore=D,ERA,T20",   # skip docstring/commented-out-code rules (handled by LLM agents)
                    tmp_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            try:
                issues = json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                return findings

            for issue in issues:
                code = issue.get("code", "UNKNOWN")
                location = issue.get("location", {})
                end_location = issue.get("end_location", location)
                findings.append(
                    self.make_finding(
                        rule_id=f"RUFF-{code}",
                        title=f"[{code}] {issue.get('message', '')}",
                        description=issue.get("message", ""),
                        severity=_ruff_severity(code),
                        filename=code_file.filename,
                        line_start=location.get("row", 0),
                        line_end=end_location.get("row", 0),
                        code_snippet=self._get_snippet(code_file.content, location.get("row", 0)),
                        suggestion=issue.get("fix", {}).get("message", "") if issue.get("fix") else "",
                        tags=["lint", "static"],
                    )
                )
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            # ruff not installed — skip silently, will be noted in summary
            findings.append(
                self.make_finding(
                    rule_id="STATIC-RUFF-UNAVAILABLE",
                    title="ruff linter not found",
                    description="Install ruff for detailed lint analysis: `pip install ruff`",
                    severity=Severity.INFO,
                    filename=code_file.filename,
                    tags=["tooling"],
                )
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return findings

    # ------------------------------------------------------------------
    # AST-based complexity checks
    # ------------------------------------------------------------------

    def _check_complexity(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []
        ast_info = context.ast_info.get(code_file.filename)
        if not ast_info:
            return findings

        max_complexity = self.rules.get("complexity", {}).get("max_cyclomatic_complexity", 10)
        max_params = self.rules.get("complexity", {}).get("max_parameters", 6)
        max_fn_lines = self.rules.get("complexity", {}).get("max_function_length_lines", 50)

        for fn in ast_info.functions:
            # Cyclomatic complexity
            if fn["complexity"] > max_complexity:
                findings.append(
                    self.make_finding(
                        rule_id="STATIC-COMPLEXITY",
                        title=f"Function `{fn['name']}` has high cyclomatic complexity ({fn['complexity']})",
                        description=(
                            f"Cyclomatic complexity of {fn['complexity']} exceeds the threshold of {max_complexity}. "
                            "Consider splitting into smaller functions."
                        ),
                        severity=Severity.WARNING,
                        filename=code_file.filename,
                        line_start=fn["line_start"],
                        line_end=fn["line_end"],
                        code_snippet=self._get_snippet(code_file.content, fn["line_start"]),
                        tags=["complexity", "static"],
                    )
                )

            # Parameter count
            if len(fn["args"]) > max_params:
                findings.append(
                    self.make_finding(
                        rule_id="STATIC-TOO-MANY-PARAMS",
                        title=f"Function `{fn['name']}` has {len(fn['args'])} parameters (max {max_params})",
                        description="Too many parameters makes functions hard to call and test. Consider grouping into a dataclass.",
                        severity=Severity.WARNING,
                        filename=code_file.filename,
                        line_start=fn["line_start"],
                        code_snippet=self._get_snippet(code_file.content, fn["line_start"]),
                        tags=["complexity", "static"],
                    )
                )

            # Function length
            fn_length = fn["line_end"] - fn["line_start"]
            if fn_length > max_fn_lines:
                findings.append(
                    self.make_finding(
                        rule_id="STATIC-LONG-FUNCTION",
                        title=f"Function `{fn['name']}` is {fn_length} lines long (max {max_fn_lines})",
                        description="Long functions are harder to read, test, and maintain. Consider refactoring.",
                        severity=Severity.INFO,
                        filename=code_file.filename,
                        line_start=fn["line_start"],
                        line_end=fn["line_end"],
                        tags=["complexity", "static"],
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Dead-code / unused import checks
    # ------------------------------------------------------------------

    def _check_dead_code(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        """Flag imports that appear to be unused (simple heuristic)."""
        findings: list[Finding] = []
        ast_info = context.ast_info.get(code_file.filename)
        if not ast_info:
            return findings

        lines = code_file.content.splitlines()
        for imp in ast_info.imports:
            # Get the leaf name (e.g., "os" from "os.path")
            leaf = imp.split(".")[-1]
            # Check if it appears in non-import lines
            usage = sum(
                1 for i, line in enumerate(lines)
                if re.search(rf'\b{re.escape(leaf)}\b', line)
                and not line.strip().startswith(("import ", "from "))
            )
            if usage == 0:
                # Find the line number of the import
                import_line = next(
                    (i + 1 for i, line in enumerate(lines)
                     if leaf in line and line.strip().startswith(("import ", "from "))),
                    0
                )
                findings.append(
                    self.make_finding(
                        rule_id="STATIC-UNUSED-IMPORT",
                        title=f"Unused import: `{imp}`",
                        description=f"The import `{imp}` does not appear to be used anywhere in the file.",
                        severity=Severity.INFO,
                        filename=code_file.filename,
                        line_start=import_line,
                        code_snippet=lines[import_line - 1] if import_line > 0 else "",
                        suggestion=f"Remove `import {imp}` or use `# noqa: F401` if intentional.",
                        tags=["unused", "static"],
                    )
                )
        return findings

    # ------------------------------------------------------------------
    # Generic checks for non-Python
    # ------------------------------------------------------------------

    def _generic_checks(self, code_file: CodeFile) -> list[Finding]:
        findings: list[Finding] = []
        max_len = self.rules.get("general", {}).get("max_line_length", 100)
        lines = code_file.content.splitlines()
        for i, line in enumerate(lines, 1):
            if len(line) > max_len:
                findings.append(
                    self.make_finding(
                        rule_id="STATIC-LINE-TOO-LONG",
                        title=f"Line {i} exceeds {max_len} characters ({len(line)})",
                        description=f"Line length {len(line)} exceeds configured max of {max_len}.",
                        severity=Severity.INFO,
                        filename=code_file.filename,
                        line_start=i,
                        code_snippet=line[:120],
                        tags=["style", "static"],
                    )
                )
        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_snippet(content: str, line_num: int, context_lines: int = 2) -> str:
        lines = content.splitlines()
        start = max(0, line_num - context_lines - 1)
        end = min(len(lines), line_num + context_lines)
        return "\n".join(lines[start:end])
