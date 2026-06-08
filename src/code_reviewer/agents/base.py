"""
Code Reviewer — Core Agent Abstractions

Defines the shared data models (Finding, ReviewContext, ReviewPlan)
and the BaseAgent abstract class that all specialist agents inherit from.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

console = Console()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    JAVA = "java"
    RUST = "rust"
    CPP = "cpp"
    C = "c"
    RUBY = "ruby"
    PHP = "php"
    UNKNOWN = "unknown"


class AgentType(str, Enum):
    STATIC = "static_analysis"
    SECURITY = "security_review"
    LOGIC = "logic_review"
    STYLE = "style_and_perf"
    CONTEXT = "context"
    ORCHESTRATOR = "orchestrator"
    AGGREGATOR = "aggregator"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class CodeFile:
    """Represents a single code file under review."""

    filename: str
    content: str
    language: Language = Language.UNKNOWN
    is_diff: bool = False          # True if content is a unified diff
    changed_lines: list[int] = field(default_factory=list)


@dataclass
class Finding:
    """A single issue discovered by a review agent."""

    rule_id: str
    title: str
    description: str
    severity: Severity
    agent: AgentType
    filename: str
    line_start: int = 0
    line_end: int = 0
    code_snippet: str = ""
    suggestion: str = ""
    confidence: float = 1.0       # 0.0 – 1.0
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.rule_id, self.filename, self.line_start))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (
            self.rule_id == other.rule_id
            and self.filename == other.filename
            and self.line_start == other.line_start
        )


@dataclass
class ASTInfo:
    """AST metadata extracted by the Context Agent."""

    filename: str
    functions: list[dict[str, Any]] = field(default_factory=list)
    classes: list[dict[str, Any]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    globals: list[str] = field(default_factory=list)
    complexity_metrics: dict[str, Any] = field(default_factory=dict)
    raw_ast_dump: str = ""


@dataclass
class ReviewContext:
    """Full context passed to all agents during a review pipeline run."""

    job_id: str
    files: list[CodeFile]
    rules: dict[str, Any] = field(default_factory=dict)
    ast_info: dict[str, ASTInfo] = field(default_factory=dict)   # filename → ASTInfo
    repo_structure: list[str] = field(default_factory=list)
    pr_metadata: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewPlan:
    """Orchestrator's plan for which agents to run."""

    job_id: str
    agents_to_run: list[AgentType]
    priority_files: list[str] = field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# Rules Loader
# ---------------------------------------------------------------------------


def load_rules(rules_path: str = "config/rules.yaml") -> dict[str, Any]:
    """Load coding standards from the YAML rules file."""
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        console.print(f"[yellow]⚠ Rules file not found at {rules_path}, using defaults[/yellow]")
        return {}


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """
    Thin wrapper around Google Gemini (primary) with retry logic.
    Can be swapped for Anthropic by setting LLM_PROVIDER=anthropic in .env.
    """

    _instance: LLMClient | None = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        self.provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        self.model_name = os.getenv("LLM_MODEL", "gemini-1.5-pro")
        self._setup()

    def _setup(self) -> None:
        if self.provider == "gemini":
            try:
                import google.generativeai as genai  # lazy import — avoids grpc DLL at module load
                api_key = os.getenv("GEMINI_API_KEY", "")
                if api_key:
                    genai.configure(api_key=api_key)
                self._model = genai.GenerativeModel(self.model_name)
                self._genai = genai
            except ImportError as exc:
                console.print(f"[red]google-generativeai unavailable: {exc}[/red]")
                self._model = None
                self._genai = None
        elif self.provider == "anthropic":
            import anthropic  # type: ignore[import]
            self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    @classmethod
    async def get_instance(cls) -> "LLMClient":
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt and return the response text."""
        for attempt in range(3):
            try:
                if self.provider == "gemini":
                    if self._model is None:
                        return "[]"  # Graceful degradation when SDK unavailable
                    genai = self._genai
                    full_prompt = f"{system}\n\n{prompt}" if system else prompt
                    response = await asyncio.to_thread(
                        self._model.generate_content,
                        full_prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=temperature,
                            max_output_tokens=max_tokens,
                        ),
                    )
                    return response.text or ""

                elif self.provider == "anthropic":
                    messages = [{"role": "user", "content": prompt}]
                    response = await asyncio.to_thread(
                        self._client.messages.create,
                        model=self.model_name,
                        max_tokens=max_tokens,
                        system=system or "You are an expert code reviewer.",
                        messages=messages,
                    )
                    return response.content[0].text

            except Exception as exc:
                if attempt == 2:
                    raise
                exc_str = str(exc).lower()
                if "429" in exc_str or "quota" in exc_str or "exhausted" in exc_str or "rate limit" in exc_str:
                    wait = 5 * (attempt + 1)
                else:
                    wait = 2 ** attempt
                console.print(f"[yellow]LLM attempt {attempt + 1} failed: {exc}. Retrying in {wait}s[/yellow]")
                await asyncio.sleep(wait)

        return ""


# ---------------------------------------------------------------------------
# Base Agent
# ---------------------------------------------------------------------------


class BaseAgent(ABC):
    """Abstract base class for all review agents."""

    agent_type: AgentType

    def __init__(self) -> None:
        self.llm: LLMClient | None = None
        self.rules: dict[str, Any] = {}

    async def initialize(self, rules: dict[str, Any]) -> None:
        """Called once before run(). Loads LLM client and rules."""
        self.llm = await LLMClient.get_instance()
        self.rules = rules

    @abstractmethod
    async def run(self, context: ReviewContext) -> list[Finding]:
        """Execute the review and return a list of Findings."""
        ...

    async def ask_llm(self, prompt: str, system: str = "") -> str:
        """Convenience wrapper for LLM calls."""
        if self.llm is None:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
        return await self.llm.complete(prompt, system=system)

    def make_finding(
        self,
        rule_id: str,
        title: str,
        description: str,
        severity: Severity,
        filename: str,
        line_start: int = 0,
        line_end: int = 0,
        code_snippet: str = "",
        suggestion: str = "",
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> Finding:
        """Factory method for creating Finding objects."""
        return Finding(
            rule_id=rule_id,
            title=title,
            description=description,
            severity=severity,
            agent=self.agent_type,
            filename=filename,
            line_start=line_start,
            line_end=line_end,
            code_snippet=code_snippet,
            suggestion=suggestion,
            confidence=confidence,
            tags=tags or [],
        )
