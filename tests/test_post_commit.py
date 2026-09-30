"""Post-commit command: best-effort, non-blocking, coalescing, bounded."""

import asyncio

from brain_mcp import gitio
from tests.conftest import data, head_sha


def make_script(tmp_path, body):
    script = tmp_path / "post-commit.sh"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return str(script)


async def drain():
    while gitio._pending_post_commits:
        await asyncio.gather(*gitio._pending_post_commits, return_exceptions=True)


async def test_runs_after_commit_in_the_brain(brain, client, tmp_path):
    out = tmp_path / "out"
    gitio.configure_post_commit(
        make_script(tmp_path, f'echo "$BRAIN_ROOT $(pwd -P) $(git rev-parse HEAD)" > {out}')
    )

    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    await drain()

    root = str(brain.resolve())
    assert out.read_text().split() == [root, root, result["commit"]]


async def test_failure_does_not_block_or_raise(brain, client, tmp_path, caplog):
    gitio.configure_post_commit(make_script(tmp_path, "echo boom; exit 3"))

    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert result["path"] == "inbox/note.md"
    await drain()

    assert any("failed (exit 3)" in r.message and "boom" in r.message for r in caplog.records)


async def test_missing_command_does_not_block_or_raise(brain, client, tmp_path, caplog):
    gitio.configure_post_commit(str(tmp_path / "does-not-exist"))

    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert result["path"] == "inbox/note.md"
    await drain()

    assert any("unexpected error running post-commit" in r.message for r in caplog.records)


async def test_not_configured_is_a_noop(brain, client):
    gitio.configure_post_commit(None)
    await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"})
    assert not gitio._pending_post_commits


async def test_is_non_blocking(brain, client, tmp_path):
    gitio.configure_post_commit(make_script(tmp_path, "sleep 0.5"))

    loop = asyncio.get_event_loop()
    start = loop.time()
    await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"})
    elapsed = loop.time() - start

    assert elapsed < 0.4  # tool returned well before the 0.5s command finished
    assert gitio._pending_post_commits  # still in flight
    await drain()


async def test_burst_of_commits_coalesces(brain, client, tmp_path):
    log = tmp_path / "runs"
    gitio.configure_post_commit(make_script(tmp_path, f"git rev-parse HEAD >> {log}; sleep 0.3"))

    for i in range(6):
        await client.call_tool("write", {"path": f"inbox/note-{i}.md", "content": f"{i}\n"})
    await drain()

    runs = log.read_text().split()
    assert len(runs) < 6  # not one run per commit
    assert runs[-1] == head_sha(brain)  # and the last run saw the newest commit


async def test_timeout_kills_the_command_and_frees_the_lock(
    brain, client, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(gitio, "POST_COMMIT_TIMEOUT", 0.2)
    gitio.configure_post_commit(make_script(tmp_path, "sleep 30"))

    await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"})
    await drain()

    assert any("killed after" in r.message for r in caplog.records)
    assert not gitio._post_commit_lock.locked()
