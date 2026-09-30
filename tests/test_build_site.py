"""scripts/build-site.sh: builds from a commit, publishes by swapping a symlink.

MkDocs itself is stubbed — a fake `uvx` on PATH that copies the export it was
handed into the output directory — so these run offline and assert on exactly
what the script fed the builder."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import commit_all, head_sha

SCRIPT = Path(__file__).parent.parent / "scripts" / "build-site.sh"

FAKE_UVX = """#!/bin/sh
# Stands in for: uvx --from mkdocs==X mkdocs build --quiet -f <config> -d <out>
while [ $# -gt 0 ]; do
    case "$1" in
        -f) config="$2"; shift ;;
        -d) out="$2"; shift ;;
    esac
    shift
done
[ -n "${FAKE_UVX_FAIL:-}" ] && exit 1
mkdir -p "$out"
cp -R "$(dirname "$config")/docs/." "$out/"
cp "$config" "$out/mkdocs.yml"
"""


@pytest.fixture
def build(brain, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uvx = bin_dir / "uvx"
    uvx.write_text(FAKE_UVX)
    uvx.chmod(0o755)
    site = tmp_path / "site"

    def run(**extra):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "BRAIN_ROOT": str(brain),
            "BRAIN_SITE_DIR": str(site),
            **extra,
        }
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)

    run.site = site
    return run


def test_publishes_the_commit_not_the_working_tree(brain, build):
    (brain / "inbox" / "draft.md").write_text("half-written\n")  # never committed
    (brain / "inbox" / "idea.md").write_text("edited but not committed\n")

    proc = build()
    assert proc.returncode == 0, proc.stderr

    current = build.site / "current"
    assert current.is_symlink()
    assert (current / "agents.md").exists()
    assert not (current / "inbox" / "draft.md").exists()
    assert "Build a rocket" in (current / "inbox" / "idea.md").read_text()
    assert (current / "mkdocs.yml").exists()  # the repo's config was handed over


def test_generates_an_index_naming_the_commit(brain, build):
    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    assert head_sha(brain)[:12] in index
    assert "[inbox/idea.md](<inbox/idea.md>)" in index


def test_index_skips_notes_that_no_longer_exist(brain, build):
    (brain / "inbox" / "idea.md").unlink()
    commit_all(brain, "delete idea")
    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    assert "inbox/idea.md" not in index
    assert "agents.md" in index


def test_a_brain_with_its_own_index_keeps_it(brain, build):
    (brain / "index.md").write_text("# Mine\n")
    commit_all(brain, "own index")
    assert build().returncode == 0
    assert (build.site / "current" / "index.md").read_text() == "# Mine\n"


def test_rebuild_swaps_current_and_prunes_old_releases(brain, build):
    for i in range(5):
        (brain / "inbox" / f"n{i}.md").write_text(f"{i}\n")
        commit_all(brain, f"n{i}")
        assert build().returncode == 0

    assert (build.site / "current" / "inbox" / "n4.md").exists()
    releases = list((build.site / "releases").iterdir())
    assert len(releases) == 3
    assert (build.site / "current").resolve() in [r.resolve() for r in releases]
    assert not list(build.site.glob("work.*"))  # work dirs cleaned up


def test_a_failed_build_leaves_the_published_site_alone(brain, build):
    assert build().returncode == 0
    before = (build.site / "current").resolve()

    (brain / "inbox" / "new.md").write_text("new\n")
    commit_all(brain, "new")
    assert build(FAKE_UVX_FAIL="1").returncode != 0

    assert (build.site / "current").resolve() == before
    assert not list(build.site.glob("work.*"))
