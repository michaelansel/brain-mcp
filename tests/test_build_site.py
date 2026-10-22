"""scripts/build-site.sh: builds from a commit, publishes by swapping a symlink.

MkDocs itself is stubbed — a fake `uvx` on PATH that copies the export it was
handed into the output directory — so these run offline and assert on exactly
what the script fed the builder."""

import json
import os
import subprocess
import sys
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
    assert "- inbox" in (current / ".nav.yml").read_text()  # and its sidebar order
    assert (current / "extra.css").exists()  # and its theme additions


def test_generates_an_index_naming_the_commit(brain, build):
    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    assert head_sha(brain)[:12] in index
    assert "[Big idea](<inbox/idea.md>)" in index  # by title, not path


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


def test_lists_every_published_file_for_offline_reading(brain, build):
    assert build().returncode == 0
    current = build.site / "current"
    files = json.loads((current / "offline.json").read_text())["files"]
    assert "agents.md" in files and "inbox/idea.md" in files
    assert "manifest.webmanifest" in files and "extra.css" in files
    assert "icons/icon-180.png" in files  # the home-screen icon
    assert not any(p.startswith("reset") for p in files)
    assert "sw.js" not in files and "offline.json" not in files
    assert (current / "reset" / "index.html").exists()


def test_a_folder_page_is_listed_by_the_folder(brain, build, tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    from offline import listing

    out = tmp_path / "out"
    (out / "areas").mkdir(parents=True)
    (out / "index.html").write_text("home")
    (out / "areas" / "index.html").write_text("areas")
    (out / "areas" / "x.js.map").write_text("{}")
    assert sorted(listing(out)) == ["", "areas/"]


def test_the_worker_changes_exactly_when_the_site_does(brain, build):
    def worker():
        assert build().returncode == 0
        return (build.site / "current" / "sw.js").read_text()

    first = worker()
    assert "__VERSION__" not in first and "brain-${VERSION}" in first
    (brain / "inbox" / "idea.md").write_text("# Big idea, revised\n")
    commit_all(brain, "edit")
    assert worker() != first


def test_offline_off_publishes_a_worker_that_clears_the_saved_copy(brain, build):
    assert build(BRAIN_SITE_OFFLINE="0").returncode == 0
    worker = (build.site / "current" / "sw.js").read_text()
    assert "unregister()" in worker and "__VERSION__" not in worker


def test_script_in_the_brain_is_never_published(brain, build):
    (brain / "inbox" / "evil.js").write_text("self.addEventListener('fetch', () => {})\n")
    (brain / "inbox" / "evil.mjs").write_text("export {}\n")
    commit_all(brain, "script")
    assert build().returncode == 0
    current = build.site / "current"
    assert not (current / "inbox" / "evil.js").exists()
    assert not (current / "inbox" / "evil.mjs").exists()
    assert (current / "inbox" / "idea.md").exists()


def test_landing_page_lists_everything_at_the_top_level(brain, build):
    archived = brain / "archive" / "old-plan"
    archived.mkdir(parents=True)
    (archived / "plan.md").write_text("# Old plan\n")
    commit_all(brain, "archive")
    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    everything = index.split("## Everything", 1)[1]
    assert "### Folders" in everything and "### Notes" in everything
    assert "[Archive](<archive/index.md>)" in everything
    assert "[Inbox](<inbox/index.md>)" in everything
    assert "](<agents.md>)" in everything
    assert "](<index.md>)" not in everything  # not the landing page itself


def at(day):
    """Environment that dates the next commit to `day`."""
    stamp = f"{day}T12:00:00+0000"
    return {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def commit_on(root, day, message):
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@test",
            "commit",
            "-q",
            "-m",
            message,
        ],
        check=True,
        env=at(day),
    )


def test_recent_notes_sit_under_the_day_they_changed(brain, build):
    (brain / "inbox" / "older.md").write_text(
        "---\ntitle: An older note\ncreated: 2026-10-01\n---\n\nText.\n"
    )
    commit_on(brain, "2026-10-09", "older")
    (brain / "inbox" / "newer.md").write_text("# A newer note\n")
    commit_on(brain, "2026-10-11", "newer")

    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    newer, older = (
        index.index("[A newer note](<inbox/newer.md>)"),
        index.index("[An older note](<inbox/older.md>)"),
    )
    assert (
        index.index("### Sunday 11 October") < newer < index.index("### Friday 9 October") < older
    )


def test_projects_active_this_week_lead_with_their_summary(brain, build):
    old = brain / "projects" / "old-thing"
    old.mkdir(parents=True)
    (old / "project.md").write_text("---\nname: old-thing\nstatus: active\n---\n")
    commit_on(brain, "2026-09-01", "old project")
    live = brain / "projects" / "event-intake"
    live.mkdir()
    (live / "plan.md").write_text("# Plan\n")
    (live / "summary.md").write_text(
        "---\ntitle: Summary\nsurveyed: 2026-10-10\n---\n\n# Where it stands\n\n"
        "The build plan is [adopted](plan.md) and spikes are done.\n\n## Open\n\nCalendars.\n"
    )
    commit_on(brain, "2026-10-10", "live project")

    assert build().returncode == 0
    index = (build.site / "current" / "index.md").read_text()
    active = index.split("## Active this week", 1)[1].split("## Recently changed", 1)[0]
    assert "[Event intake](<projects/event-intake/index.md>)" in active
    assert "2 notes changed" in active
    assert "The build plan is adopted and spikes are done." in active  # links reduced to text
    assert "old-thing" not in active.lower() and "Old thing" not in active


def test_folders_get_a_page_listing_notes_by_title_newest_first(brain, build):
    project = brain / "projects" / "event-intake"
    project.mkdir(parents=True)
    (project / "first.md").write_text("---\ntitle: First note\n---\n")
    commit_on(brain, "2026-10-01", "first")
    (project / "second.md").write_text("# Second note\n")
    (project / "summary.md").write_text("---\nsurveyed: 2026-10-05\n---\n\n# State\n\nAll good.\n")
    commit_on(brain, "2026-10-05", "second")

    assert build().returncode == 0
    current = build.site / "current"
    page = (current / "projects" / "event-intake" / "index.md").read_text()
    assert page.startswith("# Event intake\n")
    assert (
        "*As of 2026-10-05.*" in page and "## State" in page
    )  # summary heading pushed down a level
    assert page.index("[Second note](<second.md>)") < page.index("[First note](<first.md>)")
    assert "summary.md" not in page  # shown above, not listed
    parent = (current / "projects" / "index.md").read_text()
    assert "[Event intake](<event-intake/index.md>)" in parent


def test_a_folder_page_the_brain_already_has_is_kept(brain, build):
    (brain / "inbox" / "index.md").write_text("# My inbox\n")
    (brain / "areas" / "README.md").write_text("# About areas\n")
    commit_all(brain, "own folder pages")

    assert build().returncode == 0
    current = build.site / "current"
    assert (current / "inbox" / "index.md").read_text() == "# My inbox\n"
    assert not (current / "areas" / "index.md").exists()  # README.md is already the folder's page


def test_projects_created_this_week_are_marked_new(brain, build):
    old = brain / "projects" / "old-thing"
    old.mkdir(parents=True)
    (old / "project.md").write_text("---\nname: old-thing\ncreated: 2026-09-01\n---\n")
    commit_on(brain, "2026-09-01", "old project")
    (old / "notes.md").write_text("# Notes\n")
    commit_on(brain, "2026-10-10", "old project still busy")
    fresh = brain / "projects" / "fresh-idea"
    fresh.mkdir()
    (fresh / "project.md").write_text("---\nname: fresh-idea\ncreated: 2026-10-08\n---\n")
    commit_on(brain, "2026-10-08", "fresh project")
    hand = brain / "projects" / "hand-made"
    hand.mkdir()
    (hand / "plan.md").write_text("# Plan\n")  # no manifest: the first commit counts
    commit_on(brain, "2026-10-09", "hand-made project")

    assert build().returncode == 0
    current = build.site / "current"
    index = (current / "index.md").read_text()
    active = index.split("## Active this week", 1)[1].split("## Recently changed", 1)[0]
    listing = (current / "projects" / "index.md").read_text()
    chip = '<span class="brain-chip">New</span>'
    for text in (active, listing):
        rows = {line.split("]")[0]: line for line in text.splitlines() if line.startswith("- [")}
        assert chip in rows["- [Fresh idea"]
        assert chip in rows["- [Hand made"]
        assert chip not in rows["- [Old thing"]
