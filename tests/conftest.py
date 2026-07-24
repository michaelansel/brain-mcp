"""Shared fixtures: every test gets a scratch brain — a real git repo in
tmp_path with a couple of committed files — and an in-memory MCP client."""

import subprocess

import pytest
from fastmcp import Client

from brain_mcp.config import BrainConfig
from brain_mcp.server import build_server


def git(root, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def commit_all(root, message="seed"):
    git(root, "add", "-A")
    git(root, "-c", "user.name=test", "-c", "user.email=test@test", "commit", "-q", "-m", message)


@pytest.fixture
def brain(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    git(root, "init", "-q")
    (root / "agents.md").write_text("# Conventions\n\nBe tidy.\n")
    (root / "inbox").mkdir()
    (root / "inbox" / "idea.md").write_text("# Big idea\n\nBuild a rocket.\n")
    (root / "areas").mkdir()
    (root / "areas" / "health.md").write_text("# Health\n\n## Sleep\n\nMore.\n")
    commit_all(root)
    return root


@pytest.fixture
def config(brain):
    return BrainConfig(root=brain.resolve())


@pytest.fixture
def server(config):
    return build_server(config)


@pytest.fixture
async def client(server):
    async with Client(server) as c:
        yield c


def data(result):
    """Unwrap a CallToolResult into plain JSON data."""
    sc = result.structured_content
    if sc is not None and set(sc) == {"result"}:
        return sc["result"]
    return sc


def head_sha(root):
    return git(root, "rev-parse", "HEAD").stdout.strip()


def commit_files(root, sha):
    """Paths touched by a commit."""
    out = git(root, "show", "--name-only", "--no-renames", "--format=", sha).stdout
    return sorted(line for line in out.splitlines() if line.strip())


def commit_message_of(root, sha):
    return git(root, "show", "-s", "--format=%B", sha).stdout.strip("\n")
