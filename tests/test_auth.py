"""exe.dev header-auth layer: unit tests of the middleware, config rules,
and a live HTTP integration test behind a simulated exe.dev proxy."""

import os
import subprocess
import sys

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.responses import JSONResponse

from brain_mcp.auth import ExeDevHeaderAuthMiddleware, build_auth, build_http_middleware
from brain_mcp.config import BrainConfig, ConfigError, load_config
from tests.conftest import data
from tests.test_config import make_repo
from tests.test_integration import _free_port

ALLOWED = frozenset({"me@example.com"})


async def ok_app(scope, receive, send):
    await JSONResponse({"ok": True})(scope, receive, send)


def make_client(emails=ALLOWED):
    app = ExeDevHeaderAuthMiddleware(ok_app, allowed_emails=emails)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- middleware unit tests ---


async def test_allowed_email_passes():
    async with make_client() as c:
        r = await c.get("/", headers={"X-ExeDev-Email": "me@example.com"})
    assert r.status_code == 200


async def test_email_match_is_case_insensitive():
    async with make_client() as c:
        r = await c.get("/", headers={"X-ExeDev-Email": "Me@Example.COM"})
    assert r.status_code == 200


async def test_missing_header_denied():
    # fails closed: this is what every request looks like if the exe.dev
    # proxy is accidentally flipped public
    async with make_client() as c:
        r = await c.get("/")
    assert r.status_code == 401


async def test_wrong_email_denied():
    async with make_client() as c:
        r = await c.get("/", headers={"X-ExeDev-Email": "attacker@example.com"})
    assert r.status_code == 401


async def test_empty_email_denied():
    async with make_client() as c:
        r = await c.get("/", headers={"X-ExeDev-Email": ""})
    assert r.status_code == 401


async def test_duplicate_headers_denied():
    # two X-ExeDev-Email headers means something upstream appended rather
    # than replaced — treat as smuggling, even if one value is allowed
    async with make_client() as c:
        r = await c.get(
            "/",
            headers=httpx.Headers(
                [("X-ExeDev-Email", "me@example.com"), ("X-ExeDev-Email", "me@example.com")]
            ),
        )
    assert r.status_code == 401


# --- config wiring ---


def test_token_and_exedev_mutually_exclusive(tmp_path):
    repo = make_repo(tmp_path / "brain")
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config(
            {
                "BRAIN_ROOT": str(repo),
                "BRAIN_MCP_TOKEN": "x",
                "BRAIN_MCP_EXEDEV_EMAILS": "me@example.com",
            }
        )


def test_email_list_parsed_and_normalized(tmp_path):
    repo = make_repo(tmp_path / "brain")
    cfg = load_config(
        {"BRAIN_ROOT": str(repo), "BRAIN_MCP_EXEDEV_EMAILS": " Me@Example.com , other@x.dev ,"}
    )
    assert cfg.exedev_emails == frozenset({"me@example.com", "other@x.dev"})


def test_exedev_mode_builds_middleware_not_verifier(brain):
    cfg = BrainConfig(root=brain, exedev_emails=frozenset({"me@example.com"}))
    assert build_auth(cfg) is None
    assert len(build_http_middleware(cfg)) == 1


def test_token_mode_builds_verifier_not_middleware(brain):
    cfg = BrainConfig(root=brain, token="t")
    assert build_auth(cfg) is not None
    assert build_http_middleware(cfg) == []


# --- live integration: server behind a simulated exe.dev proxy ---


@pytest.fixture
def exedev_server(brain):
    port = _free_port()
    env = os.environ | {
        "BRAIN_ROOT": str(brain),
        "BRAIN_MCP_EXEDEV_EMAILS": "me@example.com",
        "BRAIN_MCP_PORT": str(port),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "brain_mcp.cli", "--transport", "http"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        import socket
        import time

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise RuntimeError("server never came up")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


async def test_exedev_identity_header_grants_access(exedev_server, brain):
    transport = StreamableHttpTransport(exedev_server, headers={"X-ExeDev-Email": "me@example.com"})
    async with Client(transport) as client:
        result = await client.call_tool("read", {"path": "inbox/idea.md"})
        assert "Build a rocket" in result.data
        wrote = data(
            await client.call_tool("write", {"path": "inbox/via-exe.md", "content": "x\n"})
        )
        assert (brain / "inbox" / "via-exe.md").read_text() == "x\n"
        assert wrote["path"] == "inbox/via-exe.md"


async def test_exedev_missing_header_rejected(exedev_server):
    with pytest.raises(Exception, match="401|[Uu]nauthorized"):
        async with Client(StreamableHttpTransport(exedev_server)) as client:
            await client.call_tool("read", {"path": "inbox/idea.md"})


async def test_exedev_wrong_email_rejected(exedev_server):
    transport = StreamableHttpTransport(
        exedev_server, headers={"X-ExeDev-Email": "intruder@example.com"}
    )
    with pytest.raises(Exception, match="401|[Uu]nauthorized"):
        async with Client(transport) as client:
            await client.call_tool("read", {"path": "inbox/idea.md"})


async def test_exedev_no_server_banner(exedev_server):
    async with httpx.AsyncClient() as c:
        r = await c.post(exedev_server, json={})
    assert r.status_code == 401
    assert "server" not in r.headers
