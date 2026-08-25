"""Adversarial tests for the path-confinement layer."""

import pytest
from fastmcp.exceptions import ToolError

from brain_mcp.pathsafe import confine, prune_empty_dirs, rel, resolve_root


@pytest.fixture
def root(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "notes.md").write_text("hello\n")
    (brain / "sub").mkdir()
    (brain / "sub" / "deep.md").write_text("deep\n")
    return resolve_root(brain)


@pytest.fixture
def outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "secret.txt").write_text("secret\n")
    return out


# --- happy paths ---


def test_simple_file(root):
    assert confine(root, "notes.md") == root / "notes.md"


def test_nested_file(root):
    assert confine(root, "sub/deep.md") == root / "sub" / "deep.md"


def test_nonexistent_path_allowed(root):
    # write() needs to target files that don't exist yet
    assert confine(root, "new/dir/file.md") == root / "new" / "dir" / "file.md"


def test_empty_string_is_root_when_allowed(root):
    assert confine(root, "", allow_root=True) == root


def test_dot_is_root_when_allowed(root):
    assert confine(root, ".", allow_root=True) == root


def test_trailing_slash(root):
    assert confine(root, "sub/") == root / "sub"


def test_redundant_dot_segments(root):
    assert confine(root, "./sub/./deep.md") == root / "sub" / "deep.md"


def test_names_with_spaces_and_unicode(root):
    (root / "meeting notes.md").write_text("x\n")
    (root / "日本語.md").write_text("x\n")
    assert confine(root, "meeting notes.md") == root / "meeting notes.md"
    assert confine(root, "日本語.md") == root / "日本語.md"


def test_symlink_inside_root_allowed(root):
    (root / "alias").symlink_to(root / "sub")
    assert confine(root, "alias/deep.md") == root / "sub" / "deep.md"


# --- rejections ---


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../x",
        "../../etc/passwd",
        "sub/../../x",
        "sub/../../../x",
        "a/../../x",
        "sub/..%2F..",  # decoded form contains ..
        "%2e%2e/x",
        "%2e%2e%2fx",
        "..%2fx",
    ],
)
def test_traversal_rejected(root, path):
    with pytest.raises(ToolError):
        confine(root, path)


@pytest.mark.parametrize("path", ["/etc/passwd", "/", "C:/evil", "C:\\evil"])
def test_absolute_rejected(root, path):
    with pytest.raises(ToolError):
        confine(root, path)


def test_own_root_as_absolute_rejected(root):
    with pytest.raises(ToolError):
        confine(root, str(root / "notes.md"))


@pytest.mark.parametrize("path", ["~", "~/x", "~user/x"])
def test_tilde_rejected(root, path):
    with pytest.raises(ToolError):
        confine(root, path)


def test_backslash_rejected(root):
    with pytest.raises(ToolError):
        confine(root, "sub\\..\\..\\x")


def test_nul_byte_rejected(root):
    with pytest.raises(ToolError):
        confine(root, "a\x00b")


def test_empty_rejected_by_default(root):
    with pytest.raises(ToolError):
        confine(root, "")


def test_root_itself_rejected_without_allow_root(root):
    with pytest.raises(ToolError):
        confine(root, ".")


@pytest.mark.parametrize("path", [".git", ".git/config", ".git/hooks/pre-commit", "sub/.git/x"])
def test_git_dir_rejected(root, path):
    with pytest.raises(ToolError):
        confine(root, path)


def test_symlink_dir_to_outside_rejected(root, outside):
    (root / "evil").symlink_to(outside)
    with pytest.raises(ToolError):
        confine(root, "evil/secret.txt")


def test_symlink_file_to_outside_rejected(root, outside):
    (root / "link.md").symlink_to(outside / "secret.txt")
    with pytest.raises(ToolError):
        confine(root, "link.md")


def test_symlink_to_outside_dir_itself_rejected(root, outside):
    (root / "evil").symlink_to(outside)
    with pytest.raises(ToolError):
        confine(root, "evil")


def test_symlink_into_git_dir_rejected(root):
    (root / ".git").mkdir()
    (root / "gitlink").symlink_to(root / ".git")
    with pytest.raises(ToolError):
        confine(root, "gitlink/config")


def test_new_path_under_outside_symlink_rejected(root, outside):
    # writing a *new* file through a symlinked dir must not escape either
    (root / "evil").symlink_to(outside)
    with pytest.raises(ToolError):
        confine(root, "evil/new-note.md")


# --- rel ---


def test_rel_roundtrip(root):
    assert rel(root, confine(root, "sub/deep.md")) == "sub/deep.md"
    assert rel(root, confine(root, "", allow_root=True)) == ""


# --- pruning empty directories ---


def test_prune_walks_up_through_empty_ancestors(tmp_path):
    root = tmp_path / "brain"
    (root / "a" / "b" / "c").mkdir(parents=True)
    assert prune_empty_dirs(root, root / "a" / "b" / "c") == ["a/b/c", "a/b"]
    assert not (root / "a" / "b").exists()


def test_prune_stops_at_the_first_dir_holding_anything(tmp_path):
    root = tmp_path / "brain"
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "b" / "keep.md").write_text("x")
    assert prune_empty_dirs(root, root / "a" / "b" / "c") == ["a/b/c"]
    assert (root / "a" / "b" / "keep.md").read_text() == "x"


def test_prune_refuses_a_dir_holding_a_file(tmp_path):
    root = tmp_path / "brain"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "note.md").write_text("x")
    assert prune_empty_dirs(root, root / "a" / "b") == []
    assert (root / "a" / "b" / "note.md").read_text() == "x"


def test_prune_refuses_a_dir_holding_a_hidden_file(tmp_path):
    root = tmp_path / "brain"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / ".keep").write_text("x")
    assert prune_empty_dirs(root, root / "a" / "b") == []
    assert (root / "a" / "b" / ".keep").exists()


def test_prune_leaves_the_skeleton_alone(tmp_path):
    root = tmp_path / "brain"
    (root / "inbox").mkdir(parents=True)
    assert prune_empty_dirs(root, root / "inbox") == []
    assert (root / "inbox").is_dir()


def test_prune_never_removes_the_root(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    assert prune_empty_dirs(root, root) == []
    assert root.is_dir()
