from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    """Request body for POST /review"""
    code: str = Field(..., description="Raw source code to review")
    filename: str = Field("snippet.py", description="Filename (used for language detection)")
    options: dict[str, bool] = Field(
        default_factory=lambda: {
            "static": True,
            "security": True,
            "logic": True,
            "style": True,
        },
        description="Which agents to enable",
    )


class FindingResponse(BaseModel):
    rule_id: str
    title: str
    description: str
    severity: str
    agent: str
    filename: str
    line_start: int
    line_end: int
    code_snippet: str
    suggestion: str
    confidence: float
    tags: list[str]


class SummaryResponse(BaseModel):
    total: int
    critical: int
    warning: int
    info: int
    score: int
    grade: str
    by_agent: dict[str, int]
    duration_ms: float


class ReviewResponse(BaseModel):
    job_id: str
    status: str = "complete"
    summary: SummaryResponse
    findings: list[FindingResponse]
    markdown_report: str


class WebhookPayload(BaseModel):
    """Generic webhook payload wrapper."""
    action: str = ""
    pull_request: dict[str, Any] | None = None
    merge_request: dict[str, Any] | None = None


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | complete | error
    progress: str = ""
    result: ReviewResponse | None = None
    error: str | None = None
