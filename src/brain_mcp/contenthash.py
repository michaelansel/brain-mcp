"""Content hashing for the read-before-write check.

`read` hands the caller a hash alongside the content; `write` and `edit` take
it back as `base_hash` and compare it against what's on disk right now. The
proof therefore travels with the request instead of living in server memory —
the server keeps no state between calls, so the check survives client
reconnects, restarts, and clients that open a fresh MCP session per tool call.
"""

from __future__ import annotations

import hashlib


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
