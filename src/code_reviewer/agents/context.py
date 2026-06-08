"""
Context Agent — Repository structure reader and AST extractor.

Runs first in the pipeline. Extracts:
- Language detection
- AST: functions, classes, imports, globals
- Cyclomatic complexity per function
- Repo file tree
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .base import (
    AgentType,
    ASTInfo,
    BaseAgent,
    Finding,
    Language,
    ReviewContext,
)


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

_EXT_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".rs": Language.RUST,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".c": Language.C,
    ".rb": Language.RUBY,
    ".php": Language.PHP,
}


def detect_language(filename: str) -> Language:
    ext = Path(filename).suffix.lower()
    return _EXT_MAP.get(ext, Language.UNKNOWN)


# ---------------------------------------------------------------------------
# Python AST Extractor
# ---------------------------------------------------------------------------


class PythonASTExtractor:
    """Extracts structural information from Python source using the `ast` module."""

    def extract(self, filename: str, source: str) -> ASTInfo:
        info = ASTInfo(filename=filename)
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as exc:
            info.raw_ast_dump = f"SyntaxError: {exc}"
            return info

        info.raw_ast_dump = ast.dump(tree, indent=2)
        self._walk_imports(tree, info)
        self._walk_definitions(tree, info)
        self._walk_globals(tree, info)
        info.complexity_metrics = self._compute_metrics(info)
        return info

    def _walk_imports(self, tree: ast.AST, info: ASTInfo) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    info.imports.append(f"{module}.{alias.name}")

    def _walk_definitions(self, tree: ast.AST, info: ASTInfo) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                complexity = self._cyclomatic_complexity(node)
                has_docstring = (
                    bool(node.body)
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                info.functions.append({
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "args": [a.arg for a in node.args.args],
                    "complexity": complexity,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "has_docstring": has_docstring,
                })
            elif isinstance(node, ast.ClassDef):
                has_docstring = (
                    bool(node.body)
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                )
                info.classes.append({
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "bases": [ast.unparse(b) for b in node.bases],
                    "has_docstring": has_docstring,
                })

    def _walk_globals(self, tree: ast.AST, info: ASTInfo) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        info.globals.append(target.id)

    def _compute_metrics(self, info: ASTInfo) -> dict:
        return {
            "total_functions": len(info.functions),
            "total_classes": len(info.classes),
            "total_imports": len(info.imports),
            "avg_complexity": (
                sum(f["complexity"] for f in info.functions) / len(info.functions)
                if info.functions else 0.0
            ),
            "max_complexity": max((f["complexity"] for f in info.functions), default=0),
        }

    def _cyclomatic_complexity(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """Compute cyclomatic complexity: 1 + number of branches."""
        complexity = 1
        branch_nodes = (
            ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
            ast.With, ast.AsyncWith, ast.Assert, ast.comprehension,
        )
        for child in ast.walk(node):
            if isinstance(child, branch_nodes):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity


# ---------------------------------------------------------------------------
# Generic Source Scanner (non-Python fallback)
# ---------------------------------------------------------------------------


def extract_generic_info(filename: str, source: str) -> ASTInfo:
    """Simple regex-based extraction for non-Python files."""
    info = ASTInfo(filename=filename)
    # Detect import-like lines
    import_patterns = [
        r'^import\s+[\w.]+',
        r'^from\s+[\w.]+\s+import',
        r'^require\s*\(',
        r'^#include\s*[<"]',
    ]
    for line in source.splitlines():
        line = line.strip()
        for pattern in import_patterns:
            if re.match(pattern, line):
                info.imports.append(line)
                break
    return info


# ---------------------------------------------------------------------------
# Context Agent
# ---------------------------------------------------------------------------


class ContextAgent(BaseAgent):
    """
    Reads all files in the review context, detects languages,
    extracts ASTs, and enriches the ReviewContext in-place.
    """

    agent_type = AgentType.CONTEXT

    async def run(self, context: ReviewContext) -> list[Finding]:
        py_extractor = PythonASTExtractor()

        for code_file in context.files:
            # Detect language
            if code_file.language == Language.UNKNOWN:
                code_file.language = detect_language(code_file.filename)

            # Extract AST
            if code_file.language == Language.PYTHON and not code_file.is_diff:
                ast_info = py_extractor.extract(code_file.filename, code_file.content)
            else:
                ast_info = extract_generic_info(code_file.filename, code_file.content)

            context.ast_info[code_file.filename] = ast_info

        # No findings — context agent only enriches the context
        return []
