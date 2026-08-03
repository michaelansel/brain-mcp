"""The read-before-write check: overwriting write requires the hash read
returned, and any supplied hash must still match what's on disk.

The proof travels with the request, so none of this depends on MCP session
identity — see test_no_session_state_is_involved, which is the case that
motivated the design (a client opening a fresh session per tool call could
never satisfy the old per-session ledger, no matter how often it re-read)."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from brain_mcp.config import BrainConfig
from brain_mcp.server import build_server
from tests.conftest import data, read_hash

EDIT = {"path": "agents.md", "old_string": "Be tidy.", "new_string": "Be tidier."}
OVERWRITE = {"path": "agents.md", "content": "# Replaced\n"}
STALE = "0" * 64


async def test_new_file_write_needs_no_hash(brain, client):
    data(await client.call_tool("write", {"path": "inbox/fresh.md", "content": "x\n"}))
    assert (brain / "inbox" / "fresh.md").exists()


async def test_overwrite_without_hash_is_refused(brain, client):
    before = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await client.call_tool("write", OVERWRITE)
    assert (brain / "agents.md").read_text() == before


async def test_read_then_write_succeeds(brain, client):
    h = await read_hash(client, "agents.md")
    data(await client.call_tool("write", OVERWRITE | {"base_hash": h}))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_read_returns_content_and_hash(brain, client):
    result = data(await client.call_tool("read", {"path": "agents.md"}))
    assert result["content"] == (brain / "agents.md").read_text()
    assert len(result["hash"]) == 64


async def test_write_with_a_stale_hash_is_refused(brain, client):
    before = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("write", OVERWRITE | {"base_hash": STALE})
    assert (brain / "agents.md").read_text() == before


async def test_hand_edit_between_read_and_write_is_caught(brain, client):
    h = await read_hash(client, "agents.md")
    (brain / "agents.md").write_text("# hand edit\n")
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("write", OVERWRITE | {"base_hash": h})
    assert (brain / "agents.md").read_text() == "# hand edit\n"  # survives


async def test_rereading_after_a_hand_edit_lets_the_write_through(brain, client):
    h = await read_hash(client, "agents.md")
    (brain / "agents.md").write_text("# hand edit\n")
    with pytest.raises(ToolError, match="changed on disk"):
        await client.call_tool("write", OVERWRITE | {"base_hash": h})
    h = await read_hash(client, "agents.md")  # reconcile
    data(await client.call_tool("write", OVERWRITE | {"base_hash": h}))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_a_write_invalidates_its_own_hash(brain, client):
    """The file is what the caller wrote, not what it read, so replaying the
    same base_hash must fail rather than silently overwrite again."""
    h = await read_hash(client, "agents.md")
    data(await client.call_tool("write", OVERWRITE | {"base_hash": h}))
    with pytest.raises(ToolError, match="changed on disk"):
        await client.call_tool(
            "write", {"path": "agents.md", "content": "# Again\n", "base_hash": h}
        )


async def test_byte_identical_overwrite_is_still_refused_without_a_hash(brain, client):
    same = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await client.call_tool("write", {"path": "agents.md", "content": same})


# --- edit: old_string is itself proof, so base_hash is optional ---


async def test_edit_without_a_hash_succeeds(brain, client):
    """A byte-exact old_string can only come from having seen the file."""
    data(await client.call_tool("edit", EDIT))
    assert "Be tidier." in (brain / "agents.md").read_text()


async def test_edit_with_a_matching_hash_succeeds(brain, client):
    h = await read_hash(client, "agents.md")
    data(await client.call_tool("edit", EDIT | {"base_hash": h}))
    assert "Be tidier." in (brain / "agents.md").read_text()


async def test_edit_with_a_stale_hash_is_refused(brain, client):
    before = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("edit", EDIT | {"base_hash": STALE})
    assert (brain / "agents.md").read_text() == before


async def test_edit_hash_catches_a_change_elsewhere_in_the_file(brain, client):
    """old_string still matches, but the file moved under the caller — the
    hash is what turns that into a refusal instead of a silent stale edit."""
    h = await read_hash(client, "agents.md")
    (brain / "agents.md").write_text("# Conventions\n\nBe tidy.\n\nHand-added line.\n")
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("edit", EDIT | {"base_hash": h})
    assert "Hand-added line." in (brain / "agents.md").read_text()


async def test_edit_still_refuses_a_nonmatching_old_string(brain, client):
    with pytest.raises(ToolError, match="old_string not found"):
        await client.call_tool(
            "edit", {"path": "agents.md", "old_string": "never here", "new_string": "x"}
        )


# --- the point of the design ---


async def test_no_session_state_is_involved(brain, client, other_client):
    """Read in one MCP session, write in another. Under the old per-session
    ledger this was refused; it is exactly what the Claude.ai connector does,
    since it opens a fresh session per tool call."""
    h = await read_hash(client, "agents.md")
    data(await other_client.call_tool("write", OVERWRITE | {"base_hash": h}))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_move_does_not_strand_a_hash(brain, client):
    """Content is unchanged by a move, so the hash still authorizes a write at
    the new path — nothing to invalidate, because nothing is stored."""
    h = await read_hash(client, "inbox/idea.md")
    data(await client.call_tool("move", {"src": "inbox/idea.md", "dst": "areas/idea.md"}))
    data(
        await client.call_tool("write", {"path": "areas/idea.md", "content": "x\n", "base_hash": h})
    )
    assert (brain / "areas" / "idea.md").read_text() == "x\n"


async def test_move_delete_project_create_need_no_hash(brain, client):
    data(await client.call_tool("move", {"src": "inbox/idea.md", "dst": "areas/idea.md"}))
    data(await client.call_tool("delete", {"path": "areas/idea.md"}))
    data(await client.call_tool("project_create", {"name": "rocket", "summary": "Build it."}))


async def test_check_can_be_disabled(brain):
    config = BrainConfig(root=brain.resolve(), read_before_write=False)
    async with Client(build_server(config)) as c:
        data(await c.call_tool("write", OVERWRITE))
        assert (brain / "agents.md").read_text() == "# Replaced\n"
        data(
            await c.call_tool(
                "write", {"path": "agents.md", "content": "# Again\n", "base_hash": STALE}
            )
        )
        assert (brain / "agents.md").read_text() == "# Again\n"
