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
| `BRAIN_MCP_TOKEN` | no | (auth disabled) | Static bearer token for the HTTP transport. stdio ignores it. Mutually exclusive with `BRAIN_MCP_EXEDEV_EMAILS`. |
| `BRAIN_MCP_EXEDEV_EMAILS` | no | (off) | Comma-separated email allowlist for deployment behind a **private** exe.dev HTTPS proxy (see below). Mutually exclusive with `BRAIN_MCP_TOKEN`. |
| `BRAIN_MCP_HOST` | no | `127.0.0.1` | HTTP bind host. |
| `BRAIN_MCP_PORT` | no | `8000` | HTTP bind port. |
| `BRAIN_MCP_TRANSPORT` | no | `stdio` | `stdio` or `http`; the `--transport` flag overrides it. |
| `BRAIN_MCP_GIT_REMOTE` | no | (push disabled) | Git remote name or URL to push `HEAD` to after every commit, best-effort and non-blocking. Auth (SSH keys/config) must already work for `git` on this host outside of brain-mcp. |
| `BRAIN_MCP_GIT_PUSH` | no | `true` | Set `false` to pause pushing without unsetting `BRAIN_MCP_GIT_REMOTE`. |
| `BRAIN_MCP_READ_BEFORE_WRITE` | no | `true` | Require a prior `read` before `edit`, or before `write` overwrites an existing file (see "Read before write"). Set `false` to disable the gate. |

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

### Deploying behind exe.dev

On an exe.dev VM, the platform's private HTTPS proxy is the auth layer: the
edge authenticates every request (browser login, or [signed VM
tokens](https://exe.dev/docs/https-tokens-for-vms) for API clients like
Claude) before it reaches the VM, then injects the caller's identity as
`X-ExeDev-Email`. Run brain-mcp with the email allowlist instead of a token:

```sh
BRAIN_ROOT=~/brain BRAIN_MCP_EXEDEV_EMAILS=you@example.com \
  uv run brain-mcp --transport http   # binds 127.0.0.1:8000
ssh exe.dev share port <vm> 8000      # point the proxy at it; keep it private
ssh exe.dev ssh-key generate-api-key --vm=<vm> --label=claude  # token for MCP clients
```

MCP clients send the exe.dev token as `Authorization: Bearer <token>` (or
`X-Exedev-Authorization` where custom headers are possible). brain-mcp then
enforces its own layer — exactly one `X-ExeDev-Email` header, value on the
allowlist — so a request must pass both the edge's signature check and the
in-app identity check, with no secret stored anywhere on the VM. It fails
closed: if the proxy is ever flipped public, the header disappears and every
request is denied.

Before trusting it, verify once from outside that a client-supplied
`X-ExeDev-Email` header is stripped by the proxy:

```sh
curl -i https://<vm>.exe.xyz/mcp -X POST \
  -H "X-Exedev-Authorization: Bearer <token>" \
  -H "X-ExeDev-Email: someone-else@example.com" \
  -d '{}'   # must NOT be treated as someone-else@example.com
```

### OAuth for Claude.ai's remote connector

Claude Code (above) works with a manually-configured bearer token. Claude.ai's
web/mobile **Connectors** UI only knows how to add a remote MCP server via
OAuth 2.1 + PKCE + dynamic client registration — it has no field for a manual
token. `gateway/` is a small, separate service that speaks that OAuth flow on
brain-mcp's behalf: it delegates login to GitHub, and instead of issuing its
own signed tokens, calls exe.dev's own API to mint a real exe.dev VM-scoped
bearer token, which becomes the OAuth access token. brain-mcp itself has
**zero code or config changes** for this — it keeps trusting `X-ExeDev-Email`
exactly as described above, on the port it always has.

The only operational change is that brain-mcp now binds
`127.0.0.1:4001` behind a small nginx instance instead of being the thing
`share port` points at directly — nginx is the only thing exe.dev's edge talks
to, and it only forwards `/mcp` once exe.dev has already verified the caller.
See `gateway/README.md` for setup and the ops runbook (including the one
manual step required roughly once a year, when the minted token expires).

## Tools

Every tool carries MCP's standard annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`) — clients that group or gate tools by
read/write (Claude.ai's connector permissions UI, for one) use these rather
than needing hardcoded tool-name lists. `read`/`list`/`search`/`glob`/`projects`/
`project` are read-only; `write`/`edit`/`patch`/`move`/`delete`/`project_create`
are not, and are further marked destructive except `project_create`, which only
ever adds (it errors rather than overwriting an existing project).

**Read**

- `list(path="")` — one directory level. Entries: `name`, `path`, `type` (`file`|`dir`), `size` (bytes; 0 for dirs), `modified` (ISO 8601 UTC).
- `read(path)` — file contents (UTF-8).
- `search(query, path="", limit=50, regex=False)` — ripgrep across the brain (pure-Python fallback when `rg` is absent). Hits: `path`, `line`, `text`. Literal by default.
- `glob(pattern)` — paths matching a glob, e.g. `projects/**/*.md`.

**Write** — each call commits and returns `{"path", "commit", "message"}`.

- `write(path, content)` — create or overwrite; creates parent dirs.
- `edit(path, old_string, new_string, replace_all=False)` — replace exact text. `old_string` must match the file byte-for-byte, including whitespace and indentation, and must be unique unless `replace_all` is set. Also returns `replacements`.
- `patch(path, section, content)` — replace one markdown section: the first heading (any level) whose text matches `section` exactly, through to the next heading of the same or higher level. Missing section is an error that lists the sections present.
- `move(src, dst)` — `git mv`; creates parent dirs.
- `delete(path)` — remove a file, or a directory recursively.

**Projects**

- `projects()` — every project with `name`, `status`, `path`, `repo_count`. Reads only frontmatter, so it stays fast with hundreds of projects.
- `project(name)` — frontmatter, `project.md` prose, and a recursive file listing.
- `project_create(name, summary, from_path=None)` — scaffold `projects/<name>/project.md`; with `from_path` (a note in `inbox/`), the note moves into the new project **in the same commit**.

### Read before write

The user edits the brain by hand in their own editor while agents are working
in it, so an agent writing from a stale mental model can silently destroy an
uncommitted hand edit. Changing an existing file therefore requires having
looked at it:

- `edit`, and `write` when the target already exists, both require that **this
  session** has `read` that path, and that the file still hashes to what the
  session saw. Two failure modes, both actionable in one call: *read it first*,
  or *it changed on disk since you read it — read it again*.
- Creating a new file, `move`, `delete`, and `project_create` are ungated —
  creation can't clobber, and the others name a whole path explicitly and leave
  the content in git.
- A successful `write`/`edit` records what it just wrote, so `read → edit →
  edit → write` needs only the one read.
- `move` and `delete` invalidate the paths they touch, for every session.

The ledger is per-session and process-local: a restart clears it, costing one
extra `read` before the next write in each surviving session. It assumes a
single stateful server process — **do not** run uvicorn with `--workers` or
enable `stateless_http`, or a fresh session id per request would make the gate
unsatisfiable. `BRAIN_MCP_READ_BEFORE_WRITE=false` is the one-restart escape
hatch.

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
- **No caching.** Every call reads current state from disk, so hand edits to the brain are always visible immediately. The read-before-write ledger is not an exception: it stores content hashes only, never content, and nothing in it is ever served to a caller.
- **Read before write.** Changing an existing file requires that the calling session read it first and that it still matches what that session saw — so an agent can't overwrite a hand edit it never looked at. Per-session, process-local, LRU-bounded at 64 sessions × 256 paths. Fails closed on a missing read (actionable: read and retry) and open on a missing session identity (unenforceable, so it logs a warning instead of bricking writes).
- **Auth seam.** All HTTP auth lives in one small module (`auth.py`) with two modes: a static bearer token, or an exe.dev identity-header allowlist for deployment behind exe.dev's private proxy. A FastMCP `OAuthProxy`/`JWTVerifier` can replace either without touching tool code. Responses never carry a server banner. For Claude.ai's OAuth-requiring connector, `gateway/` bridges the two rather than adding OAuth to this module — see "OAuth for Claude.ai's remote connector" above.
- **Push is best-effort.** With `BRAIN_MCP_GIT_REMOTE` set, every commit kicks off a background `git push` of `HEAD` — it never blocks or fails a tool call. Pushes are serialized by their own lock (separate from the write lock), so they never race each other or land out of commit order. Failures are logged, not surfaced to the caller, and self-heal: the next mutating call always pushes current `HEAD` again, so a transient outage catches up on its own.

## Decisions (where the spec left room)

- `move` commit messages use `move: <src> -> <dst>` since two paths are involved.
- A no-op `write`/`patch`/`edit` (content unchanged) returns the current HEAD sha with a "no change" message instead of erroring or creating an empty commit.
- A byte-identical `write` over an existing file is still refused without a prior read: you can't know it's a no-op without looking.
- `edit` is not idempotent — replaying it fails because `old_string` is gone — so it carries `idempotentHint: false` alongside `move`/`delete`.
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
