"""readstate: the per-session ledger behind read-before-write."""

from brain_mcp import readstate


def test_record_and_seen():
    readstate.record("s1", "a.md", "hello")
    assert readstate.seen("s1", "a.md") == readstate.digest("hello")


def test_sessions_are_isolated():
    readstate.record("s1", "a.md", "hello")
    assert readstate.seen("s2", "a.md") is None


def test_unknown_session_and_path():
    readstate.record("s1", "a.md", "hello")
    assert readstate.seen("s1", "b.md") is None
    assert readstate.seen("ghost", "a.md") is None


def test_recording_again_updates_the_hash():
    readstate.record("s1", "a.md", "hello")
    readstate.record("s1", "a.md", "goodbye")
    assert readstate.seen("s1", "a.md") == readstate.digest("goodbye")


def test_forget_clears_every_session():
    readstate.record("s1", "a.md", "x")
    readstate.record("s2", "a.md", "x")
    readstate.forget("a.md")
    assert readstate.seen("s1", "a.md") is None
    assert readstate.seen("s2", "a.md") is None


def test_forget_is_prefix_aware():
    readstate.record("s1", "inbox/idea.md", "x")
    readstate.record("s1", "inbox-other/keep.md", "x")
    readstate.record("s1", "keep.md", "x")
    readstate.forget("inbox")
    assert readstate.seen("s1", "inbox/idea.md") is None
    assert readstate.seen("s1", "inbox-other/keep.md") is not None  # not a path prefix
    assert readstate.seen("s1", "keep.md") is not None


def test_session_lru_evicts_least_recently_used():
    for i in range(readstate.MAX_SESSIONS):
        readstate.record(f"s{i}", "a.md", "x")
    assert readstate.seen("s0", "a.md") is not None  # a read counts as use
    readstate.record("overflow", "a.md", "x")
    assert readstate.seen("s0", "a.md") is not None  # kept: most recently used
    assert readstate.seen("s1", "a.md") is None  # evicted


def test_path_lru_evicts_within_a_session():
    for i in range(readstate.MAX_PATHS):
        readstate.record("s1", f"p{i}.md", "x")
    readstate.record("s1", "overflow.md", "x")
    assert readstate.seen("s1", "p0.md") is None
    assert readstate.seen("s1", "p1.md") is not None
    assert readstate.seen("s1", "overflow.md") is not None


def test_reset():
    readstate.record("s1", "a.md", "x")
    readstate.reset()
    assert readstate.seen("s1", "a.md") is None
