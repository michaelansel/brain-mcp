"""Writes the brain site's generated pages into an export of the brain.

Called by build-site.sh with the export directory (a `git archive` of the
commit being built), the brain repo, that commit, and the build time. It adds
markdown and nothing else: a landing page, and an index.md for each folder so
the theme has a page to show for it. A page the brain already has is kept.

    site_pages.py <docs> <brain_root> <sha> <built>
"""

from __future__ import annotations

import datetime as dt
import html
import re
import subprocess
import sys
from pathlib import Path

from brain_mcp.frontmatter import split

RECENT = 20  # notes on the landing page
ACTIVE_DAYS = 7  # how far back "Active this week" looks, counting HEAD's day
GIST = 200  # characters of a project's summary shown on the landing page


def history(root: Path, sha: str) -> list[tuple[str, str]]:
    """(day, path) for every change to a note, newest first, repeats kept."""
    out = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "core.quotePath=false",
            "log",
            "--format=@%cs",
            "--name-only",
            sha,
            "--",
            "*.md",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    changes, day = [], ""
    for line in out.splitlines():
        if line.startswith("@"):
            day = line[1:]
        elif line:
            changes.append((day, line))
    return changes


def last_changed(changes: list[tuple[str, str]]) -> dict[str, str]:
    days: dict[str, str] = {}
    for day, path in changes:
        days.setdefault(path, day)
    return days


def dir_title(name: str) -> str:
    """The same title MkDocs gives a folder in the sidebar."""
    title = name.replace("-", " ").replace("_", " ")
    return title.capitalize() if title.lower() == title else title


def note_title(file: Path) -> str:
    """Frontmatter title (or a manifest's name), else the first heading, else
    the filename — the order MkDocs itself uses, plus the manifest's name."""
    meta, body = split(file.read_text(encoding="utf-8", errors="replace"))
    meta = meta or {}
    for key in ("title", "name"):
        if isinstance(meta.get(key), str) and meta[key].strip():
            return meta[key].strip()
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return dir_title(file.stem)


def day_label(day: str, this_year: int, weekday: bool = True) -> str:
    d = dt.date.fromisoformat(day)
    label = f"{d.day} {d.strftime('%B')}"
    if weekday:
        label = f"{d.strftime('%A')} {label}"
    return label if d.year == this_year else f"{label} {d.year}"


def md_text(text: str) -> str:
    """Text safe to put inside a markdown link's [...]."""
    return re.sub(r"([\\\[\]*_`<>])", r"\\\1", text)


def item(title: str, link: str, meta: str = "") -> str:
    """A list item linking to a page, with a quiet note after it."""
    after = f' <span class="brain-meta">{html.escape(meta)}</span>' if meta else ""
    return f"- [{md_text(title)}](<{link}>){after}"


def index_page(docs: Path, folder: Path) -> Path | None:
    """The page MkDocs will serve for a folder, if it has one."""
    for name in ("index.md", "README.md"):
        if (folder / name).is_file():
            return folder / name
    return None


def demote(body: str) -> str:
    """Push every heading down a level, outside code fences, so a note can
    sit under a heading of its own."""
    lines, fenced = [], False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and re.match(r"#{1,5} ", line):
            line = "#" + line
        lines.append(line)
    return "\n".join(lines)


def gist(body: str) -> str:
    """The first paragraph of prose, links reduced to their text, clipped."""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para or para.startswith(("#", "|", "```", "-", "*", ">")):
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", " ".join(para.split()))
        if len(text) > GIST:
            text = text[:GIST].rsplit(" ", 1)[0] + "…"
        return text
    return ""


def folder_page(docs: Path, folder: Path, days: dict[str, str], this_year: int) -> str:
    lines = [f"# {dir_title(folder.name)}", ""]

    summary = folder / "summary.md"
    if summary.is_file():
        meta, body = split(summary.read_text(encoding="utf-8", errors="replace"))
        surveyed = (meta or {}).get("surveyed")
        lines += ["## Summary", ""]
        if surveyed:
            lines += [f"*As of {surveyed}.*", ""]
        lines += [demote(body).strip(), ""]

    return "\n".join(lines + contents(docs, folder, days, this_year))


def contents(docs: Path, folder: Path, days: dict[str, str], this_year: int) -> list[str]:
    """A folder's subfolders, then its notes, each by title, newest first."""
    rel = folder.relative_to(docs).as_posix()
    rel = "" if rel == "." else rel + "/"
    lines: list[str] = []

    def newest(path: str) -> str:
        return days.get(path, "")

    subdirs = []
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")):
        target = index_page(docs, sub)
        if target is None:
            continue  # nothing in it MkDocs would publish
        sub_rel = sub.relative_to(docs).as_posix()
        latest = max((d for p, d in days.items() if p.startswith(sub_rel + "/")), default="")
        subdirs.append((latest, sub, target))
    notes = [
        (newest(f"{rel}{f.name}"), f)
        for f in folder.iterdir()
        if f.is_file()
        and f.suffix == ".md"
        and f.name not in ("index.md", "README.md", "summary.md")
    ]

    def row(title: str, link: str, day: str) -> str:
        return item(title, link, day_label(day, this_year, weekday=False) if day else "")

    if subdirs:
        lines += ["## Folders", ""]
        for day, sub, target in sorted(subdirs, key=lambda s: (s[0], s[1].name), reverse=True):
            lines.append(row(dir_title(sub.name), target.relative_to(folder).as_posix(), day))
        lines.append("")
    if notes:
        lines += ["## Notes", ""]
        for day, f in sorted(notes, key=lambda n: (n[0], n[1].name), reverse=True):
            lines.append(row(note_title(f), f.name, day))
        lines.append("")
    return lines


def folder_pages(docs: Path, days: dict[str, str], this_year: int) -> None:
    """An index.md for each folder that holds notes, deepest first so a
    parent can link to the page just made for its child."""
    folders = [
        p
        for p in docs.rglob("*")
        if p.is_dir() and not any(s.startswith(".") for s in p.relative_to(docs).parts)
    ]
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        if index_page(docs, folder) is not None:
            continue
        has_notes = any(f.suffix == ".md" for f in folder.iterdir() if f.is_file())
        has_pages = any(index_page(docs, s) for s in folder.iterdir() if s.is_dir())
        if has_notes or has_pages:
            (folder / "index.md").write_text(
                folder_page(docs, folder, days, this_year), encoding="utf-8"
            )


def active_projects(
    docs: Path, changes: list[tuple[str, str]], head_day: str, this_year: int
) -> list[str]:
    since = (dt.date.fromisoformat(head_day) - dt.timedelta(days=ACTIVE_DAYS - 1)).isoformat()
    seen: dict[str, tuple[str, set[str]]] = {}
    for day, path in changes:
        parts = path.split("/")
        if day < since or len(parts) < 3 or parts[0] != "projects":
            continue
        project = "/".join(parts[:2])
        if not (docs / path).is_file():
            continue
        seen.setdefault(project, (day, set()))[1].add(path)
    rows = []
    for project, (day, notes) in sorted(
        seen.items(), key=lambda kv: (kv[1][0], kv[0]), reverse=True
    ):
        target = index_page(docs, docs / project)
        if target is None:
            continue
        plural = "s" if len(notes) != 1 else ""
        when = day_label(day, this_year, weekday=False)
        row = item(
            dir_title(project.split("/")[1]),
            target.relative_to(docs).as_posix(),
            f"{len(notes)} note{plural} changed · {when}",
        )
        summary = docs / project / "summary.md"
        if summary.is_file():
            text = gist(split(summary.read_text(encoding="utf-8", errors="replace"))[1])
            if text:
                row += f'<br><span class="brain-gist">{html.escape(text)}</span>'
        rows.append(row)
    return rows


def landing_page(docs: Path, changes: list[tuple[str, str]], sha: str, built: str) -> str:
    head_day = changes[0][0] if changes else built[:10]
    this_year = dt.date.fromisoformat(head_day).year
    lines = ["# Brain", "", f"Built {built} from commit `{sha[:12]}`.", ""]

    active = active_projects(docs, changes, head_day, this_year)
    if active:
        lines += ["## Active this week", "", *active, ""]

    lines += ["## Recently changed", ""]
    shown, day_shown = 0, None
    for path, day in last_changed(changes).items():
        if not (docs / path).is_file():
            continue  # since deleted or moved
        if day != day_shown:
            lines += [*([""] if lines[-1] else []), f"### {day_label(day, this_year)}", ""]
            day_shown = day
        folder = path.rsplit("/", 1)[0] if "/" in path else "top level"
        lines.append(item(note_title(docs / path), path, folder))
        shown += 1
        if shown == RECENT:
            break
    lines.append("")

    # The tabs name only the folders in nav.yml; this reaches the rest.
    lines += ["## Everything", ""]
    everything = contents(docs, docs, last_changed(changes), this_year)
    lines += ["#" + line if line.startswith("## ") else line for line in everything]
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    docs, root, sha, built = Path(argv[1]), Path(argv[2]), argv[3], argv[4]
    changes = history(root, sha)
    days = last_changed(changes)
    this_year = dt.date.fromisoformat(changes[0][0] if changes else built[:10]).year
    own_landing = (docs / "index.md").exists()
    # Folder pages first: the landing page links each active project to one.
    folder_pages(docs, days, this_year)
    if not own_landing:
        (docs / "index.md").write_text(landing_page(docs, changes, sha, built), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
