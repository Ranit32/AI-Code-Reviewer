"""Tests for the async pipeline runner and input layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_reviewer.agents.base import AgentType, CodeFile, Language, ReviewContext
from code_reviewer.inputs.diff_input import parse_unified_diff
from code_reviewer.inputs.file_input import from_string, load_file
from code_reviewer.pipeline.runner import PipelineRunner


# ---------------------------------------------------------------------------
# Input Layer
# ---------------------------------------------------------------------------

def test_from_string_detects_python() -> None:
    code_file = from_string("print('hello')", filename="test.py")
    assert code_file.language == Language.PYTHON
    assert code_file.content == "print('hello')"
    assert code_file.filename == "test.py"


def test_from_string_detects_js() -> None:
    code_file = from_string("console.log('hi')", filename="app.js")
    assert code_file.language == Language.JAVASCRIPT


def test_load_file_reads_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "sample_bad.py"
    code_file = load_file(fixture_path)
    assert code_file is not None
    assert code_file.language == Language.PYTHON
    assert len(code_file.content) > 100


def test_load_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_file("nonexistent_file_12345.py")


# ---------------------------------------------------------------------------
# Diff Parser
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,7 @@
 import os
+import sys
 
 def main():
-    print("hello")
+    print("hello world")
+    return 0
"""


def test_parse_unified_diff_finds_file() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    assert len(files) == 1
    assert "main.py" in files[0].filename


def test_parse_unified_diff_detects_new_content() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    assert files[0].patch  # Should have patch content


# ---------------------------------------------------------------------------
# Pipeline Runner (mocked agents)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_runner_builds_plan() -> None:
    """Test that the runner correctly creates a review plan."""
    runner = PipelineRunner(rules_path="config/rules.yaml")

    code_file = CodeFile(
        filename="test.py",
        content="x = 1",
        language=Language.PYTHON,
    )
    context = ReviewContext(
        job_id="test-pipeline",
        files=[code_file],
        options={"static": True, "security": False, "logic": False, "style": False},
    )

    # Inject rules manually
    context.rules = runner.rules
    await runner.orchestrator.initialize(runner.rules)
    plan = runner.orchestrator.build_plan(context)

    assert AgentType.STATIC in plan.agents_to_run
    assert AgentType.SECURITY not in plan.agents_to_run


@pytest.mark.asyncio
async def test_pipeline_runner_returns_result() -> None:
    """Test that the pipeline runner completes without errors using mocked agents."""
    runner = PipelineRunner(rules_path="config/rules.yaml")

    code_file = CodeFile(
        filename="test.py",
        content="x = 1\n",
        language=Language.PYTHON,
    )
    context = ReviewContext(
        job_id="test-run",
        files=[code_file],
        options={"static": True, "security": False, "logic": False, "style": False},
    )

    # Mock LLM client so we don't need a real API key
    with patch("code_reviewer.agents.base.LLMClient.get_instance") as mock_llm:
        mock_instance = MagicMock()
        mock_instance.complete = AsyncMock(return_value="[]")
        mock_llm.return_value = mock_instance

        result = await runner.run(context, show_progress=False, timeout=30.0)

    assert result.job_id == "test-run"
    assert result.summary is not None
    assert isinstance(result.findings, list)
    assert result.total_duration_ms > 0
