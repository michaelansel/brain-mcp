"""Integration: the real MCP round trip in-memory, and the HTTP transport
with bearer auth as a live subprocess."""

import os
import socket
import subprocess
import sys
import time

import pytest
from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from tests.conftest import data, git


async def test_server_ships_instructions(client):
    # instructions reach every enrolled session at initialize time — the one
    # channel present on all surfaces, pointing agents at agents.md
    instructions = client.initialize_result.instructions
    assert instructions is not None
    assert "agents.md" in instructions


async def test_full_flow_in_memory(brain, client):
    """The main flow end-to-end over MCP: fork a project from an inbox note,
    write, edit, search, then inspect."""
    entries = data(await client.call_tool("list", {}))
    assert {e["name"] for e in entries} >= {"agents.md", "inbox"}

    created = data(
        await client.call_tool(
            "project_create",
            {"name": "rocket", "summary": "Build a rocket.", "from_path": "inbox/idea.md"},
        )
    )
    assert created["path"] == "projects/rocket"

    data(
        await client.call_tool(
            "write",
            {
                "path": "projects/rocket/notes.md",
                "content": "# Notes\n\n## Context\n\nliftoff\n\n## Next steps\n\n- design\n",
            },
        )
    )
    # no base_hash needed: a byte-exact old_string is its own proof
    data(
        await client.call_tool(
            "edit",
            {
                "path": "projects/rocket/notes.md",
                "old_string": "- design\n",
                "new_string": "- design\n- fuel\n",
            },
        )
    )

    hits = data(await client.call_tool("search", {"query": "fuel"}))
    assert [h["path"] for h in hits] == ["projects/rocket/notes.md"]

    detail = data(await client.call_tool("project", {"name": "rocket"}))
    assert detail["frontmatter"]["status"] == "active"
    assert "projects/rocket/idea.md" in detail["files"]

    summaries = data(await client.call_tool("projects", {}))
    assert summaries == [
        {"name": "rocket", "status": "active", "path": "projects/rocket", "repo_count": 0}
    ]

    # three mutations -> exactly three commits on top of the seed
    log = git(brain, "log", "--format=%s").stdout.splitlines()
    assert log == [
        "edit: projects/rocket/notes.md",
        "write: projects/rocket/notes.md",
        "project_create: projects/rocket",
        "seed",
    ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(brain):
    port = _free_port()
    env = os.environ | {
        "BRAIN_ROOT": str(brain),
        "BRAIN_MCP_TOKEN": "test-token",
        "BRAIN_MCP_PORT": str(port),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "brain_mcp.cli", "--transport", "http"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError("server never came up")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


async def test_http_transport_with_token(http_server, brain):
    async with Client(http_server, auth=BearerAuth("test-token")) as client:
        result = await client.call_tool("read", {"path": "inbox/idea.md"})
        assert "Build a rocket" in result.data.content
        base_hash = result.data.hash
        wrote = data(await client.call_tool("write", {"path": "inbox/http.md", "content": "hi\n"}))
        assert (brain / "inbox" / "http.md").read_text() == "hi\n"
        assert wrote["commit"] == git(brain, "rev-parse", "HEAD").stdout.strip()

    # A hash read in one MCP session must authorize a change in another, over
    # the real transport. The Claude.ai connector opens a fresh session per
    # tool call, which the old per-session ledger could never satisfy — it
    # refused every edit with "read it first" no matter how often it re-read.
    async with Client(http_server, auth=BearerAuth("test-token")) as client:
        edited = data(
            await client.call_tool(
                "edit",
                {
                    "path": "inbox/idea.md",
                    "old_string": "rocket",
                    "new_string": "spaceship",
                    "base_hash": base_hash,
                },
            )
        )
        assert edited["replacements"] == 1
        assert "spaceship" in (brain / "inbox" / "idea.md").read_text()


async def test_http_transport_rejects_bad_token(http_server):
    with pytest.raises(Exception, match="401|[Uu]nauthorized|[Ii]nvalid"):
        async with Client(http_server, auth=BearerAuth("wrong-token")) as client:
            await client.call_tool("read", {"path": "inbox/idea.md"})


async def test_http_transport_rejects_missing_token(http_server):
    with pytest.raises(Exception, match="401|[Uu]nauthorized|[Ii]nvalid|[Aa]uth"):
        async with Client(http_server) as client:
            await client.call_tool("read", {"path": "inbox/idea.md"})
