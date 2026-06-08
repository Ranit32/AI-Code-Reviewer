"""
Security Review Agent — OWASP Top 10, secret detection, CVE hints.

Uses a combination of:
1. bandit (Python-specific security linter via subprocess)
2. Regex-based secret / API key detection
3. LLM-driven OWASP Top 10 analysis
"""

from __future__ import annotations

import asyncio
import json
import math
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

# ---------------------------------------------------------------------------
# OWASP categories for LLM prompting
# ---------------------------------------------------------------------------

OWASP_TOP10 = [
    "A01 Broken Access Control",
    "A02 Cryptographic Failures",
    "A03 Injection (SQL, Command, LDAP, XPath)",
    "A04 Insecure Design",
    "A05 Security Misconfiguration",
    "A06 Vulnerable and Outdated Components",
    "A07 Identification and Authentication Failures",
    "A08 Software and Data Integrity Failures",
    "A09 Security Logging and Monitoring Failures",
    "A10 Server-Side Request Forgery (SSRF)",
]

# Bandit severity → Finding severity
_BANDIT_SEV = {
    "HIGH": Severity.CRITICAL,
    "MEDIUM": Severity.WARNING,
    "LOW": Severity.INFO,
}


class SecurityReviewAgent(BaseAgent):
    """Runs bandit + secret detection + LLM OWASP review."""

    agent_type = AgentType.SECURITY

    async def run(self, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []

        tasks = []
        for code_file in context.files:
            tasks.append(self._review_file(code_file, context))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                findings.extend(result)

        return findings

    async def _review_file(self, code_file: CodeFile, context: ReviewContext) -> list[Finding]:
        findings: list[Finding] = []

        # 1. Secret detection (fast, regex-based — always run)
        findings.extend(self._detect_secrets(code_file))

        # 2. Banned function/pattern scan
        findings.extend(self._check_banned(code_file))

        # 3. bandit (Python only)
        if code_file.language == Language.PYTHON and not code_file.is_diff:
            findings.extend(await self._run_bandit(code_file))

        # 4. LLM OWASP review (if enabled in rules)
        owasp_enabled = self.rules.get("security", {}).get("owasp_checks", {})
        if any(owasp_enabled.values()) if isinstance(owasp_enabled, dict) else True:
            findings.extend(await self._llm_owasp_review(code_file))

        return findings

    # ------------------------------------------------------------------
    # Secret Detection
    # ------------------------------------------------------------------

    def _detect_secrets(self, code_file: CodeFile) -> list[Finding]:
        findings: list[Finding] = []
        patterns = self.rules.get("security", {}).get("secret_patterns", [])

        # Always include these baseline patterns
        baseline_patterns = [
            r"(?i)(api[_-]?key|secret|password|token|auth)['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{8,}",
            r"(?i)aws_access_key_id\s*=\s*['\"]?[A-Z0-9]{20}",
            r"(?i)-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
            r"(?i)(AKIA|AIPA|ASIA|AROA|ANPA|ANVA|APKA)[A-Z0-9]{16}",  # AWS key ID
            r"ghp_[A-Za-z0-9]{36}",   # GitHub personal access token
            r"glpat-[A-Za-z0-9\-_]{20}",  # GitLab PAT
            r"sk-[A-Za-z0-9]{48}",    # OpenAI API key
        ]

        all_patterns = baseline_patterns + (patterns if isinstance(patterns, list) else [])
        lines = code_file.content.splitlines()

        for line_num, line in enumerate(lines, 1):
            for pattern in all_patterns:
                if re.search(pattern, line):
                    # Check Shannon entropy to reduce false positives
                    match = re.search(pattern, line)
                    if match and self._shannon_entropy(match.group(0)) > 3.5:
                        findings.append(
                            self.make_finding(
                                rule_id="SEC-HARDCODED-SECRET",
                                title="Hardcoded secret or credential detected",
                                description=(
                                    "A potential hardcoded secret, API key, or credential was found. "
                                    "Secrets should never be committed to source code."
                                ),
                                severity=Severity.CRITICAL,
                                filename=code_file.filename,
                                line_start=line_num,
                                code_snippet=re.sub(r'(["\'])[A-Za-z0-9+/=_\-]{4}', r'\1****', line),
                                suggestion="Move this value to an environment variable or secrets manager.",
                                tags=["secret", "security", "owasp-a02"],
                                confidence=0.85,
                            )
                        )
                        break  # One finding per line max

        return findings

    @staticmethod
    def _shannon_entropy(s: str) -> float:
        """Calculate Shannon entropy of a string."""
        if not s:
            return 0.0
        freq = {}
        for c in s:
            freq[c] = freq.get(c, 0) + 1
        length = len(s)
        return -sum((count / length) * math.log2(count / length) for count in freq.values())

    # ------------------------------------------------------------------
    # Banned functions / patterns
    # ------------------------------------------------------------------

    def _check_banned(self, code_file: CodeFile) -> list[Finding]:
        findings: list[Finding] = []
        lang_key = code_file.language.value
        banned = self.rules.get("security", {}).get("banned", {}).get(lang_key, [])
        lines = code_file.content.splitlines()

        # Track multi-line string state (docstrings)
        in_docstring = False
        docstring_char = None

        for line_num, line in enumerate(lines, 1):
            clean_line = ""
            in_quote = False
            quote_char = None
            
            idx = 0
            while idx < len(line):
                char = line[idx]
                
                # Check for triple quotes (docstrings)
                if not in_quote and not in_docstring:
                    if line[idx:idx+3] in ('"""', "'''"):
                        in_docstring = True
                        docstring_char = line[idx:idx+3]
                        idx += 3
                        continue
                elif in_docstring:
                    if line[idx:idx+3] == docstring_char:
                        in_docstring = False
                        docstring_char = None
                        idx += 3
                    else:
                        idx += 1
                    continue
                
                # Check for single/double quotes and comments
                if not in_docstring:
                    if char in ('"', "'"):
                        if not in_quote:
                            in_quote = True
                            quote_char = char
                        elif quote_char == char:
                            in_quote = False
                            quote_char = None
                    elif char == "#" and not in_quote:
                        break
                
                if not in_quote and not in_docstring:
                    clean_line += char
                
                idx += 1

            # Match with word boundaries
            for banned_pattern in banned:
                if re.search(rf"\b{re.escape(banned_pattern)}\b", clean_line):
                    findings.append(
                        self.make_finding(
                            rule_id="SEC-BANNED-USAGE",
                            title=f"Use of banned function/pattern: `{banned_pattern}`",
                            description=(
                                f"`{banned_pattern}` is banned by your team's security rules. "
                                "It may allow arbitrary code execution or unsafe deserialization."
                            ),
                            severity=Severity.CRITICAL,
                            filename=code_file.filename,
                            line_start=line_num,
                            code_snippet=line.strip(),
                            tags=["security", "banned"],
                        )
                    )

        return findings

    # ------------------------------------------------------------------
    # Bandit
    # ------------------------------------------------------------------

    async def _run_bandit(self, code_file: CodeFile) -> list[Finding]:
        findings: list[Finding] = []

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(code_file.content)
            tmp_path = tmp.name

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "bandit", "-f", "json", "-q", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            try:
                data = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                return findings

            for issue in data.get("results", []):
                findings.append(
                    self.make_finding(
                        rule_id=f"BANDIT-{issue.get('test_id', 'UNKNOWN')}",
                        title=issue.get("issue_text", "Security issue"),
                        description=(
                            f"{issue.get('issue_text', '')}. "
                            f"More info: {issue.get('more_info', '')}"
                        ),
                        severity=_BANDIT_SEV.get(
                            issue.get("issue_severity", "LOW"), Severity.INFO
                        ),
                        filename=code_file.filename,
                        line_start=issue.get("line_number", 0),
                        line_end=issue.get("line_range", [0])[-1],
                        code_snippet=issue.get("code", ""),
                        tags=["security", "bandit", issue.get("test_name", "")],
                        confidence={"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}.get(
                            issue.get("issue_confidence", "LOW"), 0.5
                        ),
                    )
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return findings

    # ------------------------------------------------------------------
    # LLM OWASP Review
    # ------------------------------------------------------------------

    async def _llm_owasp_review(self, code_file: CodeFile) -> list[Finding]:
        """Ask the LLM to perform an OWASP-focused security review."""
        if len(code_file.content) > 20_000:
            # Trim very large files to first 20k chars
            content = code_file.content[:20_000] + "\n... (truncated)"
        else:
            content = code_file.content

        owasp_list = "\n".join(f"- {o}" for o in OWASP_TOP10)

        prompt = f"""You are an expert application security engineer performing a code security review.

Analyze the following code for security vulnerabilities, focusing on the OWASP Top 10:
{owasp_list}

Also look for:
- Injection vulnerabilities (SQL, command, LDAP)
- Insecure direct object references
- Missing input validation
- Unsafe deserialization
- Path traversal vulnerabilities
- Race conditions
- Insecure cryptography usage

File: {code_file.filename}
Language: {code_file.language.value}

```
{content}
```

Respond ONLY with a JSON array. Each item must have:
{{
  "rule_id": "SEC-OWASP-Axx",
  "title": "short title",
  "description": "detailed explanation of the vulnerability",
  "severity": "critical|warning|info",
  "line_start": <line number or 0>,
  "line_end": <line number or 0>,
  "code_snippet": "the relevant code",
  "suggestion": "how to fix it",
  "owasp_category": "Axx Category Name"
}}

If no security issues are found, respond with an empty array: []
Do not include any text outside the JSON array."""

        try:
            response = await self.ask_llm(prompt, system="You are an expert application security engineer.")
            # Extract JSON from response
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if not json_match:
                return []
            issues = json.loads(json_match.group(0))
        except (json.JSONDecodeError, Exception):
            return []

        findings: list[Finding] = []
        for issue in issues:
            sev_str = issue.get("severity", "info").lower()
            severity = {
                "critical": Severity.CRITICAL,
                "warning": Severity.WARNING,
                "info": Severity.INFO,
            }.get(sev_str, Severity.INFO)

            findings.append(
                self.make_finding(
                    rule_id=issue.get("rule_id", "SEC-OWASP"),
                    title=issue.get("title", "Security issue"),
                    description=issue.get("description", ""),
                    severity=severity,
                    filename=code_file.filename,
                    line_start=issue.get("line_start", 0),
                    line_end=issue.get("line_end", 0),
                    code_snippet=issue.get("code_snippet", ""),
                    suggestion=issue.get("suggestion", ""),
                    tags=["security", "owasp", issue.get("owasp_category", "")],
                    confidence=0.75,
                )
            )

        return findings
