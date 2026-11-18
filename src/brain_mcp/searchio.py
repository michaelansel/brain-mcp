"""Search across the brain: ripgrep when available, pure Python otherwise."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError

from brain_mcp import contenthash, excerpt
from brain_mcp.summaries import SUMMARY_NAME


def search(
    root: Path,
    query: str,
    start: Path,
    limit: int,
    regex: bool,
    case_sensitive: bool = False,
    archive_last: bool = False,
) -> list[dict[str, Any]]:
    """Search `start` (a confined file or directory) for `query`.

    Returns hits as {path, line, text}, sorted by (path, line), capped at
    `limit`. Literal match unless `regex` is True; case-insensitive unless
    `case_sensitive` is True. `archive_last` sorts archive/ after everything
    else before the cap, so live notes aren't crowded out.
    """
    if shutil.which("rg"):
        hits = _search_rg(root, query, start, regex, case_sensitive)
    else:
        hits = _search_py(root, query, start, regex, case_sensitive)
    if archive_last:
        hits.sort(key=lambda h: (h["path"].startswith("archive/"), h["path"], h["line"]))
    else:
        hits.sort(key=lambda h: (h["path"], h["line"]))
    return hits[:limit]


def _search_rg(
    root: Path, query: str, start: Path, regex: bool, case_sensitive: bool
) -> list[dict[str, Any]]:
    cmd = ["rg", "--json", "--hidden", "--no-ignore", "-g", "!**/.git/**"]
    cmd.append("-s" if case_sensitive else "-i")
    if not regex:
        cmd.append("-F")
    cmd += ["-e", query, "--", str(start)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 2:
        raise ToolError(f"search failed: {proc.stderr.strip()}")
    hits = []
    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        text = data["lines"].get("text")
        if text is None:
            continue  # non-UTF-8 line; skip rather than mangle
        hit_path = Path(data["path"]["text"]).resolve()
        hits.append(
            {
                "path": hit_path.relative_to(root).as_posix(),
                "line": data["line_number"],
                "text": text.rstrip("\n"),
            }
        )
    return hits


def _search_py(
    root: Path, query: str, start: Path, regex: bool, case_sensitive: bool
) -> list[dict[str, Any]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise ToolError(f"invalid regex '{query}': {exc}") from exc
    match = pattern.search

    files = [start] if start.is_file() else _walk(start)
    hits = []
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="strict") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if "\x00" in line:
                        break  # binary; skip rest of file
                    if match(line):
                        hits.append(
                            {
                                "path": f.resolve().relative_to(root).as_posix(),
                                "line": lineno,
                                "text": line.rstrip("\n"),
                            }
                        )
        except (UnicodeDecodeError, OSError):
            continue
    return hits


def _walk(start: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        out.extend(Path(dirpath) / name for name in filenames)
    return out


# search(expand=True): whole small notes inline, within an overall budget.
EXPAND_FILE_MAX_BYTES = 4096
EXPAND_BUDGET_BYTES = 40_000


def expand(root: Path, hits: list[dict[str, Any]], read_note) -> list[dict[str, Any]]:
    """Group hits by file, in hit order. Each file gets its title, its hits, and —
    if it is at most EXPAND_FILE_MAX_BYTES and the overall budget allows — its
    whole content and hash, exactly as `read` would return them. Content is all
    or nothing; a file is never truncated. summary.md is never inlined: reading
    it is what reports whether it has fallen behind, so it must be `read`."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        groups.setdefault(h["path"], []).append({"line": h["line"], "text": h["text"]})
    budget = EXPAND_BUDGET_BYTES
    out = []
    for path, file_hits in groups.items():
        entry: dict[str, Any] = {"path": path, "title": None, "hits": file_hits, "content": None}
        text = read_note(root / path)
        if text is not None:
            entry["title"] = excerpt.title(text)
            size = len(text.encode("utf-8"))
            inline = (
                Path(path).name != SUMMARY_NAME and size <= EXPAND_FILE_MAX_BYTES and size <= budget
            )
            if inline:
                entry["content"] = text
                entry["hash"] = contenthash.digest(text)
                budget -= size
        out.append(entry)
    return out
