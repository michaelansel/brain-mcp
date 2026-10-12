"""Folder summaries: a `summary.md` sitting beside the notes it covers.

A folder is summarised iff it contains `summary.md` — presence is the opt-in,
so no directory is special-cased here. `project.md` stays the hand-written
manifest about intent; the summary is a state note about what the notes in
the folder currently amount to.

The summary says how much it accounts for, in a `covers:` line naming a
commit. That claim, not the file's own history, is what freshness is measured
from — so editing the file and asserting coverage are separate acts. A typo
fix no longer silently marks six drifted notes as integrated, and a refresher
that only got through half the job simply does not advance the line, leaving
the rest flagged for whoever comes next. Partial work is then incomplete
rather than lossy, which is the property worth having: nothing can be
absorbed without someone claiming it.

Two things are reported, and they are deliberately separate. The *delta* is
information — an agent should always learn the summary is N notes behind.
The *refresh request* is a judgement call, and only worth making when the
summary has drifted far enough to mislead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from brain_mcp import frontmatter
from brain_mcp.config import BrainConfig
from brain_mcp.gitio import (
    changed_since,
    commit_date,
    commit_times,
    commit_times_since,
    has_changes,
    head_sha,
    is_usable_baseline,
    last_commit_touching,
)

SUMMARY_NAME = "summary.md"
COVERS_KEY = "covers"

# git's empty tree. Diffing against it lists every file as an addition, which
# is precisely what a summary stating no coverage accounts for: nothing.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class SummaryState(TypedDict):
    since: str  # ISO date of the commit this summary accounts for
    changed: list[str]  # "<M|A|D> <brain-relative path>"
    coverage: NotRequired[str]  # only when the summary claims none we can use
    refresh: NotRequired[str]  # only when a rewrite is worth asking for


def summary_path(folder: str) -> str:
    return f"{folder}/{SUMMARY_NAME}" if folder else SUMMARY_NAME


async def state(root: Path, folder: str, config: BrainConfig) -> SummaryState | None:
    """How far `folder`'s summary has fallen behind its notes.

    None when there is nothing to say: no summary, nothing to measure from,
    or a summary that is still current.
    """
    summary = summary_path(folder)
    if not (root / summary).is_file():
        return None

    base = await _baseline(root, summary)
    if base is None:
        # Written by hand and never committed: there is no point in history
        # to measure drift from, and whoever just wrote it has read the
        # folder. Nothing to report.
        return None
    sha, when, why = base

    pathspec = folder or "."
    # An uncommitted edit to the summary itself is the author revising it,
    # not drift, so it never counts against its own coverage.
    changed = [c for c in await changed_since(root, sha, pathspec) if c[2:] != summary]
    if not changed:
        return None

    result: SummaryState = {"since": when[:10], "changed": changed}
    if why is not None:
        result["coverage"] = why
    ask = _refresh_ask(changed, when, config, unclaimed=why is not None)
    if ask and not await _in_flight(root, sha, pathspec, config):
        result["refresh"] = ask.format(sha=await head_sha(root) or "HEAD")
    return result


async def _baseline(root: Path, summary: str) -> tuple[str, str, str | None] | None:
    """(baseline, ISO date the summary was written, why-it-is-not-a-claim).

    The third value is None when the summary states coverage this history can
    be measured from. Otherwise the baseline is the empty tree — a summary
    that cannot say what it accounts for accounts for nothing, and every note
    beside it is listed as unaccounted-for until a refresh records a claim.

    Falling back to "the last commit that touched this file" would be the
    lossy behaviour this module exists to remove: it would let a rewrite that
    claims nothing quietly absorb everything pending.
    """
    written = await last_commit_touching(root, summary)
    if written is None:
        return None  # never committed; nothing to measure from
    claimed = (frontmatter.read_head(root / summary) or {}).get(COVERS_KEY)
    if claimed is not None:
        sha = str(claimed).strip()
        if await is_usable_baseline(root, sha):
            return sha, await commit_date(root, sha), None
        why = (
            f"this summary claims to cover '{sha}', which is not a commit "
            f"behind HEAD, so nothing it says can be trusted as accounted for"
        )
    else:
        why = (
            "this summary does not say what it covers, so nothing is counted "
            "as accounted for; a refresh will record a covers: line"
        )
    return EMPTY_TREE, written[1], why


def _refresh_ask(
    changed: list[str], when: str, config: BrainConfig, *, unclaimed: bool
) -> str | None:
    """The request handed to the agent, or None if the drift is not yet worth
    interrupting for. Any one condition is enough.

    Returned with `{sha}` unfilled: the value is current HEAD, and reading it
    is a git call worth skipping when nothing will be asked.
    """
    structural = [c for c in changed if c[0] in ("A", "D")]
    written = datetime.fromisoformat(when) if when else None
    age_days = (datetime.now(tz=written.tzinfo) - written).days if written else 0

    if not (
        unclaimed  # it cannot say what it covers; getting that recorded is worth a pass
        or structural
        or len(changed) >= config.summary_min_files
        or age_days >= config.summary_max_age_days
    ):
        return None

    n = len(changed)
    return (
        f"This summary accounts for the folder as of {when[:10]}, and {n} "
        f"change{'s have' if n != 1 else ' has'} landed since — every one is "
        f"listed in `changed`. Account for all of them, not only the ones "
        f"relevant to what you are doing right now. Hand this to a subagent "
        f"if you can, since it wants a clean context; otherwise read the "
        f"changed notes yourself. Then rewrite the summary, set "
        f"`covers: {{sha}}` in its frontmatter, and write it back with "
        f"base_hash. If you cannot do the whole job, leave it alone — a "
        f"partial rewrite that advances `covers:` hides everything it "
        f"skipped. See 'Folder summaries' in agents.md."
    )


async def first_ask(root: Path, folder: str, config: BrainConfig) -> str | None:
    """The request to write a folder's first summary, or None while it is
    still small enough to read cold.

    Size is measured two ways, and either is enough: how many commits have
    touched the folder, and how many characters its files hold. A folder
    worked over in many small edits and one that landed as a few long notes
    are both expensive to orient on without a summary.

    Not asked while the folder's whole history is one burst still under way:
    whoever is building it is here, and a summary of half-written work would
    be stale before it landed.
    """
    times = await commit_times(root, folder)
    chars = 0
    for path in (root / folder).rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            chars += len(path.read_text(encoding="utf-8", errors="replace"))
    if len(times) < config.summary_first_edits and chars < config.summary_first_chars:
        return None
    if times and (datetime.now().timestamp() - min(times)) / 60 < config.summary_quiet_minutes:
        return None
    return (
        f"This folder has no summary and has grown past the point where "
        f"reading it cold is cheap: {len(times)} edits and {chars:,} characters "
        f"across its files. Write {summary_path(folder)} as a state note on "
        f"what its notes currently amount to, with `covers: {{sha}}` in its "
        f"frontmatter. Hand this to a subagent if you can, since it wants a "
        f"clean context and has to read every note. If you cannot read them "
        f"all, leave it for the next pass. See 'Folder summaries' in agents.md."
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
    return (datetime.now().timestamp() - min(times)) / 60 < config.summary_quiet_minutes
