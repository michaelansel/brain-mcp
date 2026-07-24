# brain-mcp

An MCP server that exposes a git-backed folder of markdown — "the brain" — to
AI surfaces: Claude web/mobile, Claude Code, and local agents. The brain is the
single durable memory shared across every surface where you think or act, and
this server is its only write path.

It feels like a filesystem: a small, fixed, orthogonal tool set an agent can
navigate with the same instincts it uses for local files. Every mutation is one
git commit.

## Configure

Environment variables:

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `BRAIN_ROOT` | yes | — | Path to the brain. Must exist and be the top level of a git repository; the server fails loudly at startup otherwise. |
| `BRAIN_MCP_TOKEN` | no | (auth disabled) | Static bearer token for the HTTP transport. stdio ignores it. |
| `BRAIN_MCP_HOST` | no | `127.0.0.1` | HTTP bind host. |
| `BRAIN_MCP_PORT` | no | `8000` | HTTP bind port. |
| `BRAIN_MCP_TRANSPORT` | no | `stdio` | `stdio` or `http`; the `--transport` flag overrides it. |

## Run

```sh
uv sync

# stdio (Claude Code, local agents)
BRAIN_ROOT=~/brain uv run brain-mcp

# Streamable HTTP (remote enrollment)
BRAIN_ROOT=~/brain BRAIN_MCP_TOKEN=secret uv run brain-mcp --transport http --port 8000
```

Claude Code registration:

```sh
claude mcp add brain -e BRAIN_ROOT=~/brain -- uv --directory /path/to/brain-mcp run brain-mcp
```

One-command demo (scratch brain, stdio server, main flow):

```sh
scripts/demo.sh
```

## Tools

**Read**

- `list(path="")` — one directory level. Entries: `name`, `path`, `type` (`file`|`dir`), `size` (bytes; 0 for dirs), `modified` (ISO 8601 UTC).
- `read(path)` — file contents (UTF-8).
- `search(query, path="", limit=50, regex=False)` — ripgrep across the brain (pure-Python fallback when `rg` is absent). Hits: `path`, `line`, `text`. Literal by default.
- `glob(pattern)` — paths matching a glob, e.g. `projects/**/*.md`.

**Write** — each call commits and returns `{"path", "commit", "message"}`.

- `write(path, content)` — create or overwrite; creates parent dirs.
- `patch(path, section, content)` — replace one markdown section: the first heading (any level) whose text matches `section` exactly, through to the next heading of the same or higher level. Missing section is an error that lists the sections present.
- `move(src, dst)` — `git mv`; creates parent dirs.
- `delete(path)` — remove a file, or a directory recursively.

**Projects**

- `projects()` — every project with `name`, `status`, `path`, `repo_count`. Reads only frontmatter, so it stays fast with hundreds of projects.
- `project(name)` — frontmatter, `project.md` prose, and a recursive file listing.
- `project_create(name, summary, from_path=None)` — scaffold `projects/<name>/project.md`; with `from_path` (a note in `inbox/`), the note moves into the new project **in the same commit**.

Commit message format, staged paths only, never `git add -A`:

```
<tool>: <path>

via brain-mcp
```

## Containment guarantees

- **Path confinement.** Every path is relative to `BRAIN_ROOT`. Paths are resolved — including symlinks — and verified to land inside the root. Rejected: absolute paths, `..` escapes, `~`, backslashes, NUL bytes, percent-encoded traversal variants, symlinks pointing outside the brain, and anything under `.git`.
- **No subprocesses beyond the brain.** `git` and `rg` are the only subprocesses, both rooted at the brain. The server holds no credentials and opens no other repos.
- **One commit per mutating call**, restricted by pathspec to the affected files. Unrelated dirty or staged files in the brain are left exactly as they were.
- **Serialized writes.** A single async lock wraps every mutate-and-commit; concurrent writes produce distinct, non-interleaved commits.
- **No caching.** Every call reads current state from disk, so hand edits to the brain are always visible immediately.
- **Auth seam.** HTTP auth is a static bearer token behind one small module (`auth.py`); a FastMCP `OAuthProxy` can replace it without touching tool code.

## Decisions (where the spec left room)

- `move` commit messages use `move: <src> -> <dst>` since two paths are involved.
- A no-op `write`/`patch` (content unchanged) returns the current HEAD sha with a "no change" message instead of erroring or creating an empty commit.
- `move` onto an existing file overwrites it (like `mv`); `delete` on a directory removes it recursively.
- Duplicate section headings: `patch` replaces the first match. Headings inside fenced code blocks are ignored.
- `search` results are sorted by `(path, line)` and capped at `limit`; ripgrep runs with `--hidden --no-ignore` (minus `.git`) so the fallback and rg see the same files.
- A `project.md` with missing or malformed frontmatter still appears in `projects()` with `status: "unknown"`; `project()` on it raises an actionable error.
- Project names must be plain directory names (no `/`, no leading `.`).
- Deleting an untracked (never-committed) file is an error rather than a silent `rm` — the file is left alone.

## Development

```sh
uv run pytest          # full suite, including adversarial path tests
uv run ruff check .
uv run ruff format .
```

Out of scope by design: OAuth, deployment, backlinks/tags/indexes beyond
ripgrep, filesystem watching, and delegated interrogation (`ask()`) — the
tool-module layout leaves a clean seam for that last one.
