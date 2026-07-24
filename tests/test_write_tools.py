"""write, patch, move, delete: commit-per-call, commit hygiene, concurrency."""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import commit_files, commit_message_of, data, git, head_sha


async def test_write_new_file(brain, client):
    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert (brain / "inbox" / "note.md").read_text() == "hi\n"
    assert result["path"] == "inbox/note.md"
    assert result["commit"] == head_sha(brain)
    assert commit_files(brain, result["commit"]) == ["inbox/note.md"]
    assert commit_message_of(brain, result["commit"]) == "write: inbox/note.md\n\nvia brain-mcp"


async def test_write_overwrite(brain, client):
    data(await client.call_tool("write", {"path": "agents.md", "content": "# New\n"}))
    assert (brain / "agents.md").read_text() == "# New\n"


async def test_write_creates_parent_dirs(brain, client):
    data(await client.call_tool("write", {"path": "a/b/c/deep.md", "content": "x\n"}))
    assert (brain / "a" / "b" / "c" / "deep.md").exists()


async def test_write_noop_returns_head(brain, client):
    before = head_sha(brain)
    result = data(
        await client.call_tool(
            "write", {"path": "agents.md", "content": (brain / "agents.md").read_text()}
        )
    )
    assert result["commit"] == before
    assert "no change" in result["message"]
    assert head_sha(brain) == before


async def test_write_unicode_filename(brain, client):
    args = {"path": "inbox/日本語 メモ.md", "content": "縦書き\n"}
    result = data(await client.call_tool("write", args))
    assert (brain / "inbox" / "日本語 メモ.md").read_text() == "縦書き\n"
    assert commit_files(brain, result["commit"]) == ["inbox/日本語 メモ.md"]


async def test_write_empty_content(brain, client):
    result = data(await client.call_tool("write", {"path": "empty.md", "content": ""}))
    assert (brain / "empty.md").read_text() == ""
    assert commit_files(brain, result["commit"]) == ["empty.md"]


async def test_write_large_file(brain, client):
    big = "filler line for a file large enough to matter\n" * 120_000  # ~5.4 MB
    result = data(await client.call_tool("write", {"path": "big.md", "content": big}))
    assert (brain / "big.md").stat().st_size == len(big.encode())
    assert commit_files(brain, result["commit"]) == ["big.md"]


async def test_write_traversal_rejected(brain, client, tmp_path):
    for path in ["../evil.md", "/etc/evil", "..", "inbox/../../evil.md"]:
        with pytest.raises(ToolError):
            await client.call_tool("write", {"path": path, "content": "x"})
    assert not (tmp_path / "evil.md").exists()


async def test_write_under_a_file_gives_actionable_error(brain, client):
    with pytest.raises(ToolError, match="cannot write 'agents.md/nested.md'"):
        await client.call_tool("write", {"path": "agents.md/nested.md", "content": "x"})


async def test_unrelated_dirty_files_left_alone(brain, client):
    (brain / "dirty.md").write_text("uncommitted by hand\n")
    (brain / "agents.md").write_text("# hand edit, unstaged\n")
    (brain / "staged.md").write_text("staged by hand\n")
    git(brain, "add", "--", "staged.md")

    result = data(await client.call_tool("write", {"path": "inbox/clean.md", "content": "x\n"}))

    assert commit_files(brain, result["commit"]) == ["inbox/clean.md"]
    status = git(brain, "status", "--porcelain").stdout
    assert "?? dirty.md" in status
    assert " M agents.md" in status
    assert "A  staged.md" in status  # still staged, still uncommitted


async def test_concurrent_writes_distinct_commits(brain, client):
    n = 8
    results = await asyncio.gather(
        *(
            client.call_tool("write", {"path": f"inbox/con-{i}.md", "content": f"body {i}\n"})
            for i in range(n)
        )
    )
    shas = [data(r)["commit"] for r in results]
    assert len(set(shas)) == n  # every write got its own commit
    for r in results:
        d = data(r)
        assert commit_files(brain, d["commit"]) == [d["path"]]  # no interleaving
    log = git(brain, "log", "--oneline").stdout.splitlines()
    assert len(log) == n + 1  # seed + n writes


async def test_patch_replaces_section(brain, client):
    (brain / "doc.md").write_text(
        "# Doc\n\n## Context\n\nold context\n\n## Next steps\n\n- old step\n\n## End\n\nfin\n"
    )
    result = data(
        await client.call_tool(
            "patch", {"path": "doc.md", "section": "Next steps", "content": "\n- new step\n"}
        )
    )
    text = (brain / "doc.md").read_text()
    assert "- new step" in text
    assert "- old step" not in text
    assert "old context" in text  # earlier section untouched
    assert "fin" in text  # later section untouched
    assert commit_files(brain, result["commit"]) == ["doc.md"]
    assert commit_message_of(brain, result["commit"]).startswith("patch: doc.md\n")


async def test_patch_last_section(brain, client):
    (brain / "doc.md").write_text("# Doc\n\n## Only\n\nold tail\n")
    data(
        await client.call_tool(
            "patch", {"path": "doc.md", "section": "Only", "content": "\nnew tail\n"}
        )
    )
    text = (brain / "doc.md").read_text()
    assert text.endswith("new tail\n")
    assert "old tail" not in text


async def test_patch_subsections_replaced_with_parent(brain, client):
    (brain / "doc.md").write_text(
        "# Doc\n\n## Parent\n\nbody\n\n### Child\n\nchild body\n\n## Sibling\n\nkeep\n"
    )
    data(
        await client.call_tool(
            "patch", {"path": "doc.md", "section": "Parent", "content": "\nreplaced\n"}
        )
    )
    text = (brain / "doc.md").read_text()
    assert "Child" not in text  # lower-level heading belongs to the section
    assert "## Sibling" in text and "keep" in text


async def test_patch_duplicate_headings_first_wins(brain, client):
    (brain / "doc.md").write_text("## Log\n\nfirst\n\n## Log\n\nsecond\n")
    data(
        await client.call_tool("patch", {"path": "doc.md", "section": "Log", "content": "\nnew\n"})
    )
    text = (brain / "doc.md").read_text()
    assert "first" not in text
    assert "second" in text


async def test_patch_missing_section_lists_present(brain, client):
    with pytest.raises(ToolError, match="sections present: Health, Sleep"):
        await client.call_tool(
            "patch", {"path": "areas/health.md", "section": "Decisions", "content": "x"}
        )


async def test_patch_ignores_headings_in_code_fences(brain, client):
    (brain / "doc.md").write_text(
        "# Doc\n\n## Real\n\nbody\n\n```\n## Fake heading\n```\n\nmore body\n"
    )
    data(
        await client.call_tool("patch", {"path": "doc.md", "section": "Real", "content": "\nnew\n"})
    )
    text = (brain / "doc.md").read_text()
    assert "Fake heading" not in text  # fence was inside the section body
    assert "new" in text
    with pytest.raises(ToolError, match="no such section"):
        await client.call_tool(
            "patch", {"path": "doc.md", "section": "Fake heading", "content": "x"}
        )


async def test_patch_missing_file(client):
    with pytest.raises(ToolError, match="no such file"):
        await client.call_tool("patch", {"path": "nope.md", "section": "X", "content": "y"})


async def test_move(brain, client):
    result = data(await client.call_tool("move", {"src": "inbox/idea.md", "dst": "areas/idea.md"}))
    assert not (brain / "inbox" / "idea.md").exists()
    assert (brain / "areas" / "idea.md").exists()
    assert result["path"] == "areas/idea.md"
    assert sorted(commit_files(brain, result["commit"])) == ["areas/idea.md", "inbox/idea.md"]
    msg = commit_message_of(brain, result["commit"])
    assert msg == "move: inbox/idea.md -> areas/idea.md\n\nvia brain-mcp"


async def test_move_creates_parent_dirs(brain, client):
    args = {"src": "inbox/idea.md", "dst": "projects/x/notes/idea.md"}
    data(await client.call_tool("move", args))
    assert (brain / "projects" / "x" / "notes" / "idea.md").exists()


async def test_move_missing_src(client):
    with pytest.raises(ToolError, match="no such path"):
        await client.call_tool("move", {"src": "ghost.md", "dst": "x.md"})


async def test_move_traversal_rejected(client):
    with pytest.raises(ToolError):
        await client.call_tool("move", {"src": "inbox/idea.md", "dst": "../escaped.md"})


async def test_delete_file(brain, client):
    result = data(await client.call_tool("delete", {"path": "inbox/idea.md"}))
    assert not (brain / "inbox" / "idea.md").exists()
    assert commit_files(brain, result["commit"]) == ["inbox/idea.md"]
    msg = commit_message_of(brain, result["commit"])
    assert msg == "delete: inbox/idea.md\n\nvia brain-mcp"


async def test_delete_directory(brain, client):
    result = data(await client.call_tool("delete", {"path": "inbox"}))
    assert not (brain / "inbox").exists()
    assert commit_files(brain, result["commit"]) == ["inbox/idea.md"]


async def test_delete_untracked_file_errors(brain, client):
    (brain / "untracked.md").write_text("never committed\n")
    with pytest.raises(ToolError):
        await client.call_tool("delete", {"path": "untracked.md"})
    assert (brain / "untracked.md").exists()  # left alone


async def test_delete_missing(client):
    with pytest.raises(ToolError, match="no such path"):
        await client.call_tool("delete", {"path": "ghost.md"})
