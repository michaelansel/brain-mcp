"""FastMCP server: the core filesystem tools over the brain.

Every call reads current state from disk — the user edits the brain by hand
in their own editor, so nothing is cached. Mutating tools serialize through
`gitio.write_lock` and produce exactly one commit each.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from fastmcp import FastMCP

from brain_mcp import attention as attention_module
from brain_mcp import contenthash, searchio, summaries
from brain_mcp import projects as projects_module
from brain_mcp.auth import build_auth
from brain_mcp.config import BrainConfig
from brain_mcp.errors import ToolError, git_guard
from brain_mcp.gitio import (
    commit_message,
    commit_paths,
    configure_post_commit,
    configure_push,
    has_changes,
    head_sha,
    run_git,
    write_lock,
)
from brain_mcp.pathsafe import confine, prune_empty_dirs, rel

# Injected into every enrolled session at initialization — the one channel
# that reaches all surfaces. Policy lives in agents.md, not here.
INSTRUCTIONS = (
    "This is the user's shared memory ('the brain'), edited by both the user "
    "directly and agents through these tools. Before your first write in a "
    "session, read agents.md at the root and follow its conventions. Capture "
    "is explicit: write only when the user asks, or when a conversation "
    "produces a durable decision or artifact worth keeping. Read a file before "
    "changing it: read returns the content and its hash, and overwriting an "
    "existing file with write requires passing that hash back as base_hash. "
    "Prefer edit (exact-string replacement) to change part of a file; pass "
    "base_hash there too when you have it. A folder's summary.md is a state "
    "note about what the notes beside it amount to; when a read reports it has "
    "fallen behind, read what changed and rewrite it."
)


class ReadResult(TypedDict):
    content: str
    hash: str  # sha256 hex; pass back as base_hash to write/edit
    # Only on a summary.md that has fallen behind the notes beside it.
    summary_stale: NotRequired[summaries.SummaryState]


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


class EditResult(MutationResult):
    replacements: int


def build_server(config: BrainConfig) -> FastMCP:
    root = config.root
    configure_push(config.git_remote if config.git_push else None)
    configure_post_commit(config.post_commit_cmd)
    attention = attention_module.Attention(
        config.state_dir or attention_module.default_state_dir(), config.attention_days
    )
    mcp = FastMCP(name="brain-mcp", auth=build_auth(config), instructions=INSTRUCTIONS)

    # --- read-before-write ---

    def read_text(target: Path, path: str) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"'{path}' is not a UTF-8 text file") from exc

    def require_base_hash(base_hash: str | None, r: str, current: str, *, required: bool) -> None:
        """Refuse to change a file the caller can't prove it has seen.

        Caller holds `write_lock` and has just read `current` off disk, so the
        comparison can't race a hand edit. `required` is False for edit, where
        a byte-exact `old_string` already proves the caller saw the current
        text — there, a supplied hash additionally catches changes elsewhere
        in the file.
        """
        if not config.read_before_write or (base_hash is None and not required):
            return
        if base_hash is None:
            raise ToolError(
                f"read '{r}' first and pass the hash it returns as base_hash — this "
                f"server refuses to overwrite a file you haven't seen"
            )
        if base_hash != contenthash.digest(current):
            raise ToolError(
                f"'{r}' changed on disk since you read it — the user edits the brain by "
                f"hand. Read it again, reconcile, then retry."
            )

    # --- read tools ---

    READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

    @mcp.tool(name="list", annotations=READ_ONLY)
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

    @mcp.tool(annotations=READ_ONLY)
    async def read(path: str) -> ReadResult:
        """Read a file. Returns its `content` and the `hash` of that content —
        pass the hash back as `base_hash` to write (required to overwrite) or
        edit (optional) to prove you're changing what you actually saw.

        Reading a folder's summary.md also reports what has changed in that
        folder since the summary was written."""
        target = confine(root, path)
        if not target.exists():
            raise ToolError(f"no such file '{path}'")
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory, not a file; use list")
        content = read_text(target, path)
        result: ReadResult = {"content": content, "hash": contenthash.digest(content)}
        if target.name == summaries.SUMMARY_NAME:
            folder = rel(root, target.parent)
            await attention.record(folder, "read")
            stale = await summaries.state(root, folder, config)
            if stale is not None:
                result["summary_stale"] = stale
        return result

    @mcp.tool(annotations=READ_ONLY)
    def search(query: str, path: str = "", limit: int = 50, regex: bool = False) -> list[Hit]:
        """Search the brain for a string (literal by default; regex=True to
        opt in). Hits carry path, line, and the matching line's text."""
        start = confine(root, path, allow_root=True)
        if not start.exists():
            raise ToolError(f"no such path '{path}'")
        return searchio.search(root, query, start, limit, regex)

    @mcp.tool(name="glob", annotations=READ_ONLY)
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

    # write: same content twice is a documented no-op (see below), so it's
    # idempotent despite overwriting existing content.
    OVERWRITES_IDEMPOTENT = {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    # edit/move/delete: repeating with the same args fails the second time (the
    # old text, or the source/target path, no longer exists), so not idempotent.
    OVERWRITES_NOT_IDEMPOTENT = {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }

    @mcp.tool(annotations=OVERWRITES_IDEMPOTENT)
    @git_guard
    async def write(path: str, content: str, base_hash: str | None = None) -> MutationResult:
        """Create or overwrite a file. Parent directories are created.
        Overwriting an existing file requires `base_hash` — the `hash` that
        read returned for it. Prefer edit to change part of one."""
        target = confine(root, path)
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory; give a file path")
        r = rel(root, target)
        async with write_lock:
            # Inside the lock so the check can't race a hand edit.
            if target.is_file():
                require_base_hash(base_hash, r, read_text(target, path), required=True)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            except OSError as exc:
                raise ToolError(f"cannot write '{path}': {exc}") from exc
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

    @mcp.tool(annotations=OVERWRITES_NOT_IDEMPOTENT)
    @git_guard
    async def edit(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        base_hash: str | None = None,
    ) -> EditResult:
        """Replace exact text in a file. `old_string` must match the file
        byte-for-byte, including whitespace and indentation, and must be unique
        unless `replace_all` is set. Read the file first; pass the `hash` read
        returned as `base_hash` to also catch changes elsewhere in the file.
        Prefer this over write for changing part of an existing file."""
        if not old_string:
            raise ToolError(
                "old_string is empty; use write to create a file, or give the exact text to replace"
            )
        if old_string == new_string:
            raise ToolError("old_string and new_string are identical; nothing to replace")
        target = confine(root, path)
        if target.is_dir():
            raise ToolError(f"'{path}' is a directory; give a file path")
        r = rel(root, target)
        async with write_lock:
            # Read, verify and write all inside the lock: concurrent edits to
            # one file must not lose updates.
            if not target.is_file():
                raise ToolError(f"no such file '{path}'")
            text = read_text(target, path)
            require_base_hash(base_hash, r, text, required=False)
            count = text.count(old_string)
            if count == 0:
                raise ToolError(
                    f"old_string not found in '{path}'; it must match the file exactly, "
                    f"including whitespace and indentation — read the file and copy the "
                    f"text verbatim"
                )
            if count > 1 and not replace_all:
                raise ToolError(
                    f"old_string appears {count} times in '{path}'; add surrounding "
                    f"context to make it unique, or pass replace_all=true to replace "
                    f"every occurrence"
                )
            replacements = count if replace_all else 1
            new_text = text.replace(old_string, new_string, -1 if replace_all else 1)
            target.write_text(new_text, encoding="utf-8")
            if not await has_changes(root, [r]):
                return {
                    "path": r,
                    "commit": await head_sha(root),
                    "message": "no change: result identical to the last commit",
                    "replacements": replacements,
                }
            await run_git(root, "add", "--", r)
            msg = commit_message("edit", r)
            sha = await commit_paths(root, [r], msg)
        return {
            "path": r,
            "commit": sha,
            "message": msg.splitlines()[0],
            "replacements": replacements,
        }

    @mcp.tool(annotations=OVERWRITES_NOT_IDEMPOTENT)
    @git_guard
    async def move(src: str, dst: str) -> MutationResult:
        """Move or rename via git mv. Parent directories are created;
        an existing destination file is overwritten. A source directory left
        empty by the move is removed."""
        source = confine(root, src)
        if not source.exists():
            raise ToolError(f"no such path '{src}'")
        dest = confine(root, dst)
        r_src, r_dst = rel(root, source), rel(root, dest)
        if r_src == r_dst:
            raise ToolError("src and dst are the same path")
        async with write_lock:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ToolError(f"cannot move to '{dst}': {exc}") from exc
            await run_git(root, "mv", "-f", "--", r_src, r_dst)
            msg = commit_message("move", f"{r_src} -> {r_dst}")
            sha = await commit_paths(root, [r_src, r_dst], msg)
            # Inside the lock: another writer must not be filling this
            # directory while we decide it is empty.
            prune_empty_dirs(root, source.parent)
        return {"path": r_dst, "commit": sha, "message": msg.splitlines()[0]}

    @mcp.tool(annotations=OVERWRITES_NOT_IDEMPOTENT)
    @git_guard
    async def delete(path: str) -> MutationResult:
        """Delete a file (or directory, recursively). A parent directory left
        empty by the delete is removed too."""
        target = confine(root, path)
        if not target.exists():
            raise ToolError(f"no such path '{path}'")
        r = rel(root, target)
        rm_args = ["rm", "-q", "-r", "--"] if target.is_dir() else ["rm", "-q", "--"]
        async with write_lock:
            if target.is_dir() and not any(target.iterdir()):
                # git cannot track an empty directory, so `git rm` has nothing
                # to match and fails. There is also nothing to lose and nothing
                # to commit: just remove it. Without this the server can create
                # a stray empty directory (see move) that it cannot then clean
                # up, leaving the only fix outside the API.
                try:
                    target.rmdir()
                except OSError as exc:
                    raise ToolError(f"cannot remove directory '{path}': {exc}") from exc
                removed = [r, *prune_empty_dirs(root, target.parent)]
                what = "directories" if len(removed) > 1 else "directory"
                return {
                    "path": r,
                    "commit": await head_sha(root),
                    "message": f"removed empty {what}: " + ", ".join(removed),
                }
            await run_git(root, *rm_args, r)
            msg = commit_message("delete", r)
            sha = await commit_paths(root, [r], msg)
            prune_empty_dirs(root, target.parent)
        return {"path": r, "commit": sha, "message": msg.splitlines()[0]}

    projects_module.register(mcp, config, attention)
    return mcp
