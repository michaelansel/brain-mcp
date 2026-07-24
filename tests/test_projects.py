"""projects, project, project_create — including the one-commit fork flow."""

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import commit_all, commit_files, commit_message_of, data, git


def seed_project(brain, name, status="active", repos=""):
    pdir = brain / "projects" / name
    pdir.mkdir(parents=True)
    (pdir / "project.md").write_text(
        f"---\nname: {name}\nstatus: {status}\ncreated: 2026-01-01\nrepos:{repos or ' []'}\n---\n"
        f"\n# {name}\n\nSome prose about {name}.\n"
    )
    return pdir


async def test_projects_empty(client):
    assert data(await client.call_tool("projects", {})) == []


async def test_projects_listing(brain, client):
    seed_project(brain, "alpha")
    seed_project(
        brain,
        "beta",
        status="paused",
        repos="\n  - name: beta\n    url: git@example.com:u/beta.git\n    host: hub",
    )
    commit_all(brain)
    result = data(await client.call_tool("projects", {}))
    assert result == [
        {"name": "alpha", "status": "active", "path": "projects/alpha", "repo_count": 0},
        {"name": "beta", "status": "paused", "path": "projects/beta", "repo_count": 1},
    ]


async def test_projects_malformed_frontmatter_still_listed(brain, client):
    seed_project(brain, "good")
    bad = brain / "projects" / "bad"
    bad.mkdir(parents=True)
    (bad / "project.md").write_text("no frontmatter here\n")
    (brain / "projects" / "manifestless").mkdir()
    result = data(await client.call_tool("projects", {}))
    statuses = {p["name"]: p["status"] for p in result}
    assert statuses == {"bad": "unknown", "good": "active", "manifestless": "unknown"}


async def test_projects_scale(brain, client):
    for i in range(300):
        seed_project(brain, f"proj-{i:03d}")
    result = data(await client.call_tool("projects", {}))
    assert len(result) == 300
    assert result[0]["name"] == "proj-000"


async def test_project_detail(brain, client):
    pdir = seed_project(brain, "alpha")
    (pdir / "notes.md").write_text("notes\n")
    (pdir / "sub").mkdir()
    (pdir / "sub" / "deep.md").write_text("deep\n")
    commit_all(brain)
    result = data(await client.call_tool("project", {"name": "alpha"}))
    assert result["frontmatter"]["status"] == "active"
    assert "Some prose about alpha" in result["prose"]
    assert result["files"] == [
        "projects/alpha/notes.md",
        "projects/alpha/project.md",
        "projects/alpha/sub/deep.md",
    ]


async def test_project_missing(brain, client):
    seed_project(brain, "alpha")
    with pytest.raises(ToolError, match="no such project 'ghost'"):
        await client.call_tool("project", {"name": "ghost"})


async def test_project_malformed_frontmatter_errors(brain, client):
    bad = brain / "projects" / "bad"
    bad.mkdir(parents=True)
    (bad / "project.md").write_text("just prose\n")
    with pytest.raises(ToolError, match="frontmatter"):
        await client.call_tool("project", {"name": "bad"})


async def test_project_name_traversal_rejected(client):
    for name in ["../inbox", "a/b", "..", ".hidden"]:
        with pytest.raises(ToolError):
            await client.call_tool("project", {"name": name})


async def test_project_create_plain(brain, client):
    result = data(
        await client.call_tool("project_create", {"name": "rocket", "summary": "Build a rocket."})
    )
    manifest = brain / "projects" / "rocket" / "project.md"
    text = manifest.read_text()
    assert text.startswith("---\nname: rocket\nstatus: active\n")
    assert "Build a rocket." in text
    assert result["path"] == "projects/rocket"
    assert commit_files(brain, result["commit"]) == ["projects/rocket/project.md"]
    msg = commit_message_of(brain, result["commit"])
    assert msg == "project_create: projects/rocket\n\nvia brain-mcp"


async def test_project_create_from_inbox_note_single_commit(brain, client):
    result = data(
        await client.call_tool(
            "project_create",
            {"name": "rocket", "summary": "Build a rocket.", "from_path": "inbox/idea.md"},
        )
    )
    assert not (brain / "inbox" / "idea.md").exists()
    assert (brain / "projects" / "rocket" / "idea.md").read_text().startswith("# Big idea")
    # scaffold + move landed in ONE commit
    assert commit_files(brain, result["commit"]) == [
        "inbox/idea.md",
        "projects/rocket/idea.md",
        "projects/rocket/project.md",
    ]
    log = git(brain, "log", "--oneline").stdout.splitlines()
    assert len(log) == 2  # seed + project_create


async def test_project_create_frontmatter_parses(brain, client):
    data(await client.call_tool("project_create", {"name": "parse-me", "summary": "Check YAML."}))
    result = data(await client.call_tool("project", {"name": "parse-me"}))
    fm = result["frontmatter"]
    assert fm["name"] == "parse-me"
    assert fm["status"] == "active"
    assert fm["repos"] == []


async def test_project_create_already_exists(brain, client):
    seed_project(brain, "alpha")
    with pytest.raises(ToolError, match="already exists"):
        await client.call_tool("project_create", {"name": "alpha", "summary": "dup"})


async def test_project_create_bad_from_path(client):
    with pytest.raises(ToolError, match="not a file"):
        await client.call_tool(
            "project_create", {"name": "x", "summary": "s", "from_path": "inbox/ghost.md"}
        )


async def test_project_create_traversal_rejected(client, tmp_path):
    with pytest.raises(ToolError):
        await client.call_tool("project_create", {"name": "../evil", "summary": "s"})
    with pytest.raises(ToolError):
        await client.call_tool(
            "project_create", {"name": "x", "summary": "s", "from_path": "../outside.md"}
        )
