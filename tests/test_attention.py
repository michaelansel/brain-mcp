"""Read tracking: the event log git cannot keep for us."""

import sqlite3
import time

import pytest

from brain_mcp.attention import Attention, default_state_dir
from tests.conftest import commit_all, data


@pytest.fixture
def attention(tmp_path):
    return Attention(tmp_path / "state", keep_days=90)


def rows(att):
    with sqlite3.connect(att.path) as db:
        return db.execute("SELECT ts, path, tool FROM reads ORDER BY ts").fetchall()


# --- the store ---


async def test_records_an_event_per_read(attention):
    await attention.record("projects/rocket", "read")
    await attention.record("projects/rocket", "project")
    assert [(r[1], r[2]) for r in rows(attention)] == [
        ("projects/rocket", "read"),
        ("projects/rocket", "project"),
    ]


async def test_counts_reads_and_the_latest(attention):
    for _ in range(3):
        await attention.record("projects/rocket", "read")
    await attention.record("projects/kite", "read")
    counts = await attention.counts(["projects/rocket", "projects/kite"])
    assert counts["projects/rocket"]["reads"] == 3
    assert counts["projects/kite"]["reads"] == 1
    assert counts["projects/rocket"]["last_read"] == time.strftime("%Y-%m-%d", time.gmtime())


async def test_a_folder_never_read_is_absent_rather_than_zero(attention):
    await attention.record("projects/rocket", "read")
    assert await attention.counts(["projects/kite"]) == {}


async def test_events_past_the_window_are_pruned(attention):
    """Kept bounded by the insert itself — no scheduler, no separate pass."""
    await attention.record("projects/rocket", "read")
    old = int(time.time()) - 91 * 86400
    with sqlite3.connect(attention.path) as db:
        db.execute("INSERT INTO reads (ts, path, tool) VALUES (?, ?, ?)", (old, "old", "read"))
    await attention.record("projects/kite", "read")  # any write prunes
    assert {r[1] for r in rows(attention)} == {"projects/rocket", "projects/kite"}


async def test_counting_an_empty_list_asks_nothing(attention):
    assert await attention.counts([]) == {}


# --- it must never break a read ---


async def test_an_unusable_state_dir_degrades_quietly(tmp_path):
    """Losing a read event costs a little signal; failing the read would cost
    the brain."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("I am a file\n")
    att = Attention(blocked / "state", keep_days=90)
    await att.record("projects/rocket", "read")  # must not raise
    assert await att.counts(["projects/rocket"]) == {}


def test_the_default_lives_outside_the_brain(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg")
    assert default_state_dir().as_posix() == "/tmp/xdg/brain-mcp"


# --- what the tools record ---


@pytest.fixture
def brain(brain):
    proj = brain / "projects" / "rocket"
    proj.mkdir(parents=True)
    (proj / "project.md").write_text("---\nname: rocket\nstatus: active\n---\n\n# rocket\n")
    (proj / "summary.md").write_text("---\ntitle: rocket\n---\n\nFlies.\n")
    commit_all(brain, "seed project")
    return brain


def db_rows(config):
    """Rows recorded, or [] when nothing ever wrote — the db is created lazily
    on the first event, so its absence is a real answer."""
    try:
        with sqlite3.connect(config.state_dir / "attention.db") as db:
            return db.execute("SELECT path, tool FROM reads").fetchall()
    except sqlite3.OperationalError:
        return []


async def test_reading_a_summary_records_attention(client, config):
    await client.call_tool("read", {"path": "projects/rocket/summary.md"})
    assert db_rows(config) == [("projects/rocket", "read")]


async def test_orienting_on_a_project_records_attention(client, config):
    await client.call_tool("project", {"name": "rocket"})
    assert db_rows(config) == [("projects/rocket", "project")]


async def test_reading_an_ordinary_note_records_nothing(client, config):
    await client.call_tool("read", {"path": "projects/rocket/project.md"})
    assert db_rows(config) == []


async def test_surveying_records_nothing(client, config):
    """projects() names no folder anyone is orienting on. Counting it would
    make every project look freshly attended the moment anything surveyed."""
    await client.call_tool("projects", {"summaries_too": True})
    assert db_rows(config) == []


async def test_the_survey_carries_counts_and_staleness(client, config):
    await client.call_tool("project", {"name": "rocket"})
    listed = data(await client.call_tool("projects", {"summaries_too": True}))
    rocket = next(p for p in listed if p["name"] == "rocket")
    assert rocket["reads"] == 1
    assert "summary_stale" in rocket  # no covers: line, so it accounts for nothing


async def test_the_plain_survey_stays_frontmatter_only(client, config):
    listed = data(await client.call_tool("projects", {}))
    assert set(listed[0]) == {"name", "status", "path", "repo_count"}
