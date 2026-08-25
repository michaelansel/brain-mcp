"""Configuration from environment. Fails loudly at startup on a bad BRAIN_ROOT."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from brain_mcp.pathsafe import resolve_root


class ConfigError(Exception):
    """Raised at startup when the environment is unusable."""


@dataclass(frozen=True)
class BrainConfig:
    root: Path  # resolved brain root
    token: str | None = None  # static bearer token for HTTP; None disables auth
    exedev_emails: frozenset[str] = frozenset()  # allowlist for exe.dev proxy identity
    host: str = "127.0.0.1"
    port: int = 8000
    git_remote: str | None = None  # remote name/URL to push HEAD to after each commit
    git_push: bool = True  # escape hatch: pause pushing without unsetting git_remote
    read_before_write: bool = True  # require a prior read before changing an existing file
    # When a folder summary is far enough behind its notes to be worth a rewrite.
    # Any one of these is enough; see summaries.py.
    summary_min_files: int = 3  # changed files that make drift worth mentioning
    summary_max_age_days: int = 30  # matches the stale-state threshold in weekly-review.md
    summary_quiet_minutes: int = 60  # don't interrupt a burst of work still in progress
    # Read tracking. Lives outside the brain; see attention.py.
    state_dir: Path | None = None  # None means the XDG default
    attention_days: int = 90  # how long a read event is kept


def load_config(env: Mapping[str, str] | None = None) -> BrainConfig:
    if env is None:
        env = os.environ

    raw = env.get("BRAIN_ROOT")
    if not raw:
        raise ConfigError("BRAIN_ROOT is not set; point it at the brain directory")

    root = Path(raw).expanduser()
    if not root.is_dir():
        raise ConfigError(f"BRAIN_ROOT '{raw}' does not exist or is not a directory")
    root = resolve_root(root)

    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ConfigError(f"BRAIN_ROOT '{raw}' is not a git repository") from exc
    toplevel = Path(proc.stdout.strip()).resolve()
    if toplevel != root:
        raise ConfigError(
            f"BRAIN_ROOT '{raw}' is not the top level of a git repository "
            f"(found repo at '{toplevel}')"
        )

    port = _number(env, "BRAIN_MCP_PORT", 8000)

    token = env.get("BRAIN_MCP_TOKEN") or None
    emails_raw = env.get("BRAIN_MCP_EXEDEV_EMAILS", "")
    exedev_emails = frozenset(e.strip().casefold() for e in emails_raw.split(",") if e.strip())
    if token and exedev_emails:
        raise ConfigError(
            "BRAIN_MCP_TOKEN and BRAIN_MCP_EXEDEV_EMAILS are mutually exclusive; pick one auth mode"
        )

    git_remote = env.get("BRAIN_MCP_GIT_REMOTE") or None
    push_raw = env.get("BRAIN_MCP_GIT_PUSH", "true").strip().lower()
    if push_raw not in ("true", "false"):
        raise ConfigError(f"BRAIN_MCP_GIT_PUSH '{push_raw}' must be 'true' or 'false'")
    git_push = push_raw == "true"

    rbw_raw = env.get("BRAIN_MCP_READ_BEFORE_WRITE", "true").strip().lower()
    if rbw_raw not in ("true", "false"):
        raise ConfigError(f"BRAIN_MCP_READ_BEFORE_WRITE '{rbw_raw}' must be 'true' or 'false'")
    read_before_write = rbw_raw == "true"

    return BrainConfig(
        root=root,
        token=token,
        exedev_emails=exedev_emails,
        host=env.get("BRAIN_MCP_HOST", "127.0.0.1"),
        port=port,
        git_remote=git_remote,
        git_push=git_push,
        read_before_write=read_before_write,
        summary_min_files=_number(env, "BRAIN_MCP_SUMMARY_MIN_FILES", 3),
        summary_max_age_days=_number(env, "BRAIN_MCP_SUMMARY_MAX_AGE_DAYS", 30),
        summary_quiet_minutes=_number(env, "BRAIN_MCP_SUMMARY_QUIET_MINUTES", 60),
        state_dir=Path(state_raw).expanduser()
        if (state_raw := env.get("BRAIN_MCP_STATE_DIR"))
        else None,
        attention_days=_number(env, "BRAIN_MCP_ATTENTION_DAYS", 90),
    )


def _number(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} '{raw}' is not a number") from exc
