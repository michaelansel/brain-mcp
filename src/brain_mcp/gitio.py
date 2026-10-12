"""git subprocess helpers. All git calls are rooted at the brain.

Mutating tools serialize through `write_lock`: acquire it, change the
filesystem, stage exactly the affected paths, commit, release. Commits are
restricted to the affected paths so unrelated dirty or staged files in the
brain are never swept in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# A commit-ish this server will hand to git. Anything else is refused before
# it reaches a subprocess argument.
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,40}")

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


# Post-commit command state, parallel to push. None disables it; set once at
# server build time.
_post_commit_cmd: str | None = None
# Its own lock for the same reason push has one: a slow run never blocks a writer.
_post_commit_lock = asyncio.Lock()
# True while a run is waiting for the lock. One waiter is enough — it has not
# started yet, so it will see every commit made before it does.
_post_commit_queued = False
_pending_post_commits: set[asyncio.Task[None]] = set()
# Seconds before a run is killed. A hung command would otherwise hold the lock
# forever and silently stop every later run.
POST_COMMIT_TIMEOUT = 120.0


def configure_push(remote: str | None) -> None:
    """Set (or clear) the remote every commit pushes to. None disables push."""
    global _push_remote
    _push_remote = remote


def configure_post_commit(cmd: str | None) -> None:
    """Set (or clear) the executable run after every commit. None disables it."""
    global _post_commit_cmd
    _post_commit_cmd = cmd


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
    blocking or failing this call. A configured post-commit command is
    started the same way.
    """
    await run_git(root, "commit", "-q", "-m", message, "--", *paths)
    proc = await run_git(root, "rev-parse", "HEAD")
    sha = proc.stdout.strip()
    if _push_remote:
        task = asyncio.create_task(_push_head(root))
        _pending_pushes.add(task)
        task.add_done_callback(_pending_pushes.discard)
    if _post_commit_cmd:
        task = asyncio.create_task(_run_post_commit(root))
        _pending_post_commits.add(task)
        task.add_done_callback(_pending_post_commits.discard)
    return sha


async def _run_post_commit(root: Path) -> None:
    """Best-effort run of the configured post-commit command.

    Never raises. The command is executed directly — no shell, no arguments —
    with the brain as its working directory and `BRAIN_ROOT` in its
    environment. It is told nothing about which commit triggered it and reads
    HEAD for itself, which is what lets a burst of commits coalesce: while one
    run is in flight at most one more waits, and that one sees them all.
    """
    global _post_commit_queued
    cmd = _post_commit_cmd
    if not cmd or _post_commit_queued:
        return
    _post_commit_queued = True
    try:
        await _post_commit_lock.acquire()
    finally:
        _post_commit_queued = False
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            cwd=root,
            env={**os.environ, "BRAIN_ROOT": str(root)},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # its own process group, so a kill reaches its children
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), POST_COMMIT_TIMEOUT)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            logger.warning("post-commit command %s killed after %ss", cmd, POST_COMMIT_TIMEOUT)
            return
        if proc.returncode == 0:
            logger.info("post-commit command %s succeeded", cmd)
        else:
            logger.warning(
                "post-commit command %s failed (exit %s): %s",
                cmd,
                proc.returncode,
                out.decode("utf-8", errors="replace").strip()[-2000:],
            )
    except Exception:
        logger.exception("unexpected error running post-commit command %s", cmd)
    finally:
        _post_commit_lock.release()


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


# --- history queries (read-only; no lock, nothing staged) ---


async def last_commit_touching(root: Path, path: str) -> tuple[str, str] | None:
    """(sha, ISO date) of the newest commit touching `path`, or None if the
    path has no commit history — it was created by hand and never committed."""
    proc = await run_git(root, "log", "-1", "--format=%H %cI", "--", path, check=False)
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return None
    sha, _, when = out.partition(" ")
    return sha, when


async def is_usable_baseline(root: Path, sha: str) -> bool:
    """Whether `sha` names a commit this history can be measured from.

    One call answers both ways it can be unusable: `merge-base --is-ancestor`
    exits non-zero for a sha git cannot resolve *and* for a real commit that
    is not behind HEAD. A summary claiming either is a summary whose coverage
    cannot be trusted, and both want the same response.
    """
    if not _SHA_RE.fullmatch(sha):
        return False
    proc = await run_git(root, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
    return proc.returncode == 0


async def commit_date(root: Path, sha: str) -> str:
    """The ISO commit date of `sha`, or "" if it cannot be read. Screen the
    sha with is_usable_baseline first."""
    proc = await run_git(root, "log", "-1", "--format=%cI", sha, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


async def changed_since(root: Path, sha: str, pathspec: str) -> list[str]:
    """Paths under `pathspec` changed since `sha`, as "<status> <path>".

    Covers both halves of how the brain changes: commits made since `sha`,
    and edits sitting in the working tree. The user edits these files in his
    own editor and only this server commits, so a hand edit can stay
    uncommitted indefinitely — a diff of commits alone would miss it.

    `--no-renames` keeps a rename as a D/A pair, which is what a summary
    needs to hear: a note left and another arrived.
    """
    # check=False: an unresolvable sha exits non-zero, and `read` carries no
    # git_guard, so a raised GitError would escape the tool as a raw traceback.
    # Callers screen the sha with is_usable_baseline first; this is the belt.
    committed = await run_git(
        root,
        "diff",
        "--name-status",
        "--no-renames",
        sha,
        "HEAD",
        "--",
        pathspec,
        check=False,
    )
    working = await run_git(root, "status", "--porcelain", "--", pathspec)

    changes: dict[str, str] = {}
    for line in committed.stdout.splitlines():
        status, _, path = line.partition("\t")
        if path:
            changes[path] = status.strip()[:1]
    for line in working.stdout.splitlines():
        # porcelain v1: XY<space>path, where X is staged and Y is unstaged.
        status, path = line[:2], line[3:].strip().strip('"')
        if not path:
            continue
        code = status.replace(" ", "").replace("?", "A")[:1]
        # A working-tree change is newer than anything in the diff, so it wins.
        changes[path] = code
    return sorted(f"{code} {path}" for path, code in changes.items())


async def commit_times(root: Path, pathspec: str) -> list[int]:
    """Unix timestamps of every commit touching `pathspec`, newest first."""
    proc = await run_git(root, "log", "--format=%ct", "--", pathspec, check=False)
    if proc.returncode != 0:
        return []
    return [int(line) for line in proc.stdout.split()]


async def commit_times_since(root: Path, sha: str, pathspec: str) -> list[int]:
    """Unix timestamps of commits after `sha` touching `pathspec`, newest first."""
    proc = await run_git(root, "log", "--format=%ct", f"{sha}..HEAD", "--", pathspec, check=False)
    if proc.returncode != 0:
        return []
    return [int(line) for line in proc.stdout.split()]
