"""write, edit, move, delete: commit-per-call, commit hygiene, concurrency."""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import (
    commit_all,
    commit_files,
    commit_message_of,
    data,
    git,
    head_sha,
    read_hash,
)


async def test_write_new_file(brain, client):
    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert (brain / "inbox" / "note.md").read_text() == "hi\n"
    assert result["path"] == "inbox/note.md"
    assert result["commit"] == head_sha(brain)
    assert commit_files(brain, result["commit"]) == ["inbox/note.md"]
    assert commit_message_of(brain, result["commit"]) == "write: inbox/note.md\n\nvia brain-mcp"


async def test_write_overwrite(brain, client):
    h = await read_hash(client, "agents.md")  # overwriting requires the hash
    data(
        await client.call_tool("write", {"path": "agents.md", "content": "# New\n", "base_hash": h})
    )
    assert (brain / "agents.md").read_text() == "# New\n"


async def test_write_creates_parent_dirs(brain, client):
    data(await client.call_tool("write", {"path": "a/b/c/deep.md", "content": "x\n"}))
    assert (brain / "a" / "b" / "c" / "deep.md").exists()


async def test_write_noop_returns_head(brain, client):
    before = head_sha(brain)
    h = await read_hash(client, "agents.md")
    result = data(
        await client.call_tool(
            "write",
            {"path": "agents.md", "content": (brain / "agents.md").read_text(), "base_hash": h},
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


async def seed(brain, client, name, text):
    """Write a file by hand and commit it. `edit` needs no prior read — a
    byte-exact old_string is its own proof — so nothing else is required."""
    (brain / name).write_text(text)
    commit_all(brain, f"seed {name}")
    return brain / name


async def test_edit_replace_all(brain, client):
    await seed(brain, client, "doc.md", "# Doc\n\nalpha here\nalpha again\n")
    args = {"path": "doc.md", "old_string": "alpha", "new_string": "beta", "replace_all": True}
    result = data(await client.call_tool("edit", args))
    assert (brain / "doc.md").read_text() == "# Doc\n\nbeta here\nbeta again\n"
    assert result["replacements"] == 2
    assert commit_files(brain, result["commit"]) == ["doc.md"]
    assert commit_message_of(brain, result["commit"]) == "edit: doc.md\n\nvia brain-mcp"


async def test_edit_unique_match(brain, client):
    await seed(brain, client, "doc.md", "# Doc\n\none\ntwo\nthree\n")
    args = {"path": "doc.md", "old_string": "two", "new_string": "TWO"}
    result = data(await client.call_tool("edit", args))
    assert (brain / "doc.md").read_text() == "# Doc\n\none\nTWO\nthree\n"
    assert result["replacements"] == 1


async def test_edit_ambiguous_match_errors(brain, client):
    await seed(brain, client, "doc.md", "x\nx\nx\n")
    with pytest.raises(ToolError, match="appears 3 times"):
        await client.call_tool("edit", {"path": "doc.md", "old_string": "x", "new_string": "y"})
    assert (brain / "doc.md").read_text() == "x\nx\nx\n"  # untouched


async def test_edit_not_found_errors(brain, client):
    await seed(brain, client, "doc.md", "hello\n")
    with pytest.raises(ToolError, match="old_string not found"):
        await client.call_tool("edit", {"path": "doc.md", "old_string": "nope", "new_string": "y"})


async def test_edit_empty_old_string_errors(brain, client):
    await seed(brain, client, "doc.md", "hello\n")
    with pytest.raises(ToolError, match="old_string is empty"):
        await client.call_tool("edit", {"path": "doc.md", "old_string": "", "new_string": "y"})


async def test_edit_identical_strings_error(brain, client):
    await seed(brain, client, "doc.md", "hello\n")
    with pytest.raises(ToolError, match="identical"):
        await client.call_tool(
            "edit", {"path": "doc.md", "old_string": "hello", "new_string": "hello"}
        )


async def test_edit_missing_file(client):
    with pytest.raises(ToolError, match="no such file"):
        await client.call_tool("edit", {"path": "ghost.md", "old_string": "a", "new_string": "b"})


async def test_edit_directory_errors(client):
    with pytest.raises(ToolError, match="is a directory"):
        await client.call_tool("edit", {"path": "inbox", "old_string": "a", "new_string": "b"})


async def test_edit_multiline_block_with_indentation(brain, client):
    await seed(brain, client, "doc.md", "start\n\n    - one\n    - two\n\nend\n")
    args = {
        "path": "doc.md",
        "old_string": "    - one\n    - two\n",
        "new_string": "    - one\n    - two\n    - three\n",
    }
    data(await client.call_tool("edit", args))
    assert (brain / "doc.md").read_text() == "start\n\n    - one\n    - two\n    - three\n\nend\n"


async def test_edit_unicode(brain, client):
    await seed(brain, client, "doc.md", "# メモ\n\n縦書き\n")
    args = {"path": "doc.md", "old_string": "縦書き", "new_string": "横書き"}
    data(await client.call_tool("edit", args))
    assert (brain / "doc.md").read_text() == "# メモ\n\n横書き\n"


async def test_edit_deletes_text_with_empty_new_string(brain, client):
    await seed(brain, client, "doc.md", "keep\ndrop me\nkeep\n")
    args = {"path": "doc.md", "old_string": "drop me\n", "new_string": ""}
    result = data(await client.call_tool("edit", args))
    assert (brain / "doc.md").read_text() == "keep\nkeep\n"
    assert result["replacements"] == 1


async def test_edit_non_utf8_file(brain, client):
    (brain / "bin.md").write_bytes(b"\xff\xfe\x00binary")
    commit_all(brain, "binary")
    with pytest.raises(ToolError, match="not a UTF-8 text file"):
        await client.call_tool("edit", {"path": "bin.md", "old_string": "a", "new_string": "b"})


async def test_edit_traversal_rejected(client):
    with pytest.raises(ToolError):
        await client.call_tool("edit", {"path": "../evil.md", "old_string": "a", "new_string": "b"})


async def test_edit_leaves_unrelated_dirty_files_alone(brain, client):
    await seed(brain, client, "doc.md", "target\n")
    (brain / "dirty.md").write_text("uncommitted by hand\n")
    (brain / "staged.md").write_text("staged by hand\n")
    git(brain, "add", "--", "staged.md")

    result = data(
        await client.call_tool(
            "edit", {"path": "doc.md", "old_string": "target", "new_string": "hit"}
        )
    )

    assert commit_files(brain, result["commit"]) == ["doc.md"]
    status = git(brain, "status", "--porcelain").stdout
    assert "?? dirty.md" in status
    assert "A  staged.md" in status


async def test_concurrent_edits_do_not_lose_updates(brain, client):
    """The read-verify-write sequence is inside write_lock, so N concurrent
    edits to one file all land. The old section-based `patch` (which read
    outside the lock) failed this."""
    n = 8
    await seed(brain, client, "doc.md", "".join(f"marker-{i}\n" for i in range(n)))
    results = await asyncio.gather(
        *(
            client.call_tool(
                "edit", {"path": "doc.md", "old_string": f"marker-{i}", "new_string": f"done-{i}"}
            )
            for i in range(n)
        )
    )
    shas = [data(r)["commit"] for r in results]
    assert len(set(shas)) == n  # every edit got its own commit
    final = (brain / "doc.md").read_text()
    assert final == "".join(f"done-{i}\n" for i in range(n))  # nothing lost


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


# --- directories emptied by a move or delete ---


async def test_move_removes_the_directory_it_empties(client, brain):
    """git mv leaves the source directory behind. Nothing commits it, so it
    lingers in the working tree as a folder holding nothing."""
    (brain / "projects" / "rocket").mkdir(parents=True)
    (brain / "projects" / "rocket" / "project.md").write_text("# rocket\n")
    commit_all(brain, "seed project")
    await client.call_tool(
        "move", {"src": "projects/rocket/project.md", "dst": "archive/rocket/project.md"}
    )
    assert not (brain / "projects" / "rocket").exists()
    assert (brain / "archive" / "rocket" / "project.md").is_file()
    assert (brain / "projects").is_dir()


async def test_move_keeps_a_directory_that_still_has_notes(client, brain):
    (brain / "projects" / "rocket").mkdir(parents=True)
    (brain / "projects" / "rocket" / "project.md").write_text("# rocket\n")
    (brain / "projects" / "rocket" / "fuel.md").write_text("# fuel\n")
    commit_all(brain, "seed project")
    await client.call_tool("move", {"src": "projects/rocket/fuel.md", "dst": "inbox/fuel.md"})
    assert (brain / "projects" / "rocket" / "project.md").is_file()


async def test_delete_removes_the_directory_it_empties(client, brain):
    (brain / "projects" / "rocket").mkdir(parents=True)
    (brain / "projects" / "rocket" / "project.md").write_text("# rocket\n")
    commit_all(brain, "seed project")
    await client.call_tool("delete", {"path": "projects/rocket/project.md"})
    assert not (brain / "projects" / "rocket").exists()


async def test_delete_removes_a_directory_git_never_tracked(client, brain):
    """The escape hatch: git cannot track an empty directory, so `git rm` has
    nothing to match. Before this, a stray empty folder was unreachable
    through the API and could only be fixed with shell access."""
    (brain / "projects" / "ghost").mkdir(parents=True)
    result = data(await client.call_tool("delete", {"path": "projects/ghost"}))
    assert not (brain / "projects" / "ghost").exists()
    assert "removed empty directory" in result["message"]
    assert result["commit"] == head_sha(brain)  # nothing was committed


async def test_deleting_an_empty_dir_prunes_its_empty_parents(client, brain):
    (brain / "projects" / "a" / "b").mkdir(parents=True)
    data(await client.call_tool("delete", {"path": "projects/a/b"}))
    assert not (brain / "projects" / "a").exists()
    assert (brain / "projects").is_dir()  # the skeleton is never reaped
