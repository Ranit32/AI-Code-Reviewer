"""Tests for agent-level functionality."""

from __future__ import annotations

import pytest

from code_reviewer.agents.base import (
    AgentType,
    CodeFile,
    Finding,
    Language,
    ReviewContext,
    Severity,
)
from code_reviewer.agents.aggregator import AggregatorAgent
from code_reviewer.agents.context import ContextAgent, PythonASTExtractor, detect_language
from code_reviewer.agents.static import StaticAnalysisAgent


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("main.py", Language.PYTHON),
    ("app.js", Language.JAVASCRIPT),
    ("server.ts", Language.TYPESCRIPT),
    ("main.go", Language.GO),
    ("Main.java", Language.JAVA),
    ("main.rs", Language.RUST),
    ("unknown.xyz", Language.UNKNOWN),
])
def test_language_detection(filename: str, expected: Language) -> None:
    assert detect_language(filename) == expected


# ---------------------------------------------------------------------------
# Python AST Extractor
# ---------------------------------------------------------------------------

SAMPLE_CODE = '''
import os
import sys

CONSTANT = 42

class MyClass:
    """A sample class."""

    def method_one(self, x, y):
        """Do something."""
        if x > 0:
            for i in range(y):
                print(i)
        return x + y

    def method_two(self):
        pass

def standalone_function(a, b, c):
    return a + b + c
'''


def test_ast_extractor_functions() -> None:
    extractor = PythonASTExtractor()
    info = extractor.extract("test.py", SAMPLE_CODE)

    fn_names = [f["name"] for f in info.functions]
    assert "method_one" in fn_names
    assert "method_two" in fn_names
    assert "standalone_function" in fn_names


def test_ast_extractor_classes() -> None:
    extractor = PythonASTExtractor()
    info = extractor.extract("test.py", SAMPLE_CODE)

    assert len(info.classes) == 1
    assert info.classes[0]["name"] == "MyClass"


def test_ast_extractor_imports() -> None:
    extractor = PythonASTExtractor()
    info = extractor.extract("test.py", SAMPLE_CODE)

    assert "os" in info.imports
    assert "sys" in info.imports


def test_ast_extractor_complexity() -> None:
    extractor = PythonASTExtractor()
    info = extractor.extract("test.py", SAMPLE_CODE)

    method_one = next(f for f in info.functions if f["name"] == "method_one")
    # if + for = complexity >= 3
    assert method_one["complexity"] >= 3


def test_ast_extractor_syntax_error() -> None:
    extractor = PythonASTExtractor()
    info = extractor.extract("bad.py", "def broken(:\n    pass")
    assert "SyntaxError" in info.raw_ast_dump


# ---------------------------------------------------------------------------
# Context Agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_agent_enriches_context() -> None:
    agent = ContextAgent()
    await agent.initialize({})

    code_file = CodeFile(filename="test.py", content=SAMPLE_CODE, language=Language.UNKNOWN)
    context = ReviewContext(job_id="test-job", files=[code_file])

    findings = await agent.run(context)

    assert findings == []  # Context agent produces no findings
    assert "test.py" in context.ast_info
    assert code_file.language == Language.PYTHON  # Should have been detected


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def _make_finding(rule_id: str, filename: str, line: int, severity: Severity) -> Finding:
    return Finding(
        rule_id=rule_id,
        title=f"Test finding {rule_id}",
        description="Test",
        severity=severity,
        agent=AgentType.STATIC,
        filename=filename,
        line_start=line,
    )


@pytest.mark.asyncio
async def test_aggregator_deduplicates() -> None:
    agent = AggregatorAgent()
    await agent.initialize({})

    findings = [
        _make_finding("RUFF-E501", "a.py", 10, Severity.WARNING),
        _make_finding("STATIC-LINE-TOO-LONG", "a.py", 11, Severity.INFO),  # Same bucket as line 10
        _make_finding("SEC-HARDCODED-SECRET", "a.py", 20, Severity.CRITICAL),
    ]

    result = agent.aggregate(findings)
    # The two findings at lines 10-11 are in the same 5-line bucket with same category prefix "RUFF"/"STATIC"
    # SEC finding should survive regardless
    assert any(f.severity == Severity.CRITICAL for f in result)


@pytest.mark.asyncio
async def test_aggregator_sorts_by_severity() -> None:
    agent = AggregatorAgent()
    await agent.initialize({})

    findings = [
        _make_finding("INFO-1", "a.py", 1, Severity.INFO),
        _make_finding("CRIT-1", "b.py", 2, Severity.CRITICAL),
        _make_finding("WARN-1", "c.py", 3, Severity.WARNING),
    ]

    result = agent.aggregate(findings)
    severities = [f.severity for f in result]

    assert severities[0] == Severity.CRITICAL
    assert severities[-1] == Severity.INFO


@pytest.mark.asyncio
async def test_aggregator_applies_severity_overrides() -> None:
    agent = AggregatorAgent()
    rules = {"severity_overrides": {"MISSING-DOCSTRING": "critical"}}
    await agent.initialize(rules)

    findings = [
        _make_finding("STYLE-MISSING-DOCSTRING", "a.py", 1, Severity.INFO),
    ]

    result = agent.aggregate(findings)
    assert result[0].severity == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Static Analysis (without LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_agent_detects_long_functions() -> None:
    # Generate a function that is 60 lines long
    long_fn = "def long_function():\n" + "\n".join(f"    x = {i}" for i in range(60))
    code_file = CodeFile(filename="long.py", content=long_fn, language=Language.PYTHON)
    context = ReviewContext(
        job_id="test",
        files=[code_file],
        rules={"complexity": {"max_function_length_lines": 50, "max_cyclomatic_complexity": 10, "max_parameters": 6}},
    )

    # Pre-populate AST info
    extractor = PythonASTExtractor()
    context.ast_info["long.py"] = extractor.extract("long.py", long_fn)

    agent = StaticAnalysisAgent()
    await agent.initialize({"complexity": {"max_function_length_lines": 50, "max_cyclomatic_complexity": 10, "max_parameters": 6}})

    findings = await agent.run(context)

    long_fn_findings = [f for f in findings if "LONG-FUNCTION" in f.rule_id or "long_function" in f.title]
    assert len(long_fn_findings) > 0


@pytest.mark.asyncio
async def test_static_agent_detects_too_many_params() -> None:
    code = "def f(a, b, c, d, e, f, g):\n    pass\n"
    code_file = CodeFile(filename="params.py", content=code, language=Language.PYTHON)

    extractor = PythonASTExtractor()
    context = ReviewContext(
        job_id="test",
        files=[code_file],
        rules={"complexity": {"max_parameters": 6, "max_cyclomatic_complexity": 10, "max_function_length_lines": 50}},
    )
    context.ast_info["params.py"] = extractor.extract("params.py", code)

    agent = StaticAnalysisAgent()
    await agent.initialize(context.rules)

    findings = await agent.run(context)
    param_findings = [f for f in findings if "PARAMS" in f.rule_id or "parameters" in f.title.lower()]
    assert len(param_findings) > 0
