"""Code Reviewer — AI-powered multi-agent code review system."""

import sys

# Reconfigure stdout/stderr to use UTF-8 and handle encoding errors gracefully (replace instead of crash)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "0.1.0"
__author__ = "AI Code Reviewer"

