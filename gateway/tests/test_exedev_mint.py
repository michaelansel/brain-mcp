import json

import httpx
import pytest

from oauth_gateway.exedev import ExeDevMintError, _build_command, mint_vm_token


def test_build_command_uses_day_duration_not_epoch():
    # exe.dev's --exp takes a relative duration (h/d/w/m/y), not a Unix
    # timestamp — confirmed live: a raw epoch integer is rejected outright.
    cmd = _build_command("my-vm", ttl_seconds=31536000, github_login="octocat")
    assert cmd.startswith(
        "ssh-key generate-api-key --vm=my-vm --label=oauth-gateway --exp=365d --ctx="
    )


def test_build_command_rounds_partial_days_and_floors_at_one():
    assert "--exp=1d" in _build_command("my-vm", ttl_seconds=3600, github_login="octocat")
    long_ttl = int(1.6 * 86400)
    assert "--exp=2d" in _build_command("my-vm", ttl_seconds=long_ttl, github_login="octocat")


def test_build_command_ctx_is_compact_json():
    cmd = _build_command("my-vm", ttl_seconds=86400, github_login="octocat")
    # ctx value is shell-quoted; strip the wrapping quote to inspect the JSON itself
    ctx_arg = cmd.split("--ctx=", 1)[1]
    ctx_json = ctx_arg.strip("'")
    expected = {"via": "oauth-gateway", "github_login": "octocat"}
    assert ctx_json == json.dumps(expected, separators=(",", ":"))
    assert " " not in ctx_json


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
    assert captured["body"].startswith("ssh-key generate-api-key --vm=my-vm")


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
