import httpx
import pytest

from oauth_gateway.exedev import ExeDevMintError, _build_command, mint_vm_token


def test_build_command_uses_day_duration_not_epoch():
    # exe.dev's --exp takes a relative duration (h/d/w/m/y), not a Unix
    # timestamp — confirmed live: a raw epoch integer is rejected outright.
    cmd = _build_command("my-vm", ttl_seconds=31536000)
    assert cmd == "ssh-key generate-api-key --vm=my-vm --label=oauth-gateway --exp=365d"


def test_build_command_rounds_partial_days_and_floors_at_one():
    assert "--exp=1d" in _build_command("my-vm", ttl_seconds=3600)
    long_ttl = int(1.6 * 86400)
    assert "--exp=2d" in _build_command("my-vm", ttl_seconds=long_ttl)


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
    )
    assert token == "exe1.minted"
    assert captured["url"] == "https://exe.dev/exec"
    assert captured["auth"] == "Bearer secret-minting-token"
    # No --ctx: confirmed live that ssh-key generate-api-key doesn't accept it
    # ("flag provided but not defined: -ctx") — it's only a field of the
    # lower-level signed-token JSON schema, not this CLI helper.
    expected = "ssh-key generate-api-key --vm=my-vm --label=oauth-gateway --exp=365d"
    assert captured["body"] == expected


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
        )
