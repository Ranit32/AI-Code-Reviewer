"""
CLI Entry Point — Typer-based command line interface.

Commands:
  code-reviewer review file <path>         — Review a single file
  code-reviewer review dir <directory>     — Review all files in a directory
  code-reviewer review snippet             — Review pasted code (stdin)
  code-reviewer serve                      — Start the API server
  code-reviewer version                    — Show version
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .agents.base import Language

app = typer.Typer(
    name="code-reviewer",
    help="🤖 AI-powered multi-agent code review system",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

review_app = typer.Typer(help="Review code files, directories, or snippets")
app.add_typer(review_app, name="review")

console = Console()


# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

def _get_runner(rules_path: str = "config/rules.yaml"):
    from .pipeline.runner import PipelineRunner
    return PipelineRunner(rules_path=rules_path)


def _run_pipeline(code_files, options: dict, rules_path: str, output: str | None):
    from .agents.base import ReviewContext
    from .outputs.artifact import MarkdownReportGenerator
    from .outputs.severity import compute_overall_score, grade

    runner = _get_runner(rules_path)
    report_gen = MarkdownReportGenerator()

    job_id = str(uuid.uuid4())
    context = ReviewContext(
        job_id=job_id,
        files=code_files,
        options=options,
    )

    result = asyncio.run(runner.run(context, show_progress=True))

    # Generate markdown
    markdown = report_gen.generate(
        findings=result.findings,
        summary=result.summary,
        job_id=job_id,
        plan_rationale=result.plan.rationale,
        duration_ms=result.total_duration_ms,
    )

    # Print summary table
    score = compute_overall_score(result.findings)
    _print_summary_table(result.summary, score, grade(score))

    # Print or save report
    if output:
        report_path = report_gen.save(markdown, output)
        console.print(f"\n[bold green]✓ Report saved to:[/bold green] {report_path}")
    else:
        console.print("\n")
        console.print(Markdown(markdown))

    # Exit with non-zero code if critical findings
    if result.summary.get("critical", 0) > 0:
        raise typer.Exit(code=1)


def _print_summary_table(summary: dict, score: int, grade_letter: str) -> None:
    table = Table(title="Review Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("🔴 Critical", f"[red]{summary.get('critical', 0)}[/red]")
    table.add_row("🟡 Warnings", f"[yellow]{summary.get('warning', 0)}[/yellow]")
    table.add_row("🔵 Info", f"[dim]{summary.get('info', 0)}[/dim]")
    table.add_row("📊 Total Findings", str(summary.get("total", 0)))
    table.add_row("🏆 Score", f"[bold]{score}/100 (Grade {grade_letter})[/bold]")

    console.print("\n")
    console.print(table)


# ---------------------------------------------------------------------------
# review file
# ---------------------------------------------------------------------------

@review_app.command("file")
def review_file(
    path: Path = typer.Argument(..., help="Path to the file to review"),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to this path"),
    rules: str = typer.Option("config/rules.yaml", "--rules", "-r", help="Path to rules.yaml"),
    no_static: bool = typer.Option(False, "--no-static", help="Disable static analysis"),
    no_security: bool = typer.Option(False, "--no-security", help="Disable security review"),
    no_logic: bool = typer.Option(False, "--no-logic", help="Disable logic review"),
    no_style: bool = typer.Option(False, "--no-style", help="Disable style review"),
) -> None:
    """Review a single code file."""
    from .inputs.file_input import load_file

    if not path.exists():
        console.print(f"[red]✗ File not found: {path}[/red]")
        raise typer.Exit(code=2)

    console.print(Panel(f"[bold]Reviewing:[/bold] {path}", style="blue"))

    code_file = load_file(path)
    if not code_file:
        console.print(f"[yellow]⚠ File skipped (binary or too large): {path}[/yellow]")
        raise typer.Exit(code=0)

    options = {
        "static": not no_static,
        "security": not no_security,
        "logic": not no_logic,
        "style": not no_style,
    }

    _run_pipeline([code_file], options, rules, output)


# ---------------------------------------------------------------------------
# review dir
# ---------------------------------------------------------------------------

@review_app.command("dir")
def review_directory(
    directory: Path = typer.Argument(..., help="Directory to review"),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to this path"),
    rules: str = typer.Option("config/rules.yaml", "--rules", "-r", help="Path to rules.yaml"),
    no_recursive: bool = typer.Option(False, "--no-recursive", help="Don't recurse into subdirs"),
    pattern: str | None = typer.Option(None, "--pattern", "-p", help="Glob pattern (e.g. *.py)"),
) -> None:
    """Review all code files in a directory."""
    from .inputs.file_input import load_files_from_directory

    if not directory.is_dir():
        console.print(f"[red]✗ Not a directory: {directory}[/red]")
        raise typer.Exit(code=2)

    console.print(Panel(f"[bold]Reviewing directory:[/bold] {directory}", style="blue"))

    code_files = load_files_from_directory(directory, recursive=not no_recursive)

    if pattern:
        code_files = [f for f in code_files if Path(f.filename).match(pattern)]

    if not code_files:
        console.print("[yellow]No reviewable files found.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"[dim]Found {len(code_files)} files to review[/dim]")
    _run_pipeline(code_files, {}, rules, output)


# ---------------------------------------------------------------------------
# review snippet (stdin)
# ---------------------------------------------------------------------------

@review_app.command("snippet")
def review_snippet(
    filename: str = typer.Option("snippet.py", "--filename", "-f", help="Filename hint for language detection"),
    output: str | None = typer.Option(None, "--output", "-o", help="Save report to this path"),
    rules: str = typer.Option("config/rules.yaml", "--rules", "-r", help="Path to rules.yaml"),
) -> None:
    """Review code piped from stdin.

    Example:
        cat myfile.py | code-reviewer review snippet
    """
    from .inputs.file_input import from_string

    if sys.stdin.isatty():
        console.print("[yellow]Paste your code below (Ctrl+D or Ctrl+Z to finish):[/yellow]")

    code = sys.stdin.read()
    if not code.strip():
        console.print("[red]✗ No code provided[/red]")
        raise typer.Exit(code=2)

    code_file = from_string(code, filename=filename)
    _run_pipeline([code_file], {}, rules, output)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable hot reload (dev mode)"),
    log_level: str = typer.Option("info", "--log-level", help="Log level"),
) -> None:
    """Start the AI Code Reviewer REST API server."""
    import uvicorn

    console.print(
        Panel(
            f"[bold green]🚀 Starting AI Code Reviewer API[/bold green]\n"
            f"Host: {host}:{port}\n"
            f"Docs: http://{host}:{port}/docs",
            style="green",
        )
    )
    uvicorn.run(
        "code_reviewer.api.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

@app.command()
def version() -> None:
    """Show the version and configuration."""
    console.print("[bold]AI Code Reviewer[/bold] v0.1.0")
    console.print("Built with: Gemini · FastAPI · Ruff · Bandit · asyncio")


# ---------------------------------------------------------------------------
# Interactive CLI Wizard
# ---------------------------------------------------------------------------

def detect_language_from_content(code: str) -> tuple[str, Language]:
    
    # Simple heuristics to detect language from code snippet contents
    code_lower = code.lower()
    
    # C++
    if "#include" in code_lower and ("<iostream>" in code_lower or "std::" in code_lower or "vector" in code_lower):
        return "snippet.cpp", Language.CPP
        
    # C
    if "#include" in code_lower and ("<stdio.h>" in code_lower or "printf(" in code_lower):
        return "snippet.c", Language.C
        
    # Rust
    if "fn main()" in code_lower or "use std::" in code_lower or "pub struct" in code_lower:
        return "snippet.rs", Language.RUST
        
    # Go
    if "package main" in code_lower or "func main()" in code_lower:
        return "snippet.go", Language.GO

    # Java
    if "public class" in code_lower and ("public static void main" in code_lower or "system.out.print" in code_lower):
        return "snippet.java", Language.JAVA
        
    # Javascript / Typescript
    if "console.log(" in code_lower or "const " in code_lower or "let " in code_lower or "require(" in code_lower or "import " in code_lower:
        if "interface " in code_lower or "type " in code_lower or "declare " in code_lower:
            return "snippet.ts", Language.TYPESCRIPT
        return "snippet.js", Language.JAVASCRIPT

    # Default to Python
    return "snippet.py", Language.PYTHON


def interactive_wizard() -> None:
    """Runs the interactive, guided code review wizard."""
    from rich.prompt import Prompt, Confirm
    from .inputs.file_input import from_string, load_file, load_files_from_directory
    from .outputs.severity import compute_overall_score, grade
    from .agents.base import ReviewContext
    import uuid
    import asyncio

    console.print("\n")
    console.print(Panel(
        "[bold green]🤖 Welcome to the AI Code Reviewer Wizard! 🤖[/bold green]\n\n"
        "This wizard will analyze your code for security, logic, style, and performance, "
        "and give you a score with actionable tips on where to improve.",
        title="[bold]Interactive Code Review[/bold]",
        border_style="green",
    ))

    # Ask what they want to review
    console.print("\n[bold cyan]What would you like to review?[/bold cyan]")
    console.print("  [bold]1.[/bold] Paste a code snippet")
    console.print("  [bold]2.[/bold] Review a specific file")
    console.print("  [bold]3.[/bold] Review an entire folder/directory")
    console.print("  [bold]4.[/bold] Exit")

    choice = Prompt.ask("\nChoose an option", choices=["1", "2", "3", "4"], default="1")

    if choice == "4":
        console.print("[yellow]Goodbye![/yellow]")
        return

    code_files = []
    
    if choice == "1":
        # Paste snippet
        console.print(
            "\n[yellow]Paste your code below. When done, type 'END' on a new line by itself and press Enter to finish:[/yellow]\n"
        )
        
        code_lines = []
        while True:
            try:
                line = sys.stdin.readline()
                if not line: # EOF
                    break
                if line.rstrip() in ("END", "__END__"):
                    break
                code_lines.append(line)
            except KeyboardInterrupt:
                break
                
        code = "".join(code_lines)
        if not code.strip():
            console.print("[red]✗ No code provided.[/red]")
            return
            
        filename, detected_lang = detect_language_from_content(code)
        console.print(f"[dim]Auto-detected language: {detected_lang.value.upper()} (using temporary filename: {filename})[/dim]")
        
        code_file = from_string(code, filename=filename, language=detected_lang)
        code_files = [code_file]
        
    elif choice == "2":
        # Specific file
        file_path_str = Prompt.ask("\nEnter the path to the file you want to review")
        path = Path(file_path_str)
        if not path.exists():
            console.print(f"[red]✗ File not found: {path}[/red]")
            return
        code_file = load_file(path)
        if not code_file:
            console.print(f"[yellow]⚠ File skipped (binary or too large): {path}[/yellow]")
            return
        code_files = [code_file]
        
    elif choice == "3":
        # Folder/directory
        dir_path_str = Prompt.ask("\nEnter the path to the folder/directory you want to review")
        path = Path(dir_path_str)
        if not path.is_dir():
            console.print(f"[red]✗ Directory not found: {path}[/red]")
            return
        
        recursive = Confirm.ask("Search subfolders recursively?", default=True)
        code_files = load_files_from_directory(path, recursive=recursive)
        
        pattern = Prompt.ask("Filter by glob pattern? (e.g. *.py, press Enter to skip)", default="")
        if pattern:
            code_files = [f for f in code_files if Path(f.filename).match(pattern)]
            
        if not code_files:
            console.print("[yellow]No reviewable files found in that directory.[/yellow]")
            return

    console.print(f"\n[bold green]✓ Loading {len(code_files)} file(s) for review...[/bold green]")

    runner = _get_runner()
    job_id = str(uuid.uuid4())
    context = ReviewContext(
        job_id=job_id,
        files=code_files,
        options={},
    )

    console.print("[bold yellow]⚡ Running the AI agents (Static, Security, Logic, Style)...[/bold yellow]\n")
    
    try:
        result = asyncio.run(runner.run(context, show_progress=True))
    except Exception as e:
        console.print(f"[red]✗ Review failed to run: {e}[/red]")
        return

    score = compute_overall_score(result.findings)
    grade_letter = grade(score)

    # Style/Grade banner
    color = "green" if score >= 80 else ("yellow" if score >= 50 else "red")
    console.print("\n")
    console.print(Panel(
        f"[bold {color}]🏆 Review Score: {score}/100 (Grade {grade_letter})[/bold {color}]\n\n"
        f"Critical issues: {result.summary.get('critical', 0)} | "
        f"Warnings: {result.summary.get('warning', 0)} | "
        f"Info suggestions: {result.summary.get('info', 0)}",
        title="[bold]Review Results Summary[/bold]",
        border_style=color
    ))

    # Print action items/tips
    if not result.findings:
        console.print("\n[bold green]🎉 Excellent! No issues were found in your code. Keep it up![/bold green]\n")
    else:
        console.print("\n[bold yellow]💡 Tips on where to make changes for better reliability & quality:[/bold yellow]\n")
        
        # Group findings by file
        from collections import defaultdict
        findings_by_file = defaultdict(list)
        for f in result.findings:
            findings_by_file[f.filename].append(f)
            
        for fn, findings in findings_by_file.items():
            console.print(f"[underline bold cyan]File: {fn}[/underline bold cyan]")
            for i, f in enumerate(findings, 1):
                sev_color = "red" if f.severity == "critical" else ("yellow" if f.severity == "warning" else "blue")
                line_info = f"Line {f.line_start}" if f.line_start else "General"
                
                # Show finding title with severity badge
                console.print(f"  [bold]{i}. [{sev_color}]{f.severity.upper()}[/{sev_color}] {f.title} ({line_info})[/bold]")
                # Show description
                console.print(f"     [dim]{f.description}[/dim]")
                # Show suggestion/fix
                if f.suggestion:
                    console.print(f"     [bold green]👉 Tip/Fix:[/bold green] {f.suggestion}")
                if f.code_snippet:
                    console.print("     [dim]Code context:[/dim]")
                    # format code context nicely indented
                    snippet_lines = f.code_snippet.split('\n')
                    for sl in snippet_lines[:5]: # show first 5 lines max
                        console.print(f"       [dim]{sl}[/dim]")
                    if len(snippet_lines) > 5:
                        console.print("       [dim]...[/dim]")
                console.print()

    console.print("\n[bold green]Thank you for using the AI Code Reviewer! Let me know if you want to review more code.[/bold green]\n")


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    """🤖 AI-powered multi-agent code review system"""
    if ctx.invoked_subcommand is None:
        interactive_wizard()


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()

