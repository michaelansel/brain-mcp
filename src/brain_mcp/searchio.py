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


def search(root: Path, query: str, start: Path, limit: int, regex: bool) -> list[dict[str, Any]]:
    """Search `start` (a confined file or directory) for `query`.

    Returns hits as {path, line, text}, sorted by (path, line), capped at
    `limit`. Literal match unless `regex` is True.
    """
    if shutil.which("rg"):
        hits = _search_rg(root, query, start, regex)
    else:
        hits = _search_py(root, query, start, regex)
    hits.sort(key=lambda h: (h["path"], h["line"]))
    return hits[:limit]


def _search_rg(root: Path, query: str, start: Path, regex: bool) -> list[dict[str, Any]]:
    cmd = ["rg", "--json", "--hidden", "--no-ignore", "-g", "!**/.git/**"]
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


def _search_py(root: Path, query: str, start: Path, regex: bool) -> list[dict[str, Any]]:
    if regex:
        try:
            pattern = re.compile(query)
        except re.error as exc:
            raise ToolError(f"invalid regex '{query}': {exc}") from exc
        match = pattern.search
    else:

        def match(line: str) -> bool:  # type: ignore[misc]
            return query in line

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
