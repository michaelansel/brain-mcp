"""Mints exe.dev VM-scoped bearer tokens via exe.dev's own HTTPS API.

This is the whole point of the design: the gateway never signs or verifies a
token itself. It asks exe.dev — the same system already trusted to authenticate
every other request to this VM — to mint a real, short... actually 1-year-lived
credential, and hands that back to the OAuth client verbatim.

Confirmed live against brain-mcp.exe.xyz (see the plan's Testing section):
- `--exp` takes a relative duration (`h`/`d`/`w`/`m`/`y` — note `m` is
  *months*, not minutes) — NOT a Unix timestamp; `--exp=<large integer>` is
  rejected outright.
- `ssh-key generate-api-key --vm=... --exp=... --json` returns
  `{"expires_at": "...", "fingerprint": "...", "label": "...",
  "namespace": "...", "token": "exe1...."}` — the token is always under
  `"token"`.
"""

from __future__ import annotations

import json
import shlex

import httpx

_EXEC_URL = "https://exe.dev/exec"
_SECONDS_PER_DAY = 86400


class ExeDevMintError(Exception):
    """Raised when exe.dev's token-minting API fails or returns something unexpected."""


def _build_command(vm_name: str, ttl_seconds: int, github_login: str) -> str:
    days = max(1, round(ttl_seconds / _SECONDS_PER_DAY))
    ctx = json.dumps({"via": "oauth-gateway", "github_login": github_login}, separators=(",", ":"))
    return (
        f"ssh-key generate-api-key --vm={shlex.quote(vm_name)} "
        f"--label=oauth-gateway --exp={days}d --ctx={shlex.quote(ctx)}"
    )


def _extract_token(payload: object) -> str:
    if isinstance(payload, dict):
        token = payload.get("token")
        if isinstance(token, str) and token:
            return token
    raise ExeDevMintError(f"exe.dev's /exec response did not include a token: {payload!r}")


async def mint_vm_token(
    http_client: httpx.AsyncClient,
    exedev_api_token: str,
    vm_name: str,
    ttl_seconds: int,
    github_login: str,
) -> str:
    command = _build_command(vm_name, ttl_seconds, github_login)
    response = await http_client.post(
        _EXEC_URL,
        headers={"Authorization": f"Bearer {exedev_api_token}"},
        content=command,
    )
    if response.status_code != 200:
        raise ExeDevMintError(
            f"exe.dev /exec returned {response.status_code}: {response.text[:500]}"
        )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return _extract_token(payload)
