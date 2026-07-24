"""Markdown section replacement for the `patch` tool.

A section is an ATX heading (`#` .. `######`) plus everything up to the next
heading of the same or higher level. Headings inside fenced code blocks are
ignored.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


class SectionNotFound(Exception):
    def __init__(self, section: str, present: list[str]):
        self.section = section
        self.present = present
        listing = ", ".join(present) if present else "(none)"
        super().__init__(f"no such section '{section}'; sections present: {listing}")


def find_headings(text: str) -> list[tuple[int, int, str]]:
    """All headings outside code fences as (line_index, level, title)."""
    headings = []
    in_fence = False
    fence_marker = ""
    for i, line in enumerate(text.splitlines()):
        fence = _FENCE_RE.match(line)
        if fence:
            if not in_fence:
                in_fence = True
                fence_marker = fence.group(1)
            elif fence.group(1) == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2)))
    return headings


def replace_section(text: str, section: str, content: str) -> str:
    """Replace the body of the first heading whose text equals `section`.

    The heading line is kept; everything after it up to the next heading of
    the same or higher level (or EOF) becomes `content`. Raises
    SectionNotFound if no heading matches.
    """
    headings = find_headings(text)
    target = next((h for h in headings if h[2] == section), None)
    if target is None:
        raise SectionNotFound(section, [h[2] for h in headings])

    start_line, level, _ = target
    end_line = next(
        (i for i, lvl, _ in headings if i > start_line and lvl <= level),
        None,
    )

    lines = text.splitlines(keepends=True)
    if not content.endswith("\n"):
        content += "\n"
    head = lines[: start_line + 1]
    tail = lines[end_line:] if end_line is not None else []
    return "".join(head) + content + "".join(tail)
