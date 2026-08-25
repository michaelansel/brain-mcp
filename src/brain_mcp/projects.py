"""Project tools: projects / project / project_create.

Registered onto the same FastMCP app as the filesystem tools. A future
delegated-interrogation module (`ask`) would drop in beside this one.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from brain_mcp import frontmatter, summaries
from brain_mcp.attention import Attention
from brain_mcp.config import BrainConfig
from brain_mcp.errors import ToolError, git_guard
from brain_mcp.gitio import commit_message, commit_paths, head_sha, run_git, write_lock
from brain_mcp.pathsafe import confine, rel

PROJECTS_DIR = "projects"

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
# Purely additive: errors if the project already exists rather than
# overwriting it, and a repeated call with the same name just errors again.
ADDITIVE_ONLY = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


def register(mcp: FastMCP, config: BrainConfig, attention: Attention) -> None:
    root = config.root

    @mcp.tool(annotations=READ_ONLY)
    async def projects(summaries_too: bool = False) -> list[dict[str, Any]]:
        """Every project: name, status, path, repo count. Reads frontmatter
        only, so it stays fast with hundreds of projects.

        `summaries_too` adds, per project, how far its summary has fallen
        behind and how much attention it has had — the list of what is worth
        refreshing, in one call. It costs a couple of git calls per project,
        which is why it is opt-in. Surveying this way records no reads: it
        names no single folder anyone is orienting on, and counting it would
        make everything look freshly attended."""
        pdir = root / PROJECTS_DIR
        if not pdir.is_dir():
            return []
        out = []
        for entry in sorted(pdir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name == ".git":
                continue
            meta = frontmatter.read_head(entry / "project.md") or {}
            repos = meta.get("repos")
            out.append(
                {
                    "name": str(meta.get("name", entry.name)),
                    "status": str(meta.get("status", "unknown")),
                    "path": f"{PROJECTS_DIR}/{entry.name}",
                    "repo_count": len(repos) if isinstance(repos, list) else 0,
                }
            )
        if summaries_too:
            counts = await attention.counts([p["path"] for p in out])
            for item in out:
                item.update(counts.get(item["path"], {"reads": 0}))
                item.update(await _summary_fields(root, item["path"], config))
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def project(name: str) -> dict[str, Any]:
        """One project in full: frontmatter, project.md prose, a recursive
        file listing, and the state of its summary.

        project.md says what the project is *for*; summary.md says what its
        notes currently amount to. This reports whether that summary has
        fallen behind, or that none has been written yet."""
        pdir = _project_dir(root, name)
        if not pdir.is_dir():
            available = ", ".join(p.name for p in sorted((root / PROJECTS_DIR).glob("*/")))
            raise ToolError(f"no such project '{name}'; projects present: {available or '(none)'}")
        manifest = pdir / "project.md"
        if not manifest.is_file():
            raise ToolError(f"project '{name}' has no project.md manifest")
        meta, body = frontmatter.split(manifest.read_text(encoding="utf-8"))
        if meta is None:
            raise ToolError(
                f"project.md for '{name}' has missing or malformed YAML frontmatter; "
                f"read {PROJECTS_DIR}/{name}/project.md to inspect it"
            )
        files = []
        for dirpath, dirnames, filenames in os.walk(pdir):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            files.extend((Path(dirpath) / f).relative_to(root).as_posix() for f in filenames)
        folder = f"{PROJECTS_DIR}/{name}"
        out: dict[str, Any] = {
            "name": name,
            "path": folder,
            "frontmatter": meta,
            "prose": body.strip(),
            "files": sorted(files),
        }
        await attention.record(folder, "project")
        out.update(await _summary_fields(root, folder, config))
        return out

    @mcp.tool(annotations=ADDITIVE_ONLY)
    @git_guard
    async def project_create(
        name: str, summary: str, from_path: str | None = None
    ) -> dict[str, Any]:
        """Scaffold projects/<name>/project.md. If from_path names a note
        (e.g. in inbox/), move it into the new project in the same commit."""
        pdir = _project_dir(root, name)
        manifest = pdir / "project.md"
        if manifest.exists():
            raise ToolError(f"project '{name}' already exists at {PROJECTS_DIR}/{name}")

        note = None
        if from_path is not None:
            note = confine(root, from_path)
            if not note.is_file():
                raise ToolError(f"from_path '{from_path}' is not a file in the brain")
            if note == manifest:
                raise ToolError("from_path cannot be the project.md being created")

        content = (
            "---\n"
            f"name: {name}\n"
            "status: active\n"
            f"created: {date.today().isoformat()}\n"
            "repos: []\n"
            "---\n"
            "\n"
            f"# {name}\n"
            "\n"
            f"{summary.strip()}\n"
        )
        r_manifest = rel(root, manifest)
        commit_targets = [r_manifest]
        async with write_lock:
            pdir.mkdir(parents=True, exist_ok=True)
            manifest.write_text(content, encoding="utf-8")
            await run_git(root, "add", "--", r_manifest)
            if note is not None:
                r_note = rel(root, note)
                r_dest = rel(root, pdir / note.name)
                await run_git(root, "mv", "-f", "--", r_note, r_dest)
                commit_targets += [r_note, r_dest]
            msg = commit_message("project_create", f"{PROJECTS_DIR}/{name}")
            sha = await commit_paths(root, commit_targets, msg)
        return {"path": f"{PROJECTS_DIR}/{name}", "commit": sha, "message": msg.splitlines()[0]}


async def _summary_fields(root: Path, folder: str, config: BrainConfig) -> dict[str, Any]:
    """What to say about a folder's summary: how far behind it is, or that
    there is none."""
    if not (root / folder / summaries.SUMMARY_NAME).is_file():
        # Not scaffolded at creation — an empty project has nothing to
        # summarise, and agents.md says not to scaffold empties. Say so here
        # instead, where someone is orienting on the project, and hand over
        # the sha to claim: otherwise the first summary written has no way to
        # know what it covers and reports itself stale the moment it lands.
        return {
            "summary": {
                "state": "none written",
                "write": f"{folder}/{summaries.SUMMARY_NAME}",
                "covers": await head_sha(root) or "",
                "how": (
                    "If this project has notes worth orienting from, write that "
                    "file as a state note covering what they amount to, with the "
                    "`covers:` sha above in its frontmatter."
                ),
            }
        }
    stale = await summaries.state(root, folder, config)
    return {"summary_stale": stale} if stale is not None else {}


def _project_dir(root: Path, name: str) -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ToolError(
            f"invalid project name '{name}'; use a plain directory name without slashes"
        )
    return confine(root, f"{PROJECTS_DIR}/{name}")
