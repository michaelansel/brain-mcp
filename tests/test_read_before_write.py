"""The read-before-write gate: edit and overwriting write both require that
this session read the file, and that it hasn't changed on disk since."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from brain_mcp.config import BrainConfig
from brain_mcp.server import build_server
from tests.conftest import data

EDIT = {"path": "agents.md", "old_string": "Be tidy.", "new_string": "Be tidier."}
OVERWRITE = {"path": "agents.md", "content": "# Replaced\n"}


async def test_new_file_write_needs_no_read(brain, client):
    data(await client.call_tool("write", {"path": "inbox/fresh.md", "content": "x\n"}))
    assert (brain / "inbox" / "fresh.md").exists()


async def test_overwrite_without_read_is_refused(brain, client):
    before = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await client.call_tool("write", OVERWRITE)
    assert (brain / "agents.md").read_text() == before


async def test_edit_without_read_is_refused(brain, client):
    before = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await client.call_tool("edit", EDIT)
    assert (brain / "agents.md").read_text() == before


async def test_read_then_write_succeeds(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    data(await client.call_tool("write", OVERWRITE))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_read_then_edit_succeeds(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    data(await client.call_tool("edit", EDIT))
    assert "Be tidier." in (brain / "agents.md").read_text()


async def test_hand_edit_between_read_and_edit_is_caught(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    (brain / "agents.md").write_text("# Conventions\n\nBe tidy.\n\nHand-added line.\n")
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("edit", EDIT)
    assert "Hand-added line." in (brain / "agents.md").read_text()  # survives


async def test_hand_edit_between_read_and_write_is_caught(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    (brain / "agents.md").write_text("# hand edit\n")
    with pytest.raises(ToolError, match="changed on disk since you read it"):
        await client.call_tool("write", OVERWRITE)
    assert (brain / "agents.md").read_text() == "# hand edit\n"


async def test_rereading_after_a_hand_edit_lets_the_write_through(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    (brain / "agents.md").write_text("# hand edit\n")
    with pytest.raises(ToolError, match="changed on disk"):
        await client.call_tool("write", OVERWRITE)
    await client.call_tool("read", {"path": "agents.md"})  # reconcile
    data(await client.call_tool("write", OVERWRITE))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_retry_after_staleness_repeats_the_specific_error(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    (brain / "agents.md").write_text("# hand edit\n")
    for _ in range(2):
        with pytest.raises(ToolError, match="changed on disk"):
            await client.call_tool("write", OVERWRITE)


async def test_one_read_covers_a_chain_of_edits_and_a_write(brain, client):
    await client.call_tool("read", {"path": "agents.md"})
    data(await client.call_tool("edit", EDIT))
    data(
        await client.call_tool(
            "edit", {"path": "agents.md", "old_string": "Be tidier.", "new_string": "Be tidiest."}
        )
    )
    data(await client.call_tool("write", OVERWRITE))
    assert (brain / "agents.md").read_text() == "# Replaced\n"


async def test_read_state_is_per_session(brain, client, other_client):
    await client.call_tool("read", {"path": "agents.md"})
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await other_client.call_tool("write", OVERWRITE)
    data(await client.call_tool("write", OVERWRITE))


async def test_byte_identical_overwrite_is_still_refused(brain, client):
    same = (brain / "agents.md").read_text()
    with pytest.raises(ToolError, match="read 'agents.md' first"):
        await client.call_tool("write", {"path": "agents.md", "content": same})


async def test_move_invalidates_both_paths(brain, client):
    await client.call_tool("read", {"path": "inbox/idea.md"})
    data(await client.call_tool("move", {"src": "inbox/idea.md", "dst": "areas/idea.md"}))
    with pytest.raises(ToolError, match="read 'areas/idea.md' first"):
        await client.call_tool("write", {"path": "areas/idea.md", "content": "x\n"})


async def test_delete_of_a_directory_invalidates_files_under_it(brain, client):
    await client.call_tool("read", {"path": "inbox/idea.md"})
    data(await client.call_tool("delete", {"path": "inbox"}))
    # recreated by hand, so nothing re-records it: the pre-delete read must not
    # still authorize a write to that path
    (brain / "inbox").mkdir()
    (brain / "inbox" / "idea.md").write_text("# Big idea\n\nBuild a rocket.\n")
    with pytest.raises(ToolError, match="read 'inbox/idea.md' first"):
        await client.call_tool("write", {"path": "inbox/idea.md", "content": "x\n"})


async def test_project_create_invalidates_the_moved_note(brain, client):
    await client.call_tool("read", {"path": "inbox/idea.md"})
    data(
        await client.call_tool(
            "project_create",
            {"name": "rocket", "summary": "Build it.", "from_path": "inbox/idea.md"},
        )
    )
    with pytest.raises(ToolError, match="read 'projects/rocket/idea.md' first"):
        await client.call_tool("write", {"path": "projects/rocket/idea.md", "content": "x\n"})


async def test_move_delete_project_create_need_no_prior_read(brain, client):
    data(await client.call_tool("move", {"src": "inbox/idea.md", "dst": "areas/idea.md"}))
    data(await client.call_tool("delete", {"path": "areas/idea.md"}))
    data(await client.call_tool("project_create", {"name": "rocket", "summary": "Build it."}))


async def test_gate_can_be_disabled(brain):
    config = BrainConfig(root=brain.resolve(), read_before_write=False)
    async with Client(build_server(config)) as c:
        data(await c.call_tool("write", OVERWRITE))
        assert (brain / "agents.md").read_text() == "# Replaced\n"
        data(
            await c.call_tool(
                "edit", {"path": "agents.md", "old_string": "Replaced", "new_string": "Again"}
            )
        )
        assert (brain / "agents.md").read_text() == "# Again\n"
