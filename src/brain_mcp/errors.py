"""Error helpers: git failures become ToolErrors an LLM can act on,
never bare stack traces."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastmcp.exceptions import ToolError

from brain_mcp.gitio import GitError

__all__ = ["ToolError", "git_guard"]

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def git_guard(fn: F) -> F:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except GitError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]
