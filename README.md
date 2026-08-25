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
| `BRAIN_MCP_SUMMARY_MIN_FILES` | no | `3` | Changed files that make a folder summary worth rewriting (see "Folder summaries"). |
| `BRAIN_MCP_SUMMARY_MAX_AGE_DAYS` | no | `30` | Summary age that makes a rewrite worth asking for on its own. |
| `BRAIN_MCP_SUMMARY_QUIET_MINUTES` | no | `60` | How recent a run of commits has to be to count as work still in progress, and so not worth interrupting. |
| `BRAIN_MCP_STATE_DIR` | no | `$XDG_STATE_HOME/brain-mcp` | Where read tracking lives. Outside the brain by design (see "Folder summaries"). |
| `BRAIN_MCP_ATTENTION_DAYS` | no | `90` | How long a read event is kept before pruning. |

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
`project` are read-only; `write`/`edit`/`move`/`delete`/`project_create`
are not, and are further marked destructive except `project_create`, which only
ever adds (it errors rather than overwriting an existing project).

**Read**

- `list(path="")` — one directory level. Entries: `name`, `path`, `type` (`file`|`dir`), `size` (bytes; 0 for dirs), `modified` (ISO 8601 UTC).
- `read(path)` — `{"content", "hash"}`: the file's UTF-8 contents and their sha256. Pass the hash back as `base_hash` to `write`/`edit`. Reading a `summary.md` may also carry `summary_stale` (see "Folder summaries").
- `search(query, path="", limit=50, regex=False)` — ripgrep across the brain (pure-Python fallback when `rg` is absent). Hits: `path`, `line`, `text`. Literal by default.
- `glob(pattern)` — paths matching a glob, e.g. `projects/**/*.md`.

**Write** — each call commits and returns `{"path", "commit", "message"}`.

- `write(path, content, base_hash=None)` — create or overwrite; creates parent dirs. Overwriting an existing file requires `base_hash`.
- `edit(path, old_string, new_string, replace_all=False, base_hash=None)` — replace exact text. `old_string` must match the file byte-for-byte, including whitespace and indentation, and must be unique unless `replace_all` is set. `base_hash` is optional here and verified when given. Also returns `replacements`.
- `move(src, dst)` — `git mv`; creates parent dirs. A source directory left empty by the move is removed, and its parents while they are empty too.
- `delete(path)` — remove a file, or a directory recursively. A parent directory left empty by the delete is removed too. Naming an empty directory removes just that directory, without a commit — git cannot track one, so there is nothing to commit.

**Projects**

- `projects(summaries_too=False)` — every project with `name`, `status`, `path`, `repo_count`. Reads only frontmatter, so it stays fast with hundreds of projects. `summaries_too` adds per-project summary staleness and read counts — the list of what is worth refreshing, in one call — at a couple of git calls per project, which is why it is opt-in.
- `project(name)` — frontmatter, `project.md` prose, and a recursive file listing. Also `summary_stale` if the project's summary has fallen behind, or `summary` explaining none has been written.
- `project_create(name, summary, from_path=None)` — scaffold `projects/<name>/project.md`; with `from_path` (a note in `inbox/`), the note moves into the new project **in the same commit**.

### Folder summaries

A folder is summarised iff it contains `summary.md`. Presence is the opt-in —
the server special-cases no directory, so `projects/` gets summaries because
convention puts them there, and anything else gets one the moment someone
writes one.

`project.md` says what a project is *for*; `summary.md` is a state note (in the
`agents.md` sense: carries `surveyed:`, goes stale, believed only as far as its
date) about what the notes beside it currently amount to.

#### What a summary covers

A summary states how much it accounts for, in a `covers:` line naming a commit:

```yaml
---
title: rocket
surveyed: 2026-08-25       # the human line, per the state-note convention
source: projects/rocket
covers: fb9ced4a…          # what this summary accounts for
---
```

That claim, not the file's own history, is what freshness is measured from — so
**editing the file and asserting coverage are separate acts**. Without this,
writing the summary at all advanced the baseline to HEAD and marked every
pending change integrated, whether it had been read or not: an agent that
skimmed one relevant note and reworded a paragraph silently absorbed the rest,
and a human fixing a typo absorbed everything. Partial work was lossy. Now it is
merely incomplete — a refresher that did not do the whole job leaves `covers:`
alone, and the drift stays flagged for whoever comes next. That is also why
there is no partial-credit mechanism: either you accounted for all of it and say
so, or you don't touch the line.

A summary that states no `covers:`, or names a commit that is not behind HEAD,
**accounts for nothing** — every note beside it is reported as unaccounted-for
until a refresh records a claim. There is no fallback to "the last commit that
touched this file", because that is exactly the lossy behaviour above.

The agent never derives the sha: the refresh request hands it current HEAD to
record, the same shape as `base_hash`. Every way of getting it wrong fails safe
— a hallucinated sha does not resolve, an old one does not advance coverage, and
a sha newer than what was integrated cannot be produced, since the only
resolvable shas an agent holds are the one it was given or older. The failure
mode is always "stays stale", never "falsely fresh".

The delta covers both commits made since and edits sitting uncommitted in the
working tree — only this server commits, so a hand edit can otherwise sit
unnoticed indefinitely.

#### What a read reports

```jsonc
"summary_stale": {
  "since": "2026-08-01",
  "changed": ["M projects/rocket/fuel.md", "A projects/rocket/engine.md"],
  "coverage": "…why this summary's claim could not be used…",  // only when abnormal
  "refresh": "…account for all of them, set covers: <sha>…"     // conditional
}
```

The two halves are deliberately separate. `changed` is **information** and is
always present once the summary is behind — an agent should know it is reading
something N notes out of date. `refresh` is a **judgement call**, included only
when the drift is worth interrupting for: a note was added or deleted (the
folder's contents changed, not just wording), or `BRAIN_MCP_SUMMARY_MIN_FILES`
files changed, or the summary is older than `BRAIN_MCP_SUMMARY_MAX_AGE_DAYS`.

It is suppressed when every commit in the delta lands inside
`BRAIN_MCP_SUMMARY_QUIET_MINUTES` — someone is mid-burst in that folder and
already knows what they changed. Uncommitted changes never suppress: every
agent write commits, so an uncommitted edit is a hand edit, which is exactly the
drift worth surfacing.

The request asks for the work to be handed to a subagent where one is
available. The alternative — inlining the changed notes into the response — is
backwards once you have delegation: it spends the context of the agent that is
about to hand the job off. The subagent reads the notes itself, in its own
budget.

Nothing here calls a model. The server hands over the delta and the ask; the
agent already in the loop does the work and writes the result back with
`base_hash`. Ignoring the request breaks nothing — the summary simply stays
stale and says so.

#### Read tracking

A summary read every other day and stale matters more than one nobody has
opened. git records every write to the brain and cannot see a single read, so
that signal exists nowhere else — which is the whole reason this store exists.

It is a sqlite event log, one row per read, pruned past
`BRAIN_MCP_ATTENTION_DAYS`. Keeping only a last-read-per-folder would mean
deciding at write time which reads are interesting, baked into a place it could
never be revisited; with the events kept, frequency is a signal and later
questions need no migration. `read` of a summary and `project(name)` each record
one; `projects()` records nothing, because it names no folder anyone is
orienting on and counting it would make everything look freshly attended.

It lives **outside the brain** on purpose: anything written inside would be a
commit, a push, and noise in the very activity count the weekly review uses to
notice when it has become the only thing still writing. This is a deliberate,
bounded exception to "the server never caches" — that rule protects brain
content, and no brain content is cached here. Nothing in it raises: losing a
read event costs a little signal, while failing a `read` would cost the brain.

### Read before write

The user edits the brain by hand in their own editor while agents are working
in it, so an agent writing from a stale mental model can silently destroy an
uncommitted hand edit. Changing an existing file therefore requires proving you
have seen its current contents.

The proof travels **with the request**, not in server memory. `read` returns the
content and its sha256; `write` and `edit` take that back as `base_hash` and
compare it against what is on disk right now, inside the write lock:

- `write` over an existing file **requires** `base_hash`. Two failure modes,
  both actionable in one call: *read it first and pass the hash*, or *it changed
  on disk since you read it — read it again*.
- `edit` takes `base_hash` **optionally**. A byte-exact `old_string` is already
  proof the caller saw the current text — an agent working from a stale copy
  cannot produce one — so the hash is not required. Supplied, it additionally
  catches a change *elsewhere* in the file, which `old_string` alone cannot see.
- Creating a new file, `move`, `delete`, and `project_create` need no hash —
  creation can't clobber, and the others name a whole path explicitly and leave
  the content in git.

Because the server stores nothing between calls, this survives client
reconnects, server restarts, and clients that open a **fresh MCP session per
tool call** — which Claude.ai's connector does, and which no session-keyed
scheme can satisfy. It also means uvicorn `--workers` and `stateless_http` are
safe. `BRAIN_MCP_READ_BEFORE_WRITE=false` is the one-restart escape hatch.

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
- **No state between calls.** Every call reads current state from disk, so hand edits to the brain are always visible immediately. The server keeps nothing at all between requests — no cache, and no ledger of who read what.
- **Read before write.** Overwriting an existing file requires `base_hash`, the sha256 `read` returned, and it must still match what is on disk — so an agent can't overwrite a hand edit it never looked at. `edit` accepts it optionally, since a byte-exact `old_string` already carries the same proof. Fails closed on a missing or stale hash, both actionable in one call.
- **Auth seam.** All HTTP auth lives in one small module (`auth.py`) with two modes: a static bearer token, or an exe.dev identity-header allowlist for deployment behind exe.dev's private proxy. A FastMCP `OAuthProxy`/`JWTVerifier` can replace either without touching tool code. Responses never carry a server banner. For Claude.ai's OAuth-requiring connector, `gateway/` bridges the two rather than adding OAuth to this module — see "OAuth for Claude.ai's remote connector" above.
- **Push is best-effort.** With `BRAIN_MCP_GIT_REMOTE` set, every commit kicks off a background `git push` of `HEAD` — it never blocks or fails a tool call. Pushes are serialized by their own lock (separate from the write lock), so they never race each other or land out of commit order. Failures are logged, not surfaced to the caller, and self-heal: the next mutating call always pushes current `HEAD` again, so a transient outage catches up on its own.

## Decisions (where the spec left room)

- `move` commit messages use `move: <src> -> <dst>` since two paths are involved.
- A no-op `write`/`edit` (content unchanged) returns the current HEAD sha with a "no change" message instead of erroring or creating an empty commit.
- A byte-identical `write` over an existing file is still refused without `base_hash`: you can't know it's a no-op without looking.
- `edit` does not require `base_hash` while `write` does. `old_string` must match byte-for-byte, so it already proves the caller saw the current text; `write` replaces the whole file and carries no such proof. Requiring it on `edit` would add friction without adding a guarantee.
- `edit` is not idempotent — replaying it fails because `old_string` is gone — so it carries `idempotentHint: false` alongside `move`/`delete`.
- `move` onto an existing file overwrites it (like `mv`); `delete` on a directory removes it recursively.
- `search` results are sorted by `(path, line)` and capped at `limit`; ripgrep runs with `--hidden --no-ignore` (minus `.git`) so the fallback and rg see the same files.
- A `project.md` with missing or malformed frontmatter still appears in `projects()` with `status: "unknown"`; `project()` on it raises an actionable error.
- Project names must be plain directory names (no `/`, no leading `.`).
- Deleting an untracked (never-committed) file is an error rather than a silent `rm` — the file is left alone. An empty *directory* is the exception: git cannot track one, so there is nothing to lose and nothing to commit.
- Pruning is `rmdir` only, and the walk stops the moment `rmdir` refuses — a directory holding anything at all ends it. No file, and no directory holding a file, can be lost to it however far up the chain it runs.
- Pruning stops before the top level: a direct child of the root is never removed automatically. Those are the brain's skeleton, and reaping one the moment it empties would make deleting a single note remove `inbox/`. It is a rule about depth, so no directory is special-cased by name; removing one deliberately still works by naming it.
- Folder summaries add no tools: `read` and `project()` gained response fields instead. The access path for a summary is the path it lives at.
- `project_create` does **not** scaffold a `summary.md` — a brand-new project has nothing to summarise, and `agents.md` says not to scaffold empties. `project()` reports the absence instead, where someone is orienting on the project anyway.
- A `summary.md` that exists but was never committed reports nothing: there is no point in history to measure drift from, and whoever just wrote it has read the folder.

## Development

```sh
uv run pytest          # full suite, including adversarial path tests
uv run ruff check .
uv run ruff format .
```

Out of scope by design: OAuth, deployment, backlinks/tags/indexes beyond
ripgrep, filesystem watching, and delegated interrogation (`ask()`) — the
tool-module layout leaves a clean seam for that last one. The server never
calls a model: work that needs judgement is handed to the agent already in
the loop, which is what keeps the containment guarantees above true.
