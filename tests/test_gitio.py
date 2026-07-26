"""Push-on-commit: best-effort, non-blocking, ordered, self-healing."""

import asyncio

from brain_mcp import gitio
from tests.conftest import data, git


def make_bare(tmp_path, name="remote"):
    bare = tmp_path / f"{name}.git"
    git(tmp_path, "init", "-q", "--bare", str(bare), check=True)
    return bare


def branch_of(root):
    return git(root, "symbolic-ref", "--short", "HEAD").stdout.strip()


def log_of(root, branch):
    return git(root, "log", "--format=%H", branch).stdout.split()


async def drain_pushes():
    if gitio._pending_pushes:
        await asyncio.gather(*gitio._pending_pushes, return_exceptions=True)


async def test_push_succeeds(brain, client, tmp_path):
    bare = make_bare(tmp_path)
    git(brain, "remote", "add", "home", str(bare))
    gitio.configure_push("home")

    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    await drain_pushes()

    remote_log = log_of(bare, branch_of(brain))
    assert result["commit"] in remote_log


async def test_push_failure_does_not_block_or_raise(brain, client, tmp_path, caplog):
    gitio.configure_push(str(tmp_path / "does-not-exist.git"))

    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert result["path"] == "inbox/note.md"
    await drain_pushes()

    assert any("push" in r.message and "failed" in r.message for r in caplog.records)


async def test_no_push_configured_is_a_noop(brain, client):
    gitio.configure_push(None)
    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    assert result["path"] == "inbox/note.md"
    assert not gitio._pending_pushes


async def test_concurrent_writes_push_in_commit_order(brain, client, tmp_path):
    bare = make_bare(tmp_path)
    git(brain, "remote", "add", "home", str(bare))
    gitio.configure_push("home")

    await asyncio.gather(
        *(
            client.call_tool("write", {"path": f"inbox/note-{i}.md", "content": f"{i}\n"})
            for i in range(5)
        )
    )
    await drain_pushes()

    branch = branch_of(brain)
    local_log = log_of(brain, branch)
    remote_log = log_of(bare, branch)
    assert remote_log == local_log


async def test_push_is_non_blocking(brain, client, tmp_path, monkeypatch):
    bare = make_bare(tmp_path)
    git(brain, "remote", "add", "home", str(bare))
    gitio.configure_push("home")

    real_run_git = gitio.run_git

    async def slow_run_git(root, *args, check=True):
        if args and args[0] == "push":
            await asyncio.sleep(0.5)
        return await real_run_git(root, *args, check=check)

    monkeypatch.setattr(gitio, "run_git", slow_run_git)

    loop = asyncio.get_event_loop()
    start = loop.time()
    result = data(await client.call_tool("write", {"path": "inbox/note.md", "content": "hi\n"}))
    elapsed = loop.time() - start

    assert result["path"] == "inbox/note.md"
    assert elapsed < 0.4  # tool returned well before the 0.5s push sleep
    assert gitio._pending_pushes  # push still in flight
    await drain_pushes()


async def test_push_self_heals_after_outage(brain, client, tmp_path):
    missing = tmp_path / "will-exist-later.git"
    git(brain, "remote", "add", "home", str(missing))
    gitio.configure_push("home")

    await client.call_tool("write", {"path": "inbox/first.md", "content": "1\n"})
    await drain_pushes()

    make_bare(tmp_path, name="will-exist-later")
    result = data(await client.call_tool("write", {"path": "inbox/second.md", "content": "2\n"}))
    await drain_pushes()

    branch = branch_of(brain)
    remote_log = log_of(missing, branch)
    local_log = log_of(brain, branch)
    assert remote_log == local_log
    assert result["commit"] in remote_log
