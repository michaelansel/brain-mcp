import re

import httpx
import pytest

from oauth_gateway.exedev import ExeDevMintError, _build_command, mint_vm_token


# oauth-gateway-<vm>-<github-login>-<YYYYMMDD>-<6 hex chars> — the label is the
# only per-connection audit/revocation handle exe.dev gives us, so it's built
# to be actually useful in `ssh-key list`, not just unique.
def _label_pattern(vm: str, login: str) -> str:
    return rf"oauth-gateway-{vm}-{login}-\d{{8}}-[0-9a-f]{{6}}"


def test_build_command_uses_day_duration_not_epoch():
    # exe.dev's --exp takes a relative duration (h/d/w/m/y), not a Unix
    # timestamp — confirmed live: a raw epoch integer is rejected outright.
    cmd = _build_command("my-vm", ttl_seconds=31536000, github_login="octocat")
    label = _label_pattern("my-vm", "octocat")
    pattern = rf"ssh-key generate-api-key --vm=my-vm --label={label} --exp=365d"
    assert re.fullmatch(pattern, cmd)


def test_build_command_label_is_unique_per_call():
    # exe.dev requires unique labels per account — confirmed live: a second
    # mint with a repeated label fails with 'a key named "..." already exists'.
    first = _build_command("my-vm", ttl_seconds=3600, github_login="octocat")
    second = _build_command("my-vm", ttl_seconds=3600, github_login="octocat")
    assert first != second


def test_build_command_label_includes_vm_and_github_login():
    # The whole point of the naming scheme: at a glance in `ssh-key list`,
    # you can tell which VM and which identity a given connection belongs to.
    cmd = _build_command("other-vm", ttl_seconds=3600, github_login="someone-else")
    assert "oauth-gateway-other-vm-someone-else-" in cmd


def test_build_command_rounds_partial_days_and_floors_at_one():
    cmd = _build_command("my-vm", ttl_seconds=3600, github_login="octocat")
    assert "--exp=1d" in cmd
    long_ttl = int(1.6 * 86400)
    cmd = _build_command("my-vm", ttl_seconds=long_ttl, github_login="octocat")
    assert "--exp=2d" in cmd


async def test_mint_vm_token_sends_expected_request_and_parses_token():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"token": "exe1.minted"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    token = await mint_vm_token(
        client,
        exedev_api_token="secret-minting-token",
        vm_name="my-vm",
        ttl_seconds=31536000,
        github_login="octocat",
    )
    assert token == "exe1.minted"
    assert captured["url"] == "https://exe.dev/exec"
    assert captured["auth"] == "Bearer secret-minting-token"
    # No --ctx: confirmed live that ssh-key generate-api-key doesn't accept it
    # ("flag provided but not defined: -ctx") — it's only a field of the
    # lower-level signed-token JSON schema, not this CLI helper.
    label = _label_pattern("my-vm", "octocat")
    pattern = rf"ssh-key generate-api-key --vm=my-vm --label={label} --exp=365d"
    assert re.fullmatch(pattern, captured["body"])


async def test_mint_vm_token_raises_on_non_200():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Command not allowed by token permissions")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExeDevMintError):
        await mint_vm_token(
            client,
            exedev_api_token="secret",
            vm_name="my-vm",
            ttl_seconds=3600,
            github_login="octocat",
        )


async def test_mint_vm_token_raises_when_no_token_in_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ExeDevMintError):
        await mint_vm_token(
            client,
            exedev_api_token="secret",
            vm_name="my-vm",
            ttl_seconds=3600,
            github_login="octocat",
        )
