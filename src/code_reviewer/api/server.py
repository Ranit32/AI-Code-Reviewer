"""
FastAPI server — REST API + GitHub/GitLab webhook receiver.

Endpoints:
  POST /review              — Review code from request body
  POST /review/upload       — Review uploaded file
  POST /webhook/github      — GitHub PR webhook
  POST /webhook/gitlab      — GitLab MR webhook
  GET  /results/{job_id}    — Poll async job results
  GET  /health              — Health check
  GET  /                    — API info
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from rich.console import Console

from ..agents.base import CodeFile, Finding
from ..inputs.diff_input import parse_github_webhook_diff
from ..inputs.file_input import from_string
from ..outputs.artifact import MarkdownReportGenerator
from ..outputs.severity import compute_overall_score, grade
from ..pipeline.runner import PipelineResult, PipelineRunner
from .models import (
    FindingResponse,
    JobStatus,
    ReviewRequest,
    ReviewResponse,
    SummaryResponse,
)

console = Console()

# ---------------------------------------------------------------------------
# In-memory job store (replace with Redis in production)
# ---------------------------------------------------------------------------

_jobs: dict[str, JobStatus] = {}
_runner: PipelineRunner | None = None
_report_gen = MarkdownReportGenerator()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _runner
    _runner = PipelineRunner()
    console.print("[bold green]✓ AI Code Reviewer API ready[/bold green]")
    yield
    console.print("[dim]Shutting down...[/dim]")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Code Reviewer",
    description=(
        "Multi-agent AI code review system. "
        "Analyzes code for security vulnerabilities, logic bugs, style issues, and more."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding_to_response(f: Finding) -> FindingResponse:
    return FindingResponse(
        rule_id=f.rule_id,
        title=f.title,
        description=f.description,
        severity=f.severity.value,
        agent=f.agent.value,
        filename=f.filename,
        line_start=f.line_start,
        line_end=f.line_end,
        code_snippet=f.code_snippet,
        suggestion=f.suggestion,
        confidence=f.confidence,
        tags=f.tags,
    )


def _result_to_response(result: PipelineResult) -> ReviewResponse:
    score = compute_overall_score(result.findings)
    markdown = _report_gen.generate(
        findings=result.findings,
        summary=result.summary,
        job_id=result.job_id,
        plan_rationale=result.plan.rationale,
        duration_ms=result.total_duration_ms,
    )
    return ReviewResponse(
        job_id=result.job_id,
        status="complete",
        summary=SummaryResponse(
            total=result.summary.get("total", 0),
            critical=result.summary.get("critical", 0),
            warning=result.summary.get("warning", 0),
            info=result.summary.get("info", 0),
            score=score,
            grade=grade(score),
            by_agent=result.summary.get("by_agent", {}),
            duration_ms=result.total_duration_ms,
        ),
        findings=[_finding_to_response(f) for f in result.findings],
        markdown_report=markdown,
    )


async def _run_review(
    job_id: str,
    code_files: list[CodeFile],
    options: dict[str, bool] | None = None,
) -> None:
    """Background task that runs the pipeline and updates the job store."""
    from ..agents.base import ReviewContext

    _jobs[job_id] = JobStatus(job_id=job_id, status="running")

    context = ReviewContext(
        job_id=job_id,
        files=code_files,
        options=options or {},
    )

    try:
        result = await _runner.run(context)  # type: ignore[union-attr]
        response = _result_to_response(result)
        _jobs[job_id] = JobStatus(job_id=job_id, status="complete", result=response)
    except Exception as exc:
        _jobs[job_id] = JobStatus(job_id=job_id, status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", tags=["info"])
async def root() -> dict:
    return {
        "name": "AI Code Reviewer",
        "version": "0.1.0",
        "status": "online",
        "endpoints": {
            "review": "POST /review",
            "upload": "POST /review/upload",
            "webhook_github": "POST /webhook/github",
            "webhook_gitlab": "POST /webhook/gitlab",
            "results": "GET /results/{job_id}",
            "health": "GET /health",
        },
    }


@app.get("/health", tags=["info"])
async def health() -> dict:
    return {"status": "healthy", "runner": _runner is not None}


@app.post("/review", response_model=ReviewResponse, tags=["review"])
async def review_code(request: ReviewRequest) -> ReviewResponse:
    """
    Synchronously review a code snippet.
    Returns the full analysis immediately (blocks until complete).
    """
    from ..agents.base import ReviewContext

    job_id = str(uuid.uuid4())
    code_file = from_string(request.code, filename=request.filename)
    context = ReviewContext(
        job_id=job_id,
        files=[code_file],
        options=request.options,
    )

    result = await _runner.run(context)  # type: ignore[union-attr]
    return _result_to_response(result)


@app.post("/review/upload", tags=["review"])
async def review_upload(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, "File to review"],
) -> dict:
    """
    Upload a file for async review. Returns a job_id to poll.
    """
    job_id = str(uuid.uuid4())
    content = (await file.read()).decode("utf-8", errors="replace")
    code_file = from_string(content, filename=file.filename or "upload.py")

    background_tasks.add_task(_run_review, job_id, [code_file])
    return {"job_id": job_id, "status": "pending", "poll": f"/results/{job_id}"}


@app.get("/results/{job_id}", response_model=JobStatus, tags=["review"])
async def get_results(job_id: str) -> JobStatus:
    """Poll the result of an async review job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _jobs[job_id]


@app.get("/results/{job_id}/markdown", response_class=PlainTextResponse, tags=["review"])
async def get_markdown_report(job_id: str) -> str:
    """Get the Markdown report for a completed job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    job = _jobs[job_id]
    if job.status != "complete" or not job.result:
        raise HTTPException(status_code=202, detail="Job not complete yet")
    return job.result.markdown_report


# ---------------------------------------------------------------------------
# GitHub Webhook
# ---------------------------------------------------------------------------


def _verify_github_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook/github", tags=["webhooks"])
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict:
    """
    Receive GitHub PR webhook events.
    Triggers an async review on opened/synchronize events.
    """
    body = await request.body()

    # Verify signature if secret is configured
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if secret and x_hub_signature_256 and not _verify_github_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event not in ("pull_request",):
        return {"status": "ignored", "event": x_github_event}

    payload = await request.json()
    action = payload.get("action", "")

    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "action": action}

    # Fetch PR diff files from GitHub API
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", "")

    job_id = str(uuid.uuid4())

    async def fetch_and_review() -> None:
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files",
                    headers=headers,
                    timeout=30.0,
                )
                files_data = resp.json()
                code_files = parse_github_webhook_diff({"files": files_data})
        except Exception as exc:
            _jobs[job_id] = JobStatus(job_id=job_id, status="error", error=str(exc))
            return

        await _run_review(job_id, code_files)

    background_tasks.add_task(fetch_and_review)
    return {
        "status": "accepted",
        "job_id": job_id,
        "pr": pr_number,
        "poll": f"/results/{job_id}",
    }


# ---------------------------------------------------------------------------
# GitLab Webhook
# ---------------------------------------------------------------------------


@app.post("/webhook/gitlab", tags=["webhooks"])
async def gitlab_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: str = Header(default=""),
    x_gitlab_event: str = Header(default=""),
) -> dict:
    """Receive GitLab MR webhook events."""
    secret = os.getenv("GITLAB_WEBHOOK_SECRET", "")
    if secret and x_gitlab_token != secret:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    if "Merge Request" not in x_gitlab_event:
        return {"status": "ignored", "event": x_gitlab_event}

    payload = await request.json()
    action = payload.get("object_attributes", {}).get("action", "")

    if action not in ("open", "update", "reopen"):
        return {"status": "ignored", "action": action}

    # For GitLab, we'd fetch the diff via the GitLab API
    # Simplified: just acknowledge and return job_id
    job_id = str(uuid.uuid4())
    _jobs[job_id] = JobStatus(
        job_id=job_id,
        status="pending",
        progress="GitLab diff fetch not yet implemented in this demo",
    )

    return {
        "status": "accepted",
        "job_id": job_id,
        "poll": f"/results/{job_id}",
    }
