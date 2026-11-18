"""list, read, search (rg and fallback), glob — through the real MCP round trip."""

import pytest
from fastmcp.exceptions import ToolError

import brain_mcp.searchio as searchio
from tests.conftest import commit_all, data


async def test_list_root(client):
    entries = data(await client.call_tool("list", {}))
    names = [e["name"] for e in entries]
    assert names == ["agents.md", "areas", "inbox"]
    by_name = {e["name"]: e for e in entries}
    assert by_name["inbox"]["type"] == "dir"
    assert by_name["agents.md"]["type"] == "file"
    assert by_name["agents.md"]["size"] > 0
    assert by_name["agents.md"]["path"] == "agents.md"
    assert "T" in by_name["agents.md"]["modified"]  # ISO 8601


async def test_list_subdir(client):
    entries = data(await client.call_tool("list", {"path": "inbox"}))
    assert [e["path"] for e in entries] == ["inbox/idea.md"]


async def test_list_never_shows_git(client):
    entries = data(await client.call_tool("list", {}))
    assert ".git" not in [e["name"] for e in entries]


async def test_list_missing_dir(client):
    with pytest.raises(ToolError, match="no such directory"):
        await client.call_tool("list", {"path": "nope"})


async def test_list_on_file_errors(client):
    with pytest.raises(ToolError, match="use read"):
        await client.call_tool("list", {"path": "agents.md"})


async def test_read(client):
    result = await client.call_tool("read", {"path": "inbox/idea.md"})
    assert "Build a rocket" in result.data.content
    assert len(result.data.hash) == 64


async def test_read_missing(client):
    with pytest.raises(ToolError, match="no such file"):
        await client.call_tool("read", {"path": "missing.md"})


async def test_read_traversal_rejected(client):
    with pytest.raises(ToolError):
        await client.call_tool("read", {"path": "../outside.md"})


async def test_read_empty_file(brain, client):
    (brain / "empty.md").write_text("")
    result = await client.call_tool("read", {"path": "empty.md"})
    assert result.data.content == ""


async def test_read_unicode_and_spaces(brain, client):
    (brain / "notes véritables.md").write_text("café ☕\n")
    result = await client.call_tool("read", {"path": "notes véritables.md"})
    assert "café" in result.data.content


async def test_read_large_file(brain, client):
    big = "line of filler text\n" * 300_000  # ~5.7 MB
    (brain / "big.md").write_text(big)
    result = await client.call_tool("read", {"path": "big.md"})
    assert len(result.data.content) == len(big)


async def test_search_literal(client):
    hits = data(await client.call_tool("search", {"query": "rocket"}))
    assert hits == [{"path": "inbox/idea.md", "line": 3, "text": "Build a rocket."}]


async def test_search_literal_not_regex(client):
    # a regex metacharacter must match literally by default
    hits = data(await client.call_tool("search", {"query": "rocket."}))
    assert len(hits) == 1
    hits = data(await client.call_tool("search", {"query": "rock.t"}))
    assert hits == []


async def test_search_regex_opt_in(client):
    hits = data(await client.call_tool("search", {"query": "rock.t", "regex": True}))
    assert len(hits) == 1


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("regex", [False, True])
async def test_search_ignores_case_by_default(client, monkeypatch, fallback, regex):
    if fallback:
        monkeypatch.setattr(searchio.shutil, "which", lambda _: None)
    hits = data(await client.call_tool("search", {"query": "BUILD A ROCKET", "regex": regex}))
    assert [h["path"] for h in hits] == ["inbox/idea.md"]
    hits = data(
        await client.call_tool(
            "search", {"query": "BUILD A ROCKET", "regex": regex, "case_sensitive": True}
        )
    )
    assert hits == []


async def test_search_scoped_to_path(client):
    hits = data(await client.call_tool("search", {"query": "#", "path": "areas"}))
    assert all(h["path"].startswith("areas/") for h in hits)


async def test_search_limit(brain, client):
    (brain / "many.md").write_text("match\n" * 100)
    hits = data(await client.call_tool("search", {"query": "match", "limit": 7}))
    assert len(hits) == 7


async def test_search_never_searches_git(client):
    # every git repo's .git/HEAD contains "ref:"
    hits = data(await client.call_tool("search", {"query": "ref: refs/"}))
    assert hits == []


async def test_search_fallback_without_rg(brain, client, monkeypatch):
    monkeypatch.setattr(searchio.shutil, "which", lambda _: None)
    hits = data(await client.call_tool("search", {"query": "rocket"}))
    assert hits == [{"path": "inbox/idea.md", "line": 3, "text": "Build a rocket."}]


async def test_search_fallback_matches_rg(brain, client, monkeypatch):
    (brain / "multi.md").write_text("alpha\nbeta rocket\ngamma rocket\n")
    with_rg = data(await client.call_tool("search", {"query": "rocket"}))
    monkeypatch.setattr(searchio.shutil, "which", lambda _: None)
    without_rg = data(await client.call_tool("search", {"query": "rocket"}))
    assert with_rg == without_rg


async def test_search_fallback_bad_regex(client, monkeypatch):
    monkeypatch.setattr(searchio.shutil, "which", lambda _: None)
    with pytest.raises(ToolError, match="invalid regex"):
        await client.call_tool("search", {"query": "[unclosed", "regex": True})


async def test_search_bad_regex_rg(client):
    with pytest.raises(ToolError, match="search failed"):
        await client.call_tool("search", {"query": "[unclosed", "regex": True})


async def test_glob(client):
    paths = data(await client.call_tool("glob", {"pattern": "**/*.md"}))
    assert "inbox/idea.md" in paths
    assert "agents.md" in paths
    assert not any(".git" in p for p in paths)


async def test_glob_dirs_included(client):
    paths = data(await client.call_tool("glob", {"pattern": "*"}))
    assert "inbox" in paths


async def test_glob_rejects_escape(client):
    for pattern in ["/etc/*", "../*", "a/../../*", "~/x"]:
        with pytest.raises(ToolError):
            await client.call_tool("glob", {"pattern": pattern})


async def test_reads_see_external_edits_without_restart(brain, client):
    # the user edits the brain by hand; the server never caches
    (brain / "agents.md").write_text("# Conventions\n\nEdited outside.\n")
    commit_all(brain, "external edit")
    result = await client.call_tool("read", {"path": "agents.md"})
    assert "Edited outside" in result.data.content


async def test_search_default_shape_unchanged(client):
    hits = data(await client.call_tool("search", {"query": "rocket"}))
    assert hits == [{"path": "inbox/idea.md", "line": 3, "text": "Build a rocket."}]


async def test_search_expand_groups_and_inlines_small_notes(brain, client):
    (brain / "inbox" / "two.md").write_text(
        "---\ntitle: Two rockets\n---\nrocket one\nrocket two\n"
    )
    hits = data(await client.call_tool("search", {"query": "rocket", "expand": True}))
    by_path = {h["path"]: h for h in hits}
    assert list(by_path) == ["inbox/idea.md", "inbox/two.md"]
    two = by_path["inbox/two.md"]
    assert two["title"] == "Two rockets"
    assert [h["line"] for h in two["hits"]] == [2, 4, 5]  # title matches too: case-insensitive
    read = data(await client.call_tool("read", {"path": "inbox/two.md"}))
    assert two["content"] == read["content"]
    assert two["hash"] == read["hash"]
    assert by_path["inbox/idea.md"]["title"] == "Big idea"


async def test_search_expand_hash_works_as_base_hash(brain, client):
    hit = data(await client.call_tool("search", {"query": "rocket", "expand": True}))[0]
    await client.call_tool(
        "write",
        {"path": hit["path"], "content": "# Big idea\n\nBuild two.\n", "base_hash": hit["hash"]},
    )


async def test_search_expand_skips_large_notes_and_summaries(brain, client):
    (brain / "areas" / "big.md").write_text("rocket\n" + "x" * 5000 + "\n")
    (brain / "areas" / "summary.md").write_text("# Areas\n\nrocket summary\n")
    hits = data(
        await client.call_tool("search", {"query": "rocket", "path": "areas", "expand": True})
    )
    for h in hits:
        assert h["content"] is None
        assert "hash" not in h
        assert h["hits"]


async def test_search_expand_budget_never_truncates(brain, client, monkeypatch):
    monkeypatch.setattr(searchio, "EXPAND_BUDGET_BYTES", 5000)
    for i in range(5):
        (brain / "inbox" / f"n{i}.md").write_text("rocket\n" + "y" * 2000 + "\n")
    hits = data(await client.call_tool("search", {"query": "rocket", "expand": True}))
    inlined = [h for h in hits if h["content"] is not None]
    assert 1 <= len(inlined) < len(hits)
    for h in inlined:
        assert h["content"] == (brain / h["path"]).read_text()


async def test_search_expand_puts_archive_last(brain, client):
    (brain / "archive").mkdir()
    for i in range(5):
        (brain / "archive" / f"old{i}.md").write_text("rocket\n")
    hits = data(await client.call_tool("search", {"query": "rocket", "limit": 2, "expand": True}))
    assert hits[0]["path"] == "inbox/idea.md"
    plain = data(await client.call_tool("search", {"query": "rocket", "limit": 2}))
    assert all(h["path"].startswith("archive/") for h in plain)
