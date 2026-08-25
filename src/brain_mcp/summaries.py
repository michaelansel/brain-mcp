"""Folder summaries: a `summary.md` sitting beside the notes it covers.

A folder is summarised iff it contains `summary.md` — presence is the opt-in,
so no directory is special-cased here. `project.md` stays the hand-written
manifest about intent; the summary is a state note about what the notes in
the folder currently amount to.

Freshness is derived from git on every call, never stored. The baseline is
simply the commit that last touched the summary, so a hand edit re-baselines
it and there is nothing to keep in sync.

Two things are reported, and they are deliberately separate. The *delta* is
information — an agent should always learn the summary is N notes behind.
The *refresh request* is a judgement call, and only worth making when the
summary has drifted far enough to mislead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from brain_mcp.config import BrainConfig
from brain_mcp.gitio import changed_since, commit_times_since, has_changes, last_commit_touching

SUMMARY_NAME = "summary.md"


class SummaryState(TypedDict):
    since: str  # ISO date the summary was last written
    changed: list[str]  # "<M|A|D> <brain-relative path>"
    refresh: NotRequired[str]  # only when a rewrite is worth asking for


def summary_path(folder: str) -> str:
    return f"{folder}/{SUMMARY_NAME}" if folder else SUMMARY_NAME


async def state(root: Path, folder: str, config: BrainConfig) -> SummaryState | None:
    """How far `folder`'s summary has fallen behind its notes.

    None when there is nothing to say: no summary, no history to compare
    against, or a summary that is still current.
    """
    summary = summary_path(folder)
    if not (root / summary).is_file():
        return None
    baseline = await last_commit_touching(root, summary)
    if baseline is None:
        # Written by hand and not yet committed: there is no point in history
        # to measure drift from, and the person who wrote it just read the
        # folder. Nothing to report.
        return None
    sha, when = baseline

    pathspec = folder or "."
    # The baseline is the *last* commit touching the summary, so no later
    # commit can contain it — but an uncommitted edit to the summary itself
    # would show up, and that is the author revising it, not drift.
    changed = [c for c in await changed_since(root, sha, pathspec) if c[2:] != summary]
    if not changed:
        return None

    result: SummaryState = {"since": when[:10], "changed": changed}
    ask = _refresh_ask(changed, when, config)
    if ask and not await _in_flight(root, sha, pathspec, config):
        result["refresh"] = ask
    return result


def _refresh_ask(changed: list[str], when: str, config: BrainConfig) -> str | None:
    """The sentence handed to the agent, or None if the drift is not yet
    worth interrupting for. Any one condition is enough."""
    structural = [c for c in changed if c[0] in ("A", "D")]
    written = datetime.fromisoformat(when)
    age_days = (datetime.now(tz=written.tzinfo) - written).days

    if not (
        structural
        or len(changed) >= config.summary_min_files
        or age_days >= config.summary_max_age_days
    ):
        return None

    n = len(changed)
    return (
        f"This summary is {n} change{'s' if n != 1 else ''} behind the notes it "
        f"covers (last written {when[:10]}). Read what changed, then rewrite the "
        f"summary and write it back with base_hash. The notes themselves remain "
        f"authoritative — the summary is a state note about them."
    )


async def _in_flight(root: Path, sha: str, pathspec: str, config: BrainConfig) -> bool:
    """True when the whole delta is one burst of work still in progress.

    Asking then is pure noise: whoever made these changes is still here and
    already knows about them. Judged on commit times only — every agent write
    commits, so an *uncommitted* change is a hand edit, which is exactly the
    kind of drift worth surfacing rather than suppressing.
    """
    if await has_changes(root, [pathspec]):
        return False
    times = await commit_times_since(root, sha, pathspec)
    if not times:
        return False
    oldest = min(times)
    age_minutes = (datetime.now().timestamp() - oldest) / 60
    return age_minutes < config.summary_quiet_minutes
