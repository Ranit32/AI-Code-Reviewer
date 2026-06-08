"""
Diff Input Handler — Parses unified diff format from PR/MR webhooks.

Handles GitHub/GitLab pull request diff payloads and extracts:
- Changed files with their content
- Changed line numbers (for targeted review)
- Unified diff hunks
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..agents.base import CodeFile
from ..agents.context import detect_language


@dataclass
class DiffFile:
    """Parsed representation of one file in a unified diff."""
    filename: str
    old_filename: str
    is_new: bool
    is_deleted: bool
    hunks: list[str]
    added_lines: list[int]
    removed_lines: list[int]
    patch: str


def _new_diff_file(first_line: str) -> DiffFile:
    """Create a fresh DiffFile from a 'diff --git' header line."""
    return DiffFile(
        filename="",
        old_filename="",
        is_new=False,
        is_deleted=False,
        hunks=[],
        added_lines=[],
        removed_lines=[],
        patch=first_line + "\n",
    )


def _process_hunk_header(line: str, current_file: DiffFile, current_hunk_lines: list[str]) -> list[str]:
    """Handle a @@ hunk header, flush the previous hunk, and parse line ranges."""
    if current_hunk_lines:
        current_file.hunks.append("\n".join(current_hunk_lines))
    match = re.search(r'\+(\d+)(?:,(\d+))?', line)
    if match:
        new_start = int(match.group(1))
        new_count = int(match.group(2) or 1)
        current_file.added_lines.extend(range(new_start, new_start + new_count))
    current_file.patch += line + "\n"
    return [line]


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    """
    Parse a unified diff string into DiffFile objects.
    Supports standard git diff format.
    """
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk_lines: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if current_file is not None:
                current_file.hunks.append("\n".join(current_hunk_lines))
                files.append(current_file)
            current_file = _new_diff_file(line)
            current_hunk_lines = []

        elif line.startswith("--- ") and current_file is not None:
            old = line[4:].strip()
            current_file.old_filename = old if old != "/dev/null" else ""
            current_file.is_new = old == "/dev/null"
            current_file.patch += line + "\n"

        elif line.startswith("+++ ") and current_file is not None:
            new = line[4:].strip()
            current_file.filename = (
                new.lstrip("b/") if new != "/dev/null" else current_file.old_filename
            )
            current_file.is_deleted = new == "/dev/null"
            current_file.patch += line + "\n"

        elif line.startswith("@@ ") and current_file is not None:
            current_hunk_lines = _process_hunk_header(line, current_file, current_hunk_lines)

        elif current_file is not None:
            current_hunk_lines.append(line)
            current_file.patch += line + "\n"

    # Flush the last file
    if current_file is not None:
        if current_hunk_lines:
            current_file.hunks.append("\n".join(current_hunk_lines))
        files.append(current_file)

    return files


def diff_files_to_code_files(diff_files: list[DiffFile]) -> list[CodeFile]:
    """Convert DiffFile objects to CodeFile objects for the pipeline."""
    code_files: list[CodeFile] = []

    for df in diff_files:
        if df.is_deleted:
            continue  # Skip deleted files

        code_files.append(
            CodeFile(
                filename=df.filename,
                content=df.patch,   # Review the full patch
                language=detect_language(df.filename),
                is_diff=True,
                changed_lines=df.added_lines,
            )
        )

    return code_files


def parse_github_webhook_diff(payload: dict) -> list[CodeFile]:
    """
    Extract changed files from a GitHub PR webhook payload.
    Requires the files list from the GitHub API (/pulls/{n}/files endpoint).
    """
    code_files: list[CodeFile] = []

    for file_info in payload.get("files", []):
        filename = file_info.get("filename", "")
        patch = file_info.get("patch", "")
        status = file_info.get("status", "")

        if status == "removed" or not patch:
            continue

        code_files.append(
            CodeFile(
                filename=filename,
                content=patch,
                language=detect_language(filename),
                is_diff=True,
            )
        )

    return code_files
