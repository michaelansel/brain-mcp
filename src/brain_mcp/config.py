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

    port_raw = env.get("BRAIN_MCP_PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ConfigError(f"BRAIN_MCP_PORT '{port_raw}' is not a number") from exc

    token = env.get("BRAIN_MCP_TOKEN") or None
    emails_raw = env.get("BRAIN_MCP_EXEDEV_EMAILS", "")
    exedev_emails = frozenset(e.strip().casefold() for e in emails_raw.split(",") if e.strip())
    if token and exedev_emails:
        raise ConfigError(
            "BRAIN_MCP_TOKEN and BRAIN_MCP_EXEDEV_EMAILS are mutually exclusive; pick one auth mode"
        )

    return BrainConfig(
        root=root,
        token=token,
        exedev_emails=exedev_emails,
        host=env.get("BRAIN_MCP_HOST", "127.0.0.1"),
        port=port,
    )
