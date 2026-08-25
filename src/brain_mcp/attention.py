"""Which folders are actually being consulted.

git records every write to the brain and cannot see a single read, so there
is no way to tell a summary that gets opened every other day from one nobody
has looked at since it was written. That difference is what says which stale
summary matters. This is the only thing the server keeps that the brain does
not, and it exists for exactly that gap.

It is an event log, one row per read, rather than a last-read-per-folder.
Keeping only the latest would mean deciding *at write time* which reads are
interesting — a judgement baked into a place it can never be revisited. With
the events kept, frequency is available as a signal, later questions can be
asked without a migration, and a weekly pass that reads everything is simply
one read a week against daily work rather than a special case to exclude.

It lives outside the brain on purpose. Anything written inside would be a
commit, a push, and noise in the very activity count the weekly review uses
to notice when it has become the only thing still writing.

Nothing here raises. Losing a read event costs a little signal; failing a
`read` because a state directory is unwritable would cost the brain.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS reads ("
    "  ts INTEGER NOT NULL,"
    "  path TEXT NOT NULL,"
    "  tool TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS reads_path_ts ON reads (path, ts)",
)


def default_state_dir() -> Path:
    """XDG state directory: durable, machine-local, not configuration."""
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "brain-mcp"


class Attention:
    """The read log. One instance per server; connections are per-call.

    Opening per call keeps this safe under `--workers` and off any assumption
    about which thread a call lands on, and a local sqlite open costs far less
    than the git subprocesses already on this path.
    """

    def __init__(self, state_dir: Path, keep_days: int) -> None:
        self.path = state_dir / "attention.db"
        self.keep_days = keep_days
        self._broken = False  # complain once, then stay quiet

    # --- writing ---

    async def record(self, path: str, tool: str) -> None:
        await asyncio.to_thread(self._record, path, tool)

    def _record(self, path: str, tool: str) -> None:
        cutoff = int(time.time()) - self.keep_days * 86400
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO reads (ts, path, tool) VALUES (?, ?, ?)",
                    (int(time.time()), path, tool),
                )
                # Pruning on insert needs no scheduler and no separate pass.
                # At a few dozen rows a day this is never the slow part.
                db.execute("DELETE FROM reads WHERE ts < ?", (cutoff,))
        except (OSError, sqlite3.Error):
            self._complain("record a read of %s", path)

    # --- reading ---

    async def counts(self, paths: list[str]) -> dict[str, dict[str, object]]:
        return await asyncio.to_thread(self._counts, paths)

    def _counts(self, paths: list[str]) -> dict[str, dict[str, object]]:
        """`{path: {"reads": n, "last_read": iso}}` for paths ever read.

        Absent means never read inside the window, which is a real answer and
        is left to the caller to render rather than filled in with a zero.
        """
        if not paths:
            return {}
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT path, COUNT(*), MAX(ts) FROM reads "
                    f"WHERE path IN ({','.join('?' * len(paths))}) GROUP BY path",
                    paths,
                ).fetchall()
        except (OSError, sqlite3.Error):
            self._complain("read attention for %d paths", len(paths))
            return {}
        return {
            path: {
                "reads": n,
                "last_read": time.strftime("%Y-%m-%d", time.gmtime(last)),
            }
            for path, n, last in rows
        }

    # --- plumbing ---

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        for statement in _SCHEMA:
            db.execute(statement)
        return db

    def _complain(self, what: str, *args: object) -> None:
        if not self._broken:
            self._broken = True
            logger.warning(
                "attention tracking is off (%s): could not " + what,
                self.path,
                *args,
                exc_info=True,
            )
