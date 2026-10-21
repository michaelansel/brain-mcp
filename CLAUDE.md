# brain-mcp: working rules

## This repository is public

Everything committed here — code, tests, docs, commit messages, and git
history — is readable by anyone on the internet, forever. History is never
rewritten, so a mistake cannot be taken back by a later commit. Treat every
commit as publishing.

The code is generic; the deployment is private. Anything that describes *this
author's* brain, server, or accounts belongs outside the repo (in the
deployment's environment, in the brain itself, or in local, untracked files).

### Never commit

- **The brain's contents.** No real notes, file names, folder names, project
  names, summaries, or excerpts from the brain — not as test fixtures, not as
  examples in docs, not in commit messages, not in error-message samples.
  Tests and demos build throwaway brains from invented content.
- **Secrets of any kind.** API keys, bearer tokens, exe.dev tokens (`exe1.…`),
  GitHub tokens or OAuth client secrets, Anthropic keys, SSH or TLS private
  keys, session cookies, `.env` files. Configuration reads secrets from the
  environment; the repo only names the variables.
- **Real deployment identifiers.** The live VM name and hostname, real
  allowlisted emails, real GitHub logins or numeric IDs, real redirect URIs
  beyond Anthropic's published ones, IP addresses of private machines.
- **Personal information.** Email addresses, phone numbers, home or work
  addresses, names of family, colleagues, or clients, health, finances,
  calendar entries, or anything else about a real person.
- **Machine-local details.** Absolute paths under a home directory
  (`/Users/<name>/…`, `/home/<name>/…`), hostnames of personal machines,
  local tool configuration (`.claude/settings.local.json`, editor configs
  with paths), shell history, logs, or database files (the gateway's SQLite
  file included).
- **Transcripts and debugging dumps.** Raw request/response captures, server
  logs, or tool output pasted into docs or tests often carry tokens, emails,
  or note content. Reduce them to a minimal, invented example.

### Use placeholders instead

| Kind | Use |
| --- | --- |
| exe.dev hostname | `<vm>.exe.xyz`, `my-vm.exe.xyz`, `test-vm.exe.xyz` |
| VM name | `my-vm`, `<vm>` |
| Email | anything `@example.com`, `@example.org`, `@example.net` (RFC 2606) |
| GitHub user | `octocat`, numeric ID `12345` |
| Token / secret | obviously fake strings: `test-token`, `secret`, `exe1.minted` |
| Path | relative paths, `tmp_path` in tests, `/path/to/brain` in docs |
| Brain content | invented notes (`inbox/idea.md`, `projects/alpha/…`) |

When documenting something verified against the real deployment, say "the
author's deployment" or "a live deployment" rather than naming it.

### Before every commit

1. Read the full staged diff (`git diff --cached`), not just the files you
   meant to change — look for anything on the "never commit" list.
2. Check new files especially: fixtures, scripts, captured output, binaries.
   `git status` should show nothing unexpected; never `git add -A` blindly.
3. Write the commit message without quoting brain content, real hostnames,
   or anything from the deployment's environment.
4. Run `uv run pytest tests/test_public_safety.py`. It scans tracked files
   for real-looking emails, non-placeholder exe.dev hostnames, home-directory
   paths, and token or private-key shapes. Passing it is necessary, not
   sufficient — it cannot recognise a real note or a person's name.

If something private does get committed, stop and tell the user before
pushing. Once pushed, assume it is compromised: rotate any secret
immediately; removing it from history does not un-publish it.

### Adding new configuration

New settings that could hold private values (allowlists, hostnames,
credentials, remote URLs) are read from environment variables, documented in
the README with placeholder values, and never given a real default in code.
