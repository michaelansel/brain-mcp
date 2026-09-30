"""Shared fixtures: every test gets a scratch brain — a real git repo in
tmp_path with a couple of committed files — and an in-memory MCP client."""

import asyncio
import subprocess

import pytest
from fastmcp import Client

from brain_mcp import gitio
from brain_mcp.config import BrainConfig
from brain_mcp.server import build_server


@pytest.fixture(autouse=True)
async def _reset_gitio_state():
    """gitio's locks/push-tracking are module-level, and `write_lock` is
    imported by name into server.py/projects.py — replacing the module
    attribute wouldn't reach those already-bound references, so the same
    Lock objects must be reused for the life of the process. A lock that
    ever contends binds to that test's event loop (asyncio.Lock._loop);
    pytest-asyncio gives each test a fresh loop, so a stale binding breaks
    the next test that contends the same lock. Clear that binding before
    each test instead of replacing the object, and reset push state too."""
    gitio.write_lock._loop = None
    gitio._push_lock._loop = None
    gitio._post_commit_lock._loop = None
    gitio.configure_push(None)
    gitio.configure_post_commit(None)
    gitio._last_pushed_sha = None
    gitio._post_commit_queued = False
    yield
    pending = gitio._pending_pushes | gitio._pending_post_commits
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
def config(brain, tmp_path):
    # state_dir must never default here: the real one is the user's own
    # ~/.local/state, and a test run would write read events into it.
    return BrainConfig(root=brain.resolve(), state_dir=tmp_path / "state")


@pytest.fixture
def server(config):
    return build_server(config)


@pytest.fixture
async def client(server):
    async with Client(server) as c:
        yield c


@pytest.fixture
async def other_client(server):
    """A second client on the same server — a distinct MCP session. The gate
    carries no session state, so a hash from one works in the other; this is
    what makes the server usable from a client that opens a fresh session per
    tool call."""
    async with Client(server) as c:
        yield c


def data(result):
    """Unwrap a CallToolResult into plain JSON data."""
    sc = result.structured_content
    if sc is not None and set(sc) == {"result"}:
        return sc["result"]
    return sc


async def read_hash(client, path):
    """The `hash` read returns for `path` — what write/edit take as base_hash."""
    return data(await client.call_tool("read", {"path": path}))["hash"]


def head_sha(root):
    return git(root, "rev-parse", "HEAD").stdout.strip()


def commit_files(root, sha):
    """Paths touched by a commit."""
    out = git(root, "show", "--name-only", "--no-renames", "--format=", sha).stdout
    return sorted(line for line in out.splitlines() if line.strip())


def commit_message_of(root, sha):
    return git(root, "show", "-s", "--format=%B", sha).stdout.strip("\n")
