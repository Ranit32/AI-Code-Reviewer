"""
Orchestrator Agent — Plans review strategy and delegates to specialist agents.

Receives the raw ReviewContext, decides which agents to run based on:
- File languages
- File sizes
- Available agents
- User options

Then delegates via the pipeline runner and returns the final ReviewPlan.
"""

from __future__ import annotations

import json
import re

from .base import (
    AgentType,
    BaseAgent,
    Finding,
    Language,
    ReviewContext,
    ReviewPlan,
)


class OrchestratorAgent(BaseAgent):
    """
    Plans and delegates code review tasks.

    Pipeline execution order:
    1. ContextAgent (always, runs first — enriches context)
    2. [Static, Security, Logic, Style] — run concurrently
    3. AggregatorAgent (always, runs last)
    """

    agent_type = AgentType.ORCHESTRATOR

    async def run(self, context: ReviewContext) -> list[Finding]:
        # Orchestrator itself produces no findings
        return []

    def build_plan(self, context: ReviewContext) -> ReviewPlan:
        """Decide which specialist agents to run."""
        agents: list[AgentType] = []

        has_python = any(f.language == Language.PYTHON for f in context.files)
        has_code = any(f.language != Language.UNKNOWN for f in context.files)
        options = context.options

        # Always run static analysis if there's code
        if has_code and options.get("static", True):
            agents.append(AgentType.STATIC)

        # Security review — always on by default
        if options.get("security", True):
            agents.append(AgentType.SECURITY)

        # Logic review — LLM-heavy, can be disabled for speed
        if options.get("logic", True):
            agents.append(AgentType.LOGIC)

        # Style & performance review
        if options.get("style", True):
            agents.append(AgentType.STYLE)

        # Identify priority files (changed files in a diff, or smaller files)
        priority_files = [
            f.filename for f in context.files
            if f.is_diff or len(f.content) < 5_000
        ]

        rationale = (
            f"Running {len(agents)} specialist agent(s): "
            + ", ".join(a.value for a in agents)
            + f". {len(context.files)} file(s) under review."
        )
        if has_python:
            rationale += " Python detected — enabling ruff + bandit + AST analysis."

        return ReviewPlan(
            job_id=context.job_id,
            agents_to_run=agents,
            priority_files=priority_files,
            rationale=rationale,
        )

    async def ask_llm_for_plan(self, context: ReviewContext) -> ReviewPlan:
        """
        Optional: Use LLM to determine the review strategy for complex repos.
        Falls back to build_plan() if LLM call fails.
        """
        file_summary = "\n".join(
            f"- {f.filename} ({f.language.value}, {len(f.content)} chars)"
            for f in context.files[:20]
        )

        prompt = f"""You are a code review orchestrator. Given these files under review:
{file_summary}

Decide which review agents to run. Respond with ONLY a JSON object:
{{
  "agents": ["static_analysis", "security_review", "logic_review", "style_and_perf"],
  "priority_files": ["list of filenames to prioritize"],
  "rationale": "brief explanation"
}}

Available agents: static_analysis, security_review, logic_review, style_and_perf
Only exclude agents if there's a clear reason (e.g., skip logic_review for config files)."""

        try:
            response = await self.ask_llm(prompt)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                agent_map = {
                    "static_analysis": AgentType.STATIC,
                    "security_review": AgentType.SECURITY,
                    "logic_review": AgentType.LOGIC,
                    "style_and_perf": AgentType.STYLE,
                }
                return ReviewPlan(
                    job_id=context.job_id,
                    agents_to_run=[agent_map[a] for a in data.get("agents", []) if a in agent_map],
                    priority_files=data.get("priority_files", []),
                    rationale=data.get("rationale", ""),
                )
        except Exception:
            pass

        return self.build_plan(context)
