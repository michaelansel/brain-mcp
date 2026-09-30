# oauth-gateway

A small OAuth 2.1 + PKCE + dynamic client registration (DCR) authorization
server that lets Claude.ai's remote **Connectors** UI add brain-mcp — which
otherwise only supports a manually-configured bearer token.

It does not run its own identity system: login is delegated to GitHub, and
instead of minting and verifying its own signed tokens, it calls exe.dev's own
API to mint a real exe.dev VM-scoped bearer token, which becomes the OAuth
access token handed back to Claude. brain-mcp itself needs no changes — it
keeps trusting `X-ExeDev-Email` exactly as it always has.

See `../README.md`'s "OAuth for Claude.ai's remote connector" section for how
this fits into the overall deployment, and the architecture/rationale in the
project's plan doc for why it's built this way.

## How a connection happens

1. Claude.ai registers a client (`POST /register`), then sends the user's
   browser to `/authorize`.
2. `/authorize` validates the request and redirects the browser to GitHub's
   own login page.
3. GitHub redirects back to `/auth/github/callback`. If the authenticated
   GitHub account's immutable numeric id is on `GATEWAY_ALLOWED_GITHUB_IDS`,
   the gateway issues a single-use authorization code and redirects back to
   the client — but only to a `redirect_uri` matching
   `GATEWAY_ALLOWED_REDIRECT_URIS` exactly (checked at registration time in
   `/register`, not here — see "Security review" below for why this matters).
4. Claude's backend exchanges that code at `/token`. The gateway verifies PKCE,
   then calls `exe.dev`'s API to mint a VM-scoped bearer token valid for
   `GATEWAY_VM_TOKEN_TTL_SECONDS` (default: 1 year), and returns it as the
   OAuth `access_token`.
5. From then on, Claude calls `/mcp` directly with that token. There is no
   refresh grant — when the token eventually expires, redo this flow (see
   "Reconnecting" below).

## One-time setup

**1. Create a GitHub OAuth App** (github.com → Settings → Developer settings →
OAuth Apps → New OAuth App). Authorization callback URL:
`https://<vm>.exe.xyz/auth/github/callback`. Record the client ID and secret.

**2. Provision a scoped exe.dev key for minting tokens** — separate from any
personal key, since exe.dev rate-limits per signing key, and restricted so it
can do nothing but mint VM tokens:

```sh
ssh exe.dev ssh-key generate-api-key --label=oauth-gateway-minter \
  --cmds="ssh-key generate-api-key" --exp=1y
```

(Confirmed live: `--vm=`-scoped tokens — the ones this key mints for Claude —
authenticate only to that VM's HTTPS proxy and are rejected outright against
`https://exe.dev/exec`, regardless of `cmds`. The `--cmds` restriction above is
for *this* key, which is a general account token, not VM-scoped.)

**3. Move brain-mcp off the exe.dev-facing port.** It keeps running exactly as
documented in the main README, just on a new, internal-only port:

```sh
BRAIN_ROOT=~/brain BRAIN_MCP_EXEDEV_EMAILS=you@example.com BRAIN_MCP_PORT=4001 \
  uv run brain-mcp --transport http
```

**4. Edit `deploy/nginx.conf`**: replace `allowed-email@example.com` with the
same email already on `BRAIN_MCP_EXEDEV_EMAILS` above, then start nginx with
that config listening on `127.0.0.1:8000` (the port `share port` already
targets — no exe.dev-side change needed). This file also restricts `/register`
and `/token` to Anthropic's published outbound IP range
(`160.79.104.0/21`) — those two are always server-to-server calls from
Claude's backend, never a browser, so this is a free extra layer on top of the
redirect_uri allowlist. Confirm that range is still current at
[the IP address reference](https://platform.claude.com/docs/en/api/ip-addresses)
before relying on it.

**5. Set the gateway's environment and start it:**

| Variable | Meaning |
|---|---|
| `GATEWAY_GITHUB_CLIENT_ID` / `GATEWAY_GITHUB_CLIENT_SECRET` | From step 1 |
| `GATEWAY_ALLOWED_GITHUB_IDS` | Comma-separated GitHub **numeric user id** allowlist — not the username, which is renameable and reclaimable. Find yours at `https://api.github.com/users/<your-login>` (the `id` field). |
| `GATEWAY_ALLOWED_REDIRECT_URIS` | Comma-separated exact `https://` URIs DCR may register against — **this is the actual security boundary against a third party registering their own client** (see "Security review" below). For Claude.ai's hosted surfaces (web, Desktop, mobile, Cowork) this is documented as exactly `https://claude.ai/api/mcp/auth_callback` ([source](https://claude.com/docs/connectors/building/authentication#callback-urls)) — confirm it's still current before relying on it. |
| `GATEWAY_EXEDEV_API_TOKEN` | The scoped minting key from step 2 |
| `GATEWAY_VM_NAME` | exe.dev VM name (the `--vm=` value) |
| `GATEWAY_ISSUER_URL` | `https://<vm>.exe.xyz` (no trailing slash) |
| `GATEWAY_DB_PATH` | Where to keep the SQLite file, e.g. `~/oauth-gateway/gateway.db` |
| `GATEWAY_VM_TOKEN_TTL_SECONDS` | Default `31536000` (1 year); rejected below 1 day (`--exp` has day granularity) |
| `GATEWAY_HOST` / `GATEWAY_PORT` | Default `127.0.0.1:9000` — must match `deploy/nginx.conf` |

```sh
uv run --directory gateway oauth-gateway
```

## What nginx lets through

`deploy/nginx.conf` is **default closed**. One rule at the top of the server
block refuses every request that does not carry the allowed `X-ExeDev-Email`,
before any location is chosen. The only exceptions are the six OAuth paths
named in its `$edge_only` list — they have to work for a caller with no
identity yet, so they rely on exe.dev's edge alone. Everything else, including
the brain site at `/site/` and any location added later, is closed until it is
added to that list.

This is what makes the public window below safe to open: flipping the VM
public removes the edge's login but exposes nothing new, because an anonymous
request gets the same 404 from a closed path as from one that does not exist.
`/mcp` alone answers 401, since Claude needs the challenge to start a flow.

Separately, `/mcp` refuses any request carrying a `Sec-Fetch-Site` header.
Browsers send it on every request and page script cannot strip or forge it;
MCP clients never send it. So a web page — in particular a note rendered on
the brain site, which shares this origin and the reader's login — cannot call
the brain's tools.

Before deploying any change to that file, run it for real:

```sh
gateway/deploy/check-nginx.sh     # needs finch, or CONTAINER=docker
```

It starts the config in a container and requests every kind of path with no
identity, the wrong one, and the right one.

## Connecting Claude.ai (and reconnecting when the token expires)

The port only needs to be public for the few minutes this takes. Do the
mandatory verification (step 2) every time — public mode is a different code
path on exe.dev's edge than private mode, and this check is the entire safety
argument for the design:

```sh
# 1. Flip public
ssh exe.dev share set-public <vm>

# 2. MANDATORY: confirm exe.dev still strips a forged identity header in
#    public mode, not just private mode. If this ever returns success as
#    someone-else@example.com, STOP — do not proceed, the design is unsafe.
curl -i https://<vm>.exe.xyz/mcp -H "X-ExeDev-Email: someone-else@example.com" -d '{}'

#    And that nothing but the OAuth paths opened up with it. Both must be 404:
curl -i https://<vm>.exe.xyz/site/
curl -i https://<vm>.exe.xyz/

# 3. In Claude.ai: Settings -> Connectors -> Add custom connector, URL:
#    https://<vm>.exe.xyz/mcp
#    Complete the GitHub login prompt when it appears.

# 4. As soon as Claude.ai reports the connector is working, flip back:
ssh exe.dev share set-private <vm>

# 5. Confirm a tool call still works now that the port is private again —
#    this validates that a minted VM token satisfies exe.dev's private-mode
#    gate, the same way the existing Claude Code flow already does.
```

Repeat this whenever the connector needs re-adding (new device, revoked
access) or once the 1-year token expires.

## Development

```sh
cd gateway
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Tests never touch the real network — GitHub and exe.dev are both mocked via
an injected `httpx.AsyncClient` (see `tests/conftest.py`).

Verified live against `brain-mcp.exe.xyz` on 2026-07-25:
- The public-mode header-stripping behavior in step 2 above — both an
  anonymous request and one with a forged `X-ExeDev-Email` got the same 401,
  and a real VM token authenticated identically in public and private mode.
- `POST /exec`'s response shape: `{"expires_at", "fingerprint", "label",
  "namespace", "token"}` — matches what `exedev.py`'s `_extract_token` expects.
- `--exp` on `ssh-key generate-api-key` takes a relative duration (`h`/`d`/`w`/`m`/`y`,
  where `m` is *months*) — a raw Unix timestamp is rejected outright. `exedev.py`
  converts the configured TTL to whole days accordingly.

Confirmed against Anthropic's own docs (not live-tested, but a primary source
rather than a guess): the callback URL for Claude.ai's hosted surfaces is
exactly `https://claude.ai/api/mcp/auth_callback`; `/token` must accept
`application/x-www-form-urlencoded` while `/register` uses `application/json`
(Starlette's `request.form()` already handles the former transparently); a
`401` response — never a `200` — is required for Claude to read
`WWW-Authenticate`; and Anthropic's outbound calls originate from
`160.79.104.0/21`.

The end-to-end click-through of Claude.ai's "Add custom connector" UI
succeeded live on 2026-07-25, after finding and fixing two bugs that only
showed up under the real flow:

- `ssh-key generate-api-key` has no `--ctx` flag — confirmed by the CLI
  itself (`"flag parsing error: flag provided but not defined: -ctx"`).
  `ctx` is a field of the lower-level signed-token JSON schema (for tokens
  you sign yourself), not something this convenience command exposes at
  all. Every `/token` call was failing with a 502 until `--ctx` was dropped
  entirely from `exedev.py` — it was audit-only, never load-bearing.
- The Anthropic-IP restriction on `/register`/`/token` was checking
  `$remote_addr`, which is always `127.0.0.1` since exe.dev's edge connects
  to nginx locally — so it was blocking *all* traffic, Anthropic's included,
  not just non-Anthropic callers as intended. Fixed with nginx's
  `set_real_ip_from`/`real_ip_header` to resolve the real client from
  `X-Forwarded-For`, which exe.dev's edge appends.

Both were invisible to the test suite (neither is something a unit test
against mocked HTTP clients would catch) and only surfaced once Claude's
actual connector drove the full flow — worth remembering that "all tests
pass" and "verified against the real VM in isolation" are not the same
claim as "the real end-to-end flow works."

## Security review (2026-07-25)

An independent review turned up one real, fixed vulnerability and one claim
that live testing against the actual VM disproved:

- **Fixed — no consent step + open DCR could deliver an auth code to an
  attacker.** `/authorize` redirected straight to GitHub with no indication of
  *which* registered client was requesting access. Since `/register` would
  accept any `https://` redirect_uri from anyone reachable during a public
  window, a third party could register their own client, then get the account
  owner's browser to complete a crafted `/authorize` (or directly forge
  GitHub's own `state`) — GitHub auto-approves after the first authorization,
  so this could be silent. The owner's real GitHub login would complete
  correctly, but the resulting authorization code — and the 1-year exe.dev
  token it turns into — would be delivered to the attacker's redirect_uri, not
  Claude's. PKCE and exact-match `redirect_uri` don't defend against this: both
  correctly bind the code to whichever client registered it, which is exactly
  the problem when *anyone* can register a client. Fixed by
  `GATEWAY_ALLOWED_REDIRECT_URIS`: DCR now rejects any redirect_uri that isn't
  an exact match (not even a prefix match — tightened once the real value was
  confirmed, see below), so no client but the real one can ever receive a
  code. Further hardened in `deploy/nginx.conf`: `/register` and `/token` are
  now also restricted to Anthropic's published outbound IP range, since both
  are exclusively server-to-server calls from Claude's backend.
  `GATEWAY_ALLOWED_REDIRECT_URIS` was confirmed against
  [Anthropic's own connector docs](https://claude.com/docs/connectors/building/authentication#callback-urls)
  rather than guessed — worth re-confirming if it's ever wrong.
- **Refuted by live testing — the minted token is not an arbitrary
  command-execution credential.** The initial review flagged the token handed
  to Claude as an unrestricted exe.dev API key. Testing directly against
  `brain-mcp.exe.xyz` showed otherwise: a `--vm=`-scoped token is rejected
  outright by `https://exe.dev/exec` regardless of any `cmds` setting — the
  CLI's own help text confirms `--vm` "authenticates to its HTTPS endpoints
  **instead of** exe.dev commands." The `cmds` restriction that does matter is
  on the gateway's own minting credential (`GATEWAY_EXEDEV_API_TOKEN`, step 2
  above), which is a general account token, not VM-scoped.

Also fixed, lower severity: GitHub identity is now checked by immutable
numeric id rather than username (a renamed/reclaimed handle used to be a
working key to the whole brain); auth code redemption is now an atomic
`UPDATE ... WHERE used = 0`, since the previous read-then-write had a
theoretical race between two concurrent redemptions of the same code;
malformed input (non-ASCII PKCE verifier, a multipart file part where a string
was expected) now returns a clean `invalid_request` instead of a bare 500;
`/register` now requires `Content-Type: application/json` (Starlette's
`request.json()` otherwise ignores it, making the endpoint CSRF-postable from
a plain HTML form); a sub-day `GATEWAY_VM_TOKEN_TTL_SECONDS` is now rejected at
config load instead of silently rounding up to a full day; and
`deploy/nginx.conf`'s allowlist match was an unescaped regex where the `.` in
an email matched any character — now a literal exact-string match.

Known, accepted tradeoffs (raised in review, not fixed):

- **No local inventory of minted tokens, though revocation itself is fine.**
  Every successful `/token` call registers its own uniquely-labeled exe.dev
  key (`oauth-gateway-<github-login>-<date>-<random>` — see "Why not local
  signing" below), so any single connection *can* be revoked individually via
  `ssh exe.dev ssh-key remove <label>` without affecting others — `ssh-key
  list` is the inventory. What's still missing is the gateway remembering
  this locally (no local record of what it minted), so finding the right
  label means reading `ssh-key list` and matching by login/date rather than
  looking it up by, say, which Claude.ai session is misbehaving.
- **No garbage collection or rate limiting** on `/register`/`/authorize`.
  Abandoned rows accumulate in SQLite indefinitely, and nothing throttles
  registration attempts while the port is public. Bounded by nginx's default
  request size limit — this is slow disk growth, not a live DoS — but there's
  no cleanup path today.
- **Claude will attempt to refresh anyway.** Per Anthropic's docs, Claude
  requests `offline_access` and refreshes reactively on a `401` plus
  proactively up to 5 minutes before the stored `expires_in` — which, at a
  1-year TTL, means this essentially never fires in practice, but if it ever
  does, `/token`'s `unsupported_grant_type` response is what Claude sees. The
  expectation is that this surfaces as "reconnect the connector," not a silent
  failure, but that hasn't been observed live.
- **Every reconnect registers a brand-new DCR client** — Anthropic's docs
  confirm DCR clients aren't reused across sessions/devices, so
  `oauth_clients` grows by one row per connect/reconnect, not per browser.
  Immaterial at the reconnect frequency this design assumes (about once a
  year), but worth knowing if that assumption changes.

## Why not local signing (2026-07-26)

exe.dev also supports minting tokens by signing them locally with a
registered SSH key (`/docs/https-api-local-key`) — one keypair, unlimited
tokens, fully offline, no server round-trip, structurally immune to the
label-collision bug above. Investigated switching to it and decided against
it, for two reasons confirmed live/in docs rather than assumed:

- **The key isn't scoped the way it sounds.** `ssh-key add --tag=TAG` reads
  like it would restrict the key to *signing tokens for* tagged VMs, but
  tested live: a tag-scoped key gets full interactive shell access
  (`ssh exe.dev ssh <vm>`) to every VM carrying that tag — bounded to those
  VMs (confirmed an untagged VM is inaccessible), but within them it's
  equivalent to a personal login key, not a narrow signing credential. There's
  no `--cmds`-style restriction available for `ssh-key add` the way there is
  for `ssh-key generate-api-key`.
- **No per-token revocation.** exe.dev's own FAQ on `/docs/https-api` says it
  directly: *"Use separate ssh keys for sets of API keys for revocability."*
  A locally-signed token has no server-side registration, so there's nothing
  to revoke individually — only the signing key itself, which would kill
  every token ever signed by it (every connection, all at once) rather than
  just the one you meant to revoke.

The current design's per-connection registered key is already individually
revocable with no such tradeoff, so it stays — just with the improved label
format above.
