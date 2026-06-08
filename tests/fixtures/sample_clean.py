"""
Clean Python code — should produce minimal findings.
Used to verify the reviewer doesn't produce false positives.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
DEFAULT_PAGE_SIZE = 20


@dataclass
class User:
    """Represents an application user."""

    user_id: int
    username: str
    email: str
    age: int
    is_active: bool = True
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate user data on construction."""
        if not 13 <= self.age <= 120:
            raise ValueError(f"Invalid age: {self.age}. Must be between 13 and 120.")
        if "@" not in self.email:
            raise ValueError(f"Invalid email: {self.email}")


def hash_password(password: str) -> str:
    """
    Hash a password using SHA-256 with a salt.

    Args:
        password: The plain-text password to hash.

    Returns:
        A hex-encoded SHA-256 hash prefixed with the salt.
    """
    salt = hashlib.sha256(password[:4].encode()).hexdigest()[:16]
    return salt + hashlib.sha256((salt + password).encode()).hexdigest()


def find_duplicates(items: list) -> list:
    """
    Find duplicate items in a list in O(n) time.

    Args:
        items: The list to search for duplicates.

    Returns:
        A list of items that appear more than once.
    """
    seen: set = set()
    duplicates: set = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return list(duplicates)


def paginate(items: list, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[list]:
    """
    Yield successive pages from a list.

    Args:
        items: The full list to paginate.
        page_size: Number of items per page.

    Yields:
        Sublists of up to page_size items.
    """
    for i in range(0, len(items), page_size):
        yield items[i : i + page_size]


def read_config(path: str) -> dict:
    """
    Safely read a JSON config file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Parsed configuration dictionary, or empty dict on error.
    """
    import json
    from pathlib import Path

    try:
        config_path = Path(path)
        if not config_path.exists():
            logger.warning("Config file not found: %s", path)
            return {}
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse config file %s: %s", path, exc)
        return {}
    except PermissionError as exc:
        logger.error("Permission denied reading config %s: %s", path, exc)
        return {}
