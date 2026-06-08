"""
Pipeline Runner — Async concurrent execution of the review pipeline.

Execution order:
  1. ContextAgent          (serial — enriches ReviewContext for all others)
  2. [Static, Security, Logic, Style]  (concurrent — asyncio.gather)
  3. AggregatorAgent       (serial — merges and ranks)

Returns a PipelineResult with all findings + summary statistics.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass, field

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..agents import (
    AgentType,
    AggregatorAgent,
    ContextAgent,
    Finding,
    LogicReviewAgent,
    OrchestratorAgent,
    ReviewContext,
    ReviewPlan,
    SecurityReviewAgent,
    StaticAnalysisAgent,
    StyleAndPerfAgent,
    load_rules,
)

console = Console()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    agent: AgentType
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class PipelineResult:
    job_id: str
    plan: ReviewPlan
    findings: list[Finding] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    agent_results: list[AgentResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------


class PipelineRunner:
    """
    Orchestrates the full review pipeline for a given ReviewContext.
    Handles initialization, concurrent execution, and aggregation.
    """

    _AGENT_MAP = {
        AgentType.STATIC: StaticAnalysisAgent,
        AgentType.SECURITY: SecurityReviewAgent,
        AgentType.LOGIC: LogicReviewAgent,
        AgentType.STYLE: StyleAndPerfAgent,
    }

    def __init__(self, rules_path: str = "config/rules.yaml") -> None:
        self.rules = load_rules(rules_path)
        self.orchestrator = OrchestratorAgent()
        self.context_agent = ContextAgent()
        self.aggregator = AggregatorAgent()

    async def run(
        self,
        context: ReviewContext,
        show_progress: bool = False,
        timeout: float = 120.0,
    ) -> PipelineResult:
        """Execute the full pipeline and return aggregated results."""
        start = time.monotonic()

        # Inject rules into context
        context.rules = self.rules

        # Initialize orchestrator and context agent
        await self.orchestrator.initialize(self.rules)
        await self.context_agent.initialize(self.rules)
        await self.aggregator.initialize(self.rules)

        # Step 1: Build review plan
        plan = self.orchestrator.build_plan(context)
        console.print(f"\n[bold blue]🔍 Review Plan:[/bold blue] {plan.rationale}")

        # Step 2: Context agent (serial — must run first)
        console.print("[dim]Running context agent...[/dim]")
        await self.context_agent.run(context)

        # Step 3: Run specialist agents concurrently
        agent_results: list[AgentResult] = []
        all_findings: list[Finding] = []

        async def run_agent(agent_type: AgentType) -> AgentResult:
            agent_cls = self._AGENT_MAP.get(agent_type)
            if not agent_cls:
                return AgentResult(agent=agent_type, error=f"Unknown agent: {agent_type}")

            agent = agent_cls()
            await agent.initialize(self.rules)
            t0 = time.monotonic()

            try:
                findings = await asyncio.wait_for(
                    agent.run(context),
                    timeout=timeout,
                )
                return AgentResult(
                    agent=agent_type,
                    findings=findings,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
            except TimeoutError:
                return AgentResult(
                    agent=agent_type,
                    error="Timed out",
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
            except Exception as exc:
                return AgentResult(
                    agent=agent_type,
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                tasks = {
                    agent_type: progress.add_task(
                        f"[cyan]{agent_type.value}[/cyan]", total=None
                    )
                    for agent_type in plan.agents_to_run
                }
                coros = [run_agent(a) for a in plan.agents_to_run]
                results = await asyncio.gather(*coros, return_exceptions=False)
                for agent_type, result in zip(plan.agents_to_run, results, strict=False):
                    progress.update(tasks[agent_type], completed=True)
                    agent_results.append(result)
        else:
            coros = [run_agent(a) for a in plan.agents_to_run]
            results = await asyncio.gather(*coros, return_exceptions=False)
            agent_results = list(results)

        # Collect all findings
        for ar in agent_results:
            if ar.error:
                console.print(f"[red]⚠ Agent {ar.agent.value} error: {ar.error}[/red]")
            else:
                all_findings.extend(ar.findings)
                console.print(
                    f"[green]✓[/green] {ar.agent.value}: "
                    f"{len(ar.findings)} findings ({ar.duration_ms:.0f}ms)"
                )

        # Step 4: Aggregate
        final_findings = self.aggregator.aggregate(all_findings)
        summary = self.aggregator.summarize(final_findings)

        total_ms = (time.monotonic() - start) * 1000

        console.print(
            f"\n[bold]📊 Review complete:[/bold] "
            f"[red]{summary['critical']} critical[/red] · "
            f"[yellow]{summary['warning']} warnings[/yellow] · "
            f"[dim]{summary['info']} info[/dim] "
            f"({total_ms:.0f}ms total)"
        )

        return PipelineResult(
            job_id=context.job_id,
            plan=plan,
            findings=final_findings,
            summary=summary,
            agent_results=agent_results,
            total_duration_ms=total_ms,
        )
