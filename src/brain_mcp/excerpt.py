"""Short excerpts of a note: its title, and the first sentence of its prose.

Used where a caller wants a map of many notes in one call (`projects(about=True)`,
`search(expand=True)`) rather than reading each one.
"""

from __future__ import annotations

import re

from brain_mcp import frontmatter

# Blocks that aren't prose: headings, lists, tables, quotes, code, HTML.
_NOT_PROSE = ("#", "|", "-", "*", "+", ">", "```", "<", "!")
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)")


def first_sentence(text: str, max_chars: int = 200) -> str:
    """The first sentence of the first prose paragraph after any frontmatter,
    whitespace-collapsed and cut to `max_chars`. Empty if there is no prose."""
    _meta, body = frontmatter.split(text)
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block or block.startswith(_NOT_PROSE) or re.match(r"\d+\.\s", block):
            continue
        para = " ".join(block.split())
        m = _SENTENCE.match(para)
        sentence = m.group(1) if m else para
        if len(sentence) > max_chars:
            sentence = sentence[: max_chars - 1].rstrip() + "…"
        return sentence
    return ""


def title(text: str) -> str | None:
    """Frontmatter `title` (or `name`), else the first `# ` heading, else None."""
    meta, body = frontmatter.split(text)
    if meta:
        for key in ("title", "name"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None
