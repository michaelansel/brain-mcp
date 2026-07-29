"""Per-session ledger of what each client has read.

This is a precondition ledger, not a cache: nothing stored here is ever served
as content. Only content hashes live here, and the only question they answer is
"has this session seen this file, and is it still what they saw?" — so tools
still read current state from disk on every call.

State is process-local and per MCP session. A restart clears it; the cost is
one extra `read` before the next write in each surviving session.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

# Bounds, since an HTTP server sees many sessions over its lifetime. Worst case
# is ~64 * 256 * ~200 bytes ≈ 3 MB. Eviction is LRU on both levels; an evicted
# entry costs one extra read and heals itself.
MAX_SESSIONS = 64
MAX_PATHS = 256

# session id -> {brain-relative path -> sha256 of the content that session saw}
_sessions: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(session: str, path: str, content: str) -> None:
    """Remember that `session` has seen `content` at `path`."""
    paths = _sessions.get(session)
    if paths is None:
        paths = _sessions[session] = OrderedDict()
        if len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    _sessions.move_to_end(session)
    paths[path] = digest(content)
    paths.move_to_end(path)
    if len(paths) > MAX_PATHS:
        paths.popitem(last=False)


def seen(session: str, path: str) -> str | None:
    """The hash `session` last saw at `path`, or None if it never read it."""
    paths = _sessions.get(session)
    if paths is None:
        return None
    hash_ = paths.get(path)
    if hash_ is None:
        return None
    _sessions.move_to_end(session)
    paths.move_to_end(path)
    return hash_


def forget(path: str) -> None:
    """Drop `path` and anything under it, for every session.

    Called after move/delete: whatever any session read at that path is no
    longer what lives there.
    """
    prefix = path + "/"
    for paths in _sessions.values():
        for known in [p for p in paths if p == path or p.startswith(prefix)]:
            del paths[known]


def reset() -> None:
    """Drop all state. For tests."""
    _sessions.clear()
