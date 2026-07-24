"""FastMCP server: the core filesystem tools over the brain.

Every call reads current state from disk — the user edits the brain by hand
in their own editor, so nothing is cached. Mutating tools serialize through
`gitio.write_lock` and produce exactly one commit each.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TypedDict

from fastmcp import FastMCP

from brain_mcp import projects as projects_module
from brain_mcp import searchio
from brain_mcp.auth import build_auth
from brain_mcp.config import BrainConfig
from brain_mcp.errors import ToolError, git_guard
from brain_mcp.gitio import commit_message, commit_paths, has_changes, head_sha, run_git, write_lock
from brain_mcp.pathsafe import confine, rel
from brain_mcp.sections import SectionNotFound, replace_section


class Entry(TypedDict):
    name: str
    path: str
    type: str  # "file" | "dir"
    size: int
    modified: str  # ISO 8601 UTC


class Hit(TypedDict):
    path: str
    line: int
    text: str


class MutationResult(TypedDict):
    path: str
    commit: str | None
    message: str


def build_server(config: BrainConfig) -> FastMCP:
    root = config.root
    mcp = FastMCP(name="brain-mcp", auth=build_auth(config))

    # --- read tools ---

    @mcp.tool(name="list")
    def list_dir(path: str = "") -> list[Entry]:
        """List one directory level of the brain. Entries carry name, path,
        type (file|dir), size in bytes, and modified time (ISO 8601 UTC)."""
        target = confine(root, path, allow_root=True)
        if not target.is_dir():
            if target.exists():
                raise ToolError(f"'{path}' is a file, not a directory; use read")
            raise ToolError(f"no such directory '{path}'")
        parent = rel(root, target)
        entries = []
        for entry in os.scandir(target):
            if entry.name == ".git":
                continue
            stat = entry.stat()
            is_dir = entry.is_dir()
            entries.append(
                {
                    "name": entry.name,
                    "path": f"{parent}/{entry.name}" if parent else entry.name,
                    "type": "dir" if is_dir else "file",
                    "size": 0 if is_dir else stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        return sorted(entries, key=lambda e: e["name"])

    @mcp.tool
    def read(path: str) -> str:
        """Read a file's contents."""
        target = confine(root, path)
        if not target.exists():
            raise ToolError(f"no such file '{path}'")
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory, not a file; use list")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"'{path}' is not a UTF-8 text file") from exc

    @mcp.tool
    def search(query: str, path: str = "", limit: int = 50, regex: bool = False) -> list[Hit]:
        """Search the brain for a string (literal by default; regex=True to
        opt in). Hits carry path, line, and the matching line's text."""
        start = confine(root, path, allow_root=True)
        if not start.exists():
            raise ToolError(f"no such path '{path}'")
        return searchio.search(root, query, start, limit, regex)

    @mcp.tool(name="glob")
    def glob_paths(pattern: str) -> list[str]:
        """Paths matching a glob pattern (e.g. 'projects/**/*.md')."""
        if not pattern:
            raise ToolError("a glob pattern is required")
        if pattern.startswith(("/", "~")) or "\\" in pattern or ".." in pattern.split("/"):
            raise ToolError("glob patterns must be relative and stay inside the brain")
        try:
            matches = root.glob(pattern)
        except (ValueError, NotImplementedError) as exc:
            raise ToolError(f"bad glob pattern '{pattern}': {exc}") from exc
        return sorted(
            m.relative_to(root).as_posix()
            for m in matches
            if ".git" not in m.relative_to(root).parts
        )

    # --- write tools (one commit per call) ---

    @mcp.tool
    @git_guard
    async def write(path: str, content: str) -> MutationResult:
        """Create or overwrite a file. Parent directories are created."""
        target = confine(root, path)
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory; give a file path")
        r = rel(root, target)
        async with write_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if not await has_changes(root, [r]):
                return {
                    "path": r,
                    "commit": await head_sha(root),
                    "message": "no change: content identical",
                }
            await run_git(root, "add", "--", r)
            msg = commit_message("write", r)
            sha = await commit_paths(root, [r], msg)
        return {"path": r, "commit": sha, "message": msg.splitlines()[0]}

    @mcp.tool
    @git_guard
    async def patch(path: str, section: str, content: str) -> MutationResult:
        """Replace one markdown section: everything under the first heading
        matching `section` exactly, up to the next same-or-higher heading."""
        target = confine(root, path)
        if not target.is_file():
            raise ToolError(f"no such file '{path}'")
        text = target.read_text(encoding="utf-8")
        try:
            new_text = replace_section(text, section, content)
        except SectionNotFound as exc:
            listing = ", ".join(exc.present) if exc.present else "(none)"
            raise ToolError(
                f"no such section '{section}' in {path}; sections present: {listing}"
            ) from exc
        r = rel(root, target)
        async with write_lock:
            target.write_text(new_text, encoding="utf-8")
            if not await has_changes(root, [r]):
                return {
                    "path": r,
                    "commit": await head_sha(root),
                    "message": "no change: section already had that content",
                }
            await run_git(root, "add", "--", r)
            msg = commit_message("patch", r)
            sha = await commit_paths(root, [r], msg)
        return {"path": r, "commit": sha, "message": msg.splitlines()[0]}

    @mcp.tool
    @git_guard
    async def move(src: str, dst: str) -> MutationResult:
        """Move or rename via git mv. Parent directories are created;
        an existing destination file is overwritten."""
        source = confine(root, src)
        if not source.exists():
            raise ToolError(f"no such path '{src}'")
        dest = confine(root, dst)
        r_src, r_dst = rel(root, source), rel(root, dest)
        if r_src == r_dst:
            raise ToolError("src and dst are the same path")
        async with write_lock:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await run_git(root, "mv", "-f", "--", r_src, r_dst)
            msg = commit_message("move", f"{r_src} -> {r_dst}")
            sha = await commit_paths(root, [r_src, r_dst], msg)
        return {"path": r_dst, "commit": sha, "message": msg.splitlines()[0]}

    @mcp.tool
    @git_guard
    async def delete(path: str) -> MutationResult:
        """Delete a file (or directory, recursively)."""
        target = confine(root, path)
        if not target.exists():
            raise ToolError(f"no such path '{path}'")
        r = rel(root, target)
        rm_args = ["rm", "-q", "-r", "--"] if target.is_dir() else ["rm", "-q", "--"]
        async with write_lock:
            await run_git(root, *rm_args, r)
            msg = commit_message("delete", r)
            sha = await commit_paths(root, [r], msg)
        return {"path": r, "commit": sha, "message": msg.splitlines()[0]}

    projects_module.register(mcp, config)
    return mcp
