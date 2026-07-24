"""git subprocess helpers. All git calls are rooted at the brain.

Mutating tools serialize through `write_lock`: acquire it, change the
filesystem, stage exactly the affected paths, commit, release. Commits are
restricted to the affected paths so unrelated dirty or staged files in the
brain are never swept in.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

# One lock for every mutate-and-commit; concurrent writes must not interleave.
write_lock = asyncio.Lock()

# Identity overrides so commits never fail on missing git config.
_IDENTITY = [
    "-c",
    "user.name=brain-mcp",
    "-c",
    "user.email=brain-mcp@localhost",
]


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
    changes out of the commit.
    """
    await run_git(root, "commit", "-q", "-m", message, "--", *paths)
    proc = await run_git(root, "rev-parse", "HEAD")
    return proc.stdout.strip()
