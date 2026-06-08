# 🤖 AI Code Reviewer

A production-grade, **multi-agent AI code review system** built in Python. Analyzes code for security vulnerabilities, logic bugs, style issues, and more — powered by Google Gemini and a concurrent async pipeline.

---

## Architecture

```
Input Layer          Agent Layer                    Output Layer
─────────────        ───────────────────────────    ────────────────────
File / Paste    →                                →  Markdown Report
PR Diff         →   Orchestrator                →  Inline PR Comments
CLI Trigger     →   ├── Context Agent           →  Severity Score
REST API        →   ├── [concurrent]            →
                    │   ├── Static Analysis      →
                    │   ├── Security Review      →
                    │   ├── Logic Review         →
                    │   └── Style & Perf         →
                    └── Result Aggregator        →
```

## Features

| Category | Details |
|---|---|
| 🔧 **Static Analysis** | `ruff` lint, AST-based complexity, unused imports, dead code |
| 🔒 **Security Review** | `bandit`, OWASP Top 10 (LLM), secret detection (entropy), banned functions |
| 🧠 **Logic Review** | LLM bug detection, off-by-one, null dereference, edge cases |
| ✨ **Style & Perf** | Naming conventions, O(n²) loops, docstrings, magic numbers |
| 🔄 **Concurrent Pipeline** | All 4 specialist agents run in parallel via `asyncio.gather` |
| 📊 **Smart Aggregation** | Deduplication, severity overrides, ranked by impact |
| 🌐 **REST API** | FastAPI server with sync + async review endpoints |
| 🪝 **Webhooks** | GitHub PR + GitLab MR webhook receivers |
| 💻 **CLI** | Review files, directories, or piped snippets |

---

## Quick Start

### 1. Install

```bash
# Install uv (if not already installed)
pip install uv

# Install the project
uv pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### 3. Review a file

```bash
code-reviewer review file path/to/your/code.py
```

### 4. Review a file and save the report

```bash
code-reviewer review file myfile.py --output review_report.md
```

### 5. Review a directory

```bash
code-reviewer review dir ./src --output reports/review.md
```

### 6. Pipe code from stdin

```bash
cat myfile.py | code-reviewer review snippet --filename myfile.py
```

### 7. Start the API server

```bash
code-reviewer serve
# API docs at http://localhost:8000/docs
```

---

## API Usage

### Review a code snippet

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{
    "code": "password = \"hunter2\"\neval(input())",
    "filename": "bad.py",
    "options": {"static": true, "security": true, "logic": true, "style": true}
  }'
```

### Upload a file (async)

```bash
# Upload and get job_id
JOB=$(curl -s -X POST http://localhost:8000/review/upload \
  -F "file=@myfile.py" | jq -r .job_id)

# Poll for results
curl http://localhost:8000/results/$JOB

# Get Markdown report
curl http://localhost:8000/results/$JOB/markdown
```

### GitHub Webhook

Set your GitHub webhook URL to `http://your-server:8000/webhook/github`  
with content type `application/json` and the `pull_request` event.  
Set `GITHUB_WEBHOOK_SECRET` and `GITHUB_TOKEN` in your `.env`.

---

## Configuration

Edit [`config/rules.yaml`](config/rules.yaml) to configure your team's standards:

```yaml
complexity:
  max_cyclomatic_complexity: 10   # Flag functions above this
  max_function_length_lines: 50   # Flag long functions
  max_parameters: 6               # Flag too many params

naming:
  functions: snake_case           # or camelCase, PascalCase
  classes: PascalCase
  constants: UPPER_SNAKE_CASE

security:
  owasp_checks:
    injection: true
    broken_auth: true
    # ... more OWASP categories

severity_overrides:
  hardcoded_secret: critical      # Override specific rule severities
  missing_docstring: info
```

---

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=code_reviewer --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_agents.py -v
```

---

## Project Structure

```
code-reviewer/
├── config/
│   └── rules.yaml              # Coding standards config
├── src/
│   └── code_reviewer/
│       ├── agents/
│       │   ├── base.py         # BaseAgent, Finding, ReviewContext
│       │   ├── orchestrator.py # Plans & delegates
│       │   ├── context.py      # AST extraction
│       │   ├── static.py       # ruff + complexity checks
│       │   ├── security.py     # bandit + OWASP + secrets
│       │   ├── logic.py        # LLM bug review
│       │   ├── style.py        # LLM style review
│       │   └── aggregator.py   # Merge, deduplicate, rank
│       ├── pipeline/
│       │   └── runner.py       # Async concurrent pipeline
│       ├── inputs/
│       │   ├── file_input.py   # File/directory loading
│       │   └── diff_input.py   # Unified diff parser
│       ├── outputs/
│       │   ├── artifact.py     # Markdown report
│       │   ├── suggestions.py  # PR comment builder
│       │   └── severity.py     # Score & grade
│       ├── api/
│       │   ├── server.py       # FastAPI app
│       │   └── models.py       # Pydantic schemas
│       └── main.py             # CLI entry point
└── tests/
    ├── fixtures/
    │   ├── sample_bad.py       # Intentionally buggy code
    │   └── sample_clean.py     # Well-written code
    ├── test_agents.py
    ├── test_pipeline.py
    └── test_outputs.py
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key *(required)* | — |
| `ANTHROPIC_API_KEY` | Claude API key *(optional fallback)* | — |
| `LLM_MODEL` | Model name | `gemini-1.5-pro` |
| `LLM_PROVIDER` | `gemini` or `anthropic` | `gemini` |
| `GITHUB_TOKEN` | For posting PR review comments | — |
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret | — |
| `GITLAB_TOKEN` | GitLab API token | — |
| `GITLAB_WEBHOOK_SECRET` | GitLab webhook token | — |
| `HOST` | API server bind host | `0.0.0.0` |
| `PORT` | API server port | `8000` |

---

*Built with Python 3.11+ · FastAPI · Google Gemini · Ruff · Bandit · asyncio*
>>>>>>> 29062b9 (Initial commit: Set up AI Code Reviewer with interactive wizard and bat launcher)
