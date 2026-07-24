"""YAML frontmatter parsing for project manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def split(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a document into (frontmatter, body). Frontmatter is None if
    absent or malformed."""
    if not text.startswith("---\n") and text != "---":
        return None, text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            raw = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            try:
                meta = yaml.safe_load(raw)
            except yaml.YAMLError:
                return None, text
            return (meta, body) if isinstance(meta, dict) else (None, text)
    return None, text


def read_head(path: Path, max_bytes: int = 65536) -> dict[str, Any] | None:
    """Read only the frontmatter block of a file — never the whole document.

    Keeps `projects()` fast with hundreds of manifests.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            first = f.readline(max_bytes)
            if first.strip() != "---":
                return None
            collected: list[str] = []
            read = len(first)
            while read < max_bytes:
                line = f.readline(max_bytes)
                if not line:
                    return None  # unterminated frontmatter
                read += len(line)
                if line.strip() in ("---", "..."):
                    meta = yaml.safe_load("".join(collected))
                    return meta if isinstance(meta, dict) else None
                collected.append(line)
            return None
    except (OSError, yaml.YAMLError):
        return None
