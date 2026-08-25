"""Folder summaries: how far a summary.md has fallen behind its notes.

Everything here is derived from git on each call, so the tests drive real
commits against the scratch brain rather than stubbing anything out.
"""

from dataclasses import replace

import pytest
from fastmcp import Client

from brain_mcp.server import build_server
from tests.conftest import commit_all, data, git


@pytest.fixture
def brain(brain):
    """The scratch brain, plus a project whose summary is current."""
    proj = brain / "projects" / "rocket"
    proj.mkdir(parents=True)
    (proj / "project.md").write_text("---\nname: rocket\nstatus: active\n---\n\n# rocket\n\nFly.\n")
    (proj / "fuel.md").write_text("# Fuel\n\nKerosene.\n")
    commit_all(brain, "seed project")
    (proj / "summary.md").write_text(
        "---\ntitle: rocket\nsurveyed: 2026-08-01\n---\n\nFuel picked.\n"
    )
    commit_all(brain, "write summary")
    return brain


@pytest.fixture
async def cold_client(config):
    """A client seeing the folder as no longer being actively worked in.

    Test commits are all seconds old, which is exactly the burst the quiet
    window suppresses. Zeroing it isolates the ask-conditions from the
    suppression, which has tests of its own below."""
    async with Client(build_server(replace(config, summary_quiet_minutes=0))) as c:
        yield c


def touch(brain, relpath, text, message):
    path = brain / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    commit_all(brain, message)


async def read_summary(client, folder="projects/rocket"):
    return data(await client.call_tool("read", {"path": f"{folder}/summary.md"}))


# --- nothing to report ---


async def test_current_summary_reports_nothing(client):
    assert "summary_stale" not in await read_summary(client)


async def test_ordinary_file_is_untouched(client, brain):
    result = data(await client.call_tool("read", {"path": "inbox/idea.md"}))
    assert set(result) == {"content", "hash"}


async def test_uncommitted_summary_has_no_baseline(client, brain):
    """A summary written by hand and never committed has no point in history
    to measure drift from, so it reports nothing rather than guessing."""
    (brain / "projects" / "moon").mkdir(parents=True)
    (brain / "projects" / "moon" / "summary.md").write_text("Fresh.\n")
    assert "summary_stale" not in await read_summary(client, "projects/moon")


# --- the delta ---


async def test_modified_note_appears_in_the_delta(client, brain):
    touch(brain, "projects/rocket/fuel.md", "# Fuel\n\nHydrogen.\n", "switch fuel")
    stale = (await read_summary(client))["summary_stale"]
    assert stale["changed"] == ["M projects/rocket/fuel.md"]
    assert stale["since"]


async def test_added_and_deleted_notes_appear(client, brain):
    touch(brain, "projects/rocket/engine.md", "# Engine\n", "add engine")
    git(brain, "rm", "-q", "--", "projects/rocket/fuel.md")
    commit_all(brain, "drop fuel")
    changed = (await read_summary(client))["summary_stale"]["changed"]
    assert changed == ["A projects/rocket/engine.md", "D projects/rocket/fuel.md"]


async def test_uncommitted_hand_edit_is_seen(client, brain):
    """Only this server commits, so a hand edit can sit uncommitted forever.
    A diff of commits alone would miss it."""
    (brain / "projects" / "rocket" / "fuel.md").write_text("# Fuel\n\nMethane.\n")
    changed = (await read_summary(client))["summary_stale"]["changed"]
    assert changed == ["M projects/rocket/fuel.md"]


async def test_untracked_note_is_seen_as_added(client, brain):
    (brain / "projects" / "rocket" / "crew.md").write_text("# Crew\n")
    changed = (await read_summary(client))["summary_stale"]["changed"]
    assert changed == ["A projects/rocket/crew.md"]


async def test_changes_outside_the_folder_are_ignored(client, brain):
    touch(brain, "inbox/other.md", "# Other\n", "unrelated")
    assert "summary_stale" not in await read_summary(client)


async def test_editing_the_summary_rebaselines_it(client, brain):
    touch(brain, "projects/rocket/fuel.md", "# Fuel\n\nHydrogen.\n", "switch fuel")
    assert "summary_stale" in await read_summary(client)
    touch(brain, "projects/rocket/summary.md", "Rewritten.\n", "refresh summary")
    assert "summary_stale" not in await read_summary(client)


async def test_summary_excludes_its_own_uncommitted_edit(client, brain):
    """Revising the summary is the author at work, not drift."""
    (brain / "projects" / "rocket" / "summary.md").write_text("Half-rewritten.\n")
    assert "summary_stale" not in await read_summary(client)


# --- whether a rewrite is asked for ---


async def test_structural_change_asks_for_a_rewrite(cold_client, brain):
    """One added note is enough: the folder's contents changed, not just wording."""
    touch(brain, "projects/rocket/engine.md", "# Engine\n", "add engine")
    assert "refresh" in (await read_summary(cold_client))["summary_stale"]


async def test_one_edit_reports_the_delta_without_asking(cold_client, brain):
    """Below every threshold: say what changed, don't interrupt."""
    touch(brain, "projects/rocket/fuel.md", "# Fuel\n\nHydrogen.\n", "switch fuel")
    stale = (await read_summary(cold_client))["summary_stale"]
    assert stale["changed"] and "refresh" not in stale


async def test_enough_edits_ask_for_a_rewrite(cold_client, brain, config):
    for i in range(config.summary_min_files):
        touch(brain, f"projects/rocket/note{i}.md", "x\n", f"note {i}")
        git(brain, "rm", "-q", "--", f"projects/rocket/note{i}.md")
        commit_all(brain, f"drop {i}")
    for i in range(config.summary_min_files):
        (brain / "projects" / "rocket" / f"part{i}.md").write_text("seed\n")
    commit_all(brain, "seed parts")
    touch(brain, "projects/rocket/summary.md", "Rewritten.\n", "refresh")
    for i in range(config.summary_min_files):
        touch(brain, f"projects/rocket/part{i}.md", f"edited {i}\n", f"edit {i}")
    stale = (await read_summary(cold_client))["summary_stale"]
    assert len(stale["changed"]) == config.summary_min_files
    assert "refresh" in stale


async def test_age_alone_asks_for_a_rewrite(brain, config):
    """A single edit, but the summary was written long ago."""
    touch(brain, "projects/rocket/fuel.md", "# Fuel\n\nHydrogen.\n", "switch fuel")
    aged = replace(config, summary_max_age_days=0, summary_quiet_minutes=0)
    async with Client(build_server(aged)) as c:
        assert "refresh" in (await read_summary(c))["summary_stale"]


async def test_a_burst_still_in_progress_is_not_interrupted(brain, config):
    """Every agent write commits, so a run of very recent commits is someone
    working right now — they already know what they changed."""
    touch(brain, "projects/rocket/engine.md", "# Engine\n", "add engine")
    busy = replace(config, summary_quiet_minutes=60)
    async with Client(build_server(busy)) as c:
        stale = (await read_summary(c))["summary_stale"]
    assert stale["changed"] and "refresh" not in stale


async def test_the_same_burst_once_it_has_gone_quiet(brain, config):
    touch(brain, "projects/rocket/engine.md", "# Engine\n", "add engine")
    quiet = replace(config, summary_quiet_minutes=0)
    async with Client(build_server(quiet)) as c:
        assert "refresh" in (await read_summary(c))["summary_stale"]


async def test_uncommitted_change_is_never_suppressed(brain, config):
    """An uncommitted edit is a hand edit — exactly the drift worth surfacing."""
    (brain / "projects" / "rocket" / "crew.md").write_text("# Crew\n")
    busy = replace(config, summary_quiet_minutes=10_000)
    async with Client(build_server(busy)) as c:
        assert "refresh" in (await read_summary(c))["summary_stale"]


# --- the project tool ---


async def test_project_reports_a_stale_summary(cold_client, brain):
    touch(brain, "projects/rocket/engine.md", "# Engine\n", "add engine")
    result = data(await cold_client.call_tool("project", {"name": "rocket"}))
    assert "refresh" in result["summary_stale"]


async def test_project_reports_a_missing_summary(client, brain):
    git(brain, "rm", "-q", "--", "projects/rocket/summary.md")
    commit_all(brain, "drop summary")
    result = data(await client.call_tool("project", {"name": "rocket"}))
    assert "summary_stale" not in result
    assert "none written" in result["summary"]


async def test_project_stays_quiet_when_current(client, brain):
    result = data(await client.call_tool("project", {"name": "rocket"}))
    assert "summary_stale" not in result and "summary" not in result


# --- the rule is structural, so it reaches the root too ---


async def test_a_root_summary_covers_the_whole_brain(cold_client, brain):
    """Nothing special-cases projects/. A summary.md at the root summarises
    everything under it, which is what the structural rule means."""
    touch(brain, "summary.md", "The brain, in brief.\n", "root summary")
    touch(brain, "inbox/later.md", "# Later\n", "unrelated note")
    stale = data(await cold_client.call_tool("read", {"path": "summary.md"}))["summary_stale"]
    assert "A inbox/later.md" in stale["changed"]
