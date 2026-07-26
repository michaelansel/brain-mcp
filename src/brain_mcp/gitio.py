"""git subprocess helpers. All git calls are rooted at the brain.

Mutating tools serialize through `write_lock`: acquire it, change the
filesystem, stage exactly the affected paths, commit, release. Commits are
restricted to the affected paths so unrelated dirty or staged files in the
brain are never swept in.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# One lock for every mutate-and-commit; concurrent writes must not interleave.
write_lock = asyncio.Lock()

# Identity overrides so commits never fail on missing git config.
_IDENTITY = [
    "-c",
    "user.name=brain-mcp",
    "-c",
    "user.email=brain-mcp@localhost",
]

# Push-on-commit state. None disables push; set once at server build time.
_push_remote: str | None = None
# Separate from write_lock so a slow/hung push never blocks the next writer.
_push_lock = asyncio.Lock()
# Strong refs to fire-and-forget push tasks so they aren't garbage-collected
# mid-flight.
_pending_pushes: set[asyncio.Task[None]] = set()
# Best-known last successfully pushed sha, for drift logging on failure.
_last_pushed_sha: str | None = None


def configure_push(remote: str | None) -> None:
    """Set (or clear) the remote every commit pushes to. None disables push."""
    global _push_remote
    _push_remote = remote


class GitError(Exception):
    """A git subprocess failed."""


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), *_IDENTITY, *args],
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


async def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(_run, root, *args, check=check)


def commit_message(tool: str, path_desc: str) -> str:
    return f"{tool}: {path_desc}\n\nvia brain-mcp"


async def has_changes(root: Path, paths: list[str]) -> bool:
    proc = await run_git(root, "status", "--porcelain", "--", *paths)
    return bool(proc.stdout.strip())


async def head_sha(root: Path) -> str | None:
    proc = await run_git(root, "rev-parse", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


async def commit_paths(root: Path, paths: list[str], message: str) -> str:
    """Commit exactly `paths` (already staged or tracked) and return the sha.

    Caller must hold `write_lock`. The pathspec keeps unrelated staged
    changes out of the commit. If a push remote is configured, a push of
    the new HEAD is kicked off in the background — best-effort, never
    blocking or failing this call.
    """
    await run_git(root, "commit", "-q", "-m", message, "--", *paths)
    proc = await run_git(root, "rev-parse", "HEAD")
    sha = proc.stdout.strip()
    if _push_remote:
        task = asyncio.create_task(_push_head(root))
        _pending_pushes.add(task)
        task.add_done_callback(_pending_pushes.discard)
    return sha


async def _push_head(root: Path) -> None:
    """Best-effort push of current HEAD to the configured remote.

    Never raises. Serialized by `_push_lock` (distinct from `write_lock`)
    so concurrent pushes don't race the same remote refs; pushing HEAD by
    name rather than a captured sha means a backlog of queued pushes
    self-coalesces instead of needing per-commit bookkeeping.
    """
    global _last_pushed_sha
    remote = _push_remote
    try:
        async with _push_lock:
            proc = await run_git(root, "push", remote, "HEAD", check=False)
            local = (await run_git(root, "rev-parse", "HEAD")).stdout.strip()
            if proc.returncode == 0:
                _last_pushed_sha = local
                logger.info("pushed %s to %s", local, remote)
            else:
                logger.warning(
                    "push to %s failed (local HEAD %s, last known pushed %s): %s",
                    remote,
                    local,
                    _last_pushed_sha or "unknown",
                    (proc.stderr or proc.stdout).strip(),
                )
    except Exception:
        logger.exception("unexpected error pushing to %s", remote)
