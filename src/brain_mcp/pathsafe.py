"""Path confinement for the brain root.

Every user-supplied path is relative to the brain root. `confine` resolves it
(including symlinks) and guarantees the result stays inside the root. This is
the security boundary that makes remote exposure acceptable.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from fastmcp.exceptions import ToolError

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def resolve_root(root: Path) -> Path:
    """Resolve the brain root once at startup. Must exist."""
    return root.resolve(strict=True)


def confine(root: Path, user_path: str, *, allow_root: bool = False) -> Path:
    """Resolve `user_path` against `root` and verify it stays inside.

    `root` must already be resolved (see `resolve_root`). Returns the resolved
    absolute path. Raises ToolError for absolute paths, `..` escapes, symlinks
    pointing outside the root, `.git` access, and encoded traversal variants.
    """
    _check_lexical(user_path, allow_root=allow_root)

    # Percent-encoded traversal ("%2e%2e/x") must not slip through if a layer
    # above ever decodes it: the decoded form must pass the same lexical checks.
    decoded = urllib.parse.unquote(user_path)
    if decoded != user_path:
        _check_lexical(decoded, allow_root=allow_root)

    resolved = (root / user_path).resolve()
    if not (resolved == root or resolved.is_relative_to(root)):
        raise ToolError(f"path '{user_path}' escapes the brain root; paths must stay inside it")
    if resolved == root and not allow_root:
        raise ToolError("path must name a file or directory inside the brain root")
    if ".git" in resolved.relative_to(root).parts:
        raise ToolError("'.git' is not accessible through the brain")
    return resolved


def _check_lexical(user_path: str, *, allow_root: bool) -> None:
    if "\x00" in user_path:
        raise ToolError("path contains a NUL byte")
    if "\\" in user_path:
        raise ToolError("backslashes are not allowed in paths; use '/' separators")
    if user_path.startswith("~"):
        raise ToolError("'~' expansion is not allowed; use a path relative to the brain root")
    if user_path.startswith("/") or _DRIVE_RE.match(user_path):
        raise ToolError("absolute paths are not allowed; use a path relative to the brain root")
    if user_path == "" and not allow_root:
        raise ToolError("a non-empty path is required")
    parts = [p for p in user_path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ToolError("'..' is not allowed in paths")
    if ".git" in parts:
        raise ToolError("'.git' is not accessible through the brain")


def rel(root: Path, resolved: Path) -> str:
    """Root-relative POSIX form of a confined path, for display and git."""
    if resolved == root:
        return ""
    return resolved.relative_to(root).as_posix()


def prune_empty_dirs(root: Path, start: Path) -> list[str]:
    """Remove `start` and its ancestors while they are empty, stopping before
    the top level. Returns what was removed, root-relative, innermost first.

    git cannot track an empty directory, so moving or deleting the last file
    out of one leaves it behind in the working tree with nothing to commit —
    a folder that exists but holds nothing, which agents.md says should not
    exist. Nothing here touches git, because git never knew about it.

    A direct child of the root is never pruned. Those are the brain's
    skeleton (inbox, projects, areas, resources, archive), and reaping one
    the moment it empties would make deleting a single note remove `inbox/`.
    That is a rule about depth, not about which directories they are, so no
    name is special-cased. Removing one on purpose still works: name it.

    Nothing outside the root is touched; `start` must already be confined.
    """
    removed = []
    current = start
    while current.parent != root and root in current.parents:
        try:
            current.rmdir()  # refuses on a non-empty directory, which is the guard
        except OSError:
            break
        removed.append(rel(root, current))
        current = current.parent
    return removed
