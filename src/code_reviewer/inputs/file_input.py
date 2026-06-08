"""
File Input Handler — Accepts raw code strings or file paths.

Handles:
- Direct code string input (with language hint)
- File path loading (auto-detects language from extension)
- Multiple files at once
"""

from __future__ import annotations

from pathlib import Path

from ..agents.base import CodeFile, Language
from ..agents.context import detect_language


# Max file size to process (5 MB)
MAX_FILE_SIZE = 5 * 1024 * 1024

# Extensions to skip (binary, generated, etc.)
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".mp4",
    ".zip", ".tar", ".gz", ".lock", ".min.js", ".map",
}

# Directories to skip
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "env", "dist", "build", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


def load_file(file_path: str | Path) -> CodeFile | None:
    """
    Load a single file from disk into a CodeFile object.
    Returns None if the file should be skipped.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not path.is_file():
        raise ValueError(f"Not a file: {file_path}")

    if path.suffix.lower() in SKIP_EXTENSIONS:
        return None

    if path.stat().st_size > MAX_FILE_SIZE:
        return None

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return None

    return CodeFile(
        filename=str(path),
        content=content,
        language=detect_language(path.name),
    )


def load_files_from_directory(directory: str | Path, recursive: bool = True) -> list[CodeFile]:
    """
    Walk a directory and load all reviewable code files.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    code_files: list[CodeFile] = []
    pattern = "**/*" if recursive else "*"

    for path in root.glob(pattern):
        # Skip hidden dirs and common non-code dirs
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        code_file = load_file(path)
        if code_file:
            code_files.append(code_file)

    return code_files


def from_string(
    code: str,
    filename: str = "snippet.py",
    language: Language | None = None,
) -> CodeFile:
    """
    Create a CodeFile from a raw code string.
    Useful for API/CLI paste input.
    """
    lang = language or detect_language(filename)
    return CodeFile(
        filename=filename,
        content=code,
        language=lang,
    )
