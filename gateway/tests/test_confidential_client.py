"""A confidential client added by hand (Home Assistant's shape): a static client_id and
secret, client_secret_post, no PKCE, and a refresh_token it can never actually use."""

import json
import sqlite3

import httpx
import pytest
from starlette.testclient import TestClient

from oauth_gateway import cli, db
from oauth_gateway.app import build_app
from tests.conftest import ALLOWED_ID, ALLOWED_LOGIN, MINTED_TOKEN
from tests.test_authorize_flow import _query, _register, pkce_pair

HA_REDIRECT = "https://my.home-assistant.io/redirect/oauth"


def _recording_http_client(seen: list[str]):
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gh-access-token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"login": ALLOWED_LOGIN, "id": int(ALLOWED_ID)})
        if url == "https://exe.dev/exec":
            seen.append(request.content.decode())
            return httpx.Response(200, json={"token": MINTED_TOKEN})
        raise AssertionError(f"unexpected request to {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _add_client(config, name="ha", redirect=HA_REDIRECT) -> tuple[str, str]:
    conn = db.connect(config.db_path)
    client_id, secret = f"{name}-id", f"{name}-secret"
    db.create_client(conn, client_id, [redirect], name=name, client_secret=secret)
    conn.close()
    return client_id, secret


def _code(client, client_id, redirect=HA_REDIRECT, challenge=None) -> str:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect}
    if challenge:
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    r = client.get("/authorize", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = _query(r.headers["location"])["state"]
    r = client.get(
        "/auth/github/callback",
        params={"code": "fake-github-code", "state": state},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith(redirect + "?")
    return _query(r.headers["location"])["code"]


def _exchange(client, client_id, secret, code, **extra):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": HA_REDIRECT,
        **extra,
    }
    if secret is not None:
        data["client_secret"] = secret
    return client.post("/token", data=data)


def test_full_flow_without_pkce(config):
    client_id, secret = _add_client(config)
    seen: list[str] = []
    with TestClient(build_app(config, http_client=_recording_http_client(seen))) as client:
        r = _exchange(client, client_id, secret, _code(client, client_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] == MINTED_TOKEN
    assert body["expires_in"] == config.vm_token_ttl_seconds
    assert isinstance(body["expires_in"], int)
    assert body["refresh_token"]
    assert r.headers["cache-control"] == "no-store"
    # The minted key's label names the client, so it can be revoked on its own.
    assert f"--label=oauth-gateway-test-vm-ha-{ALLOWED_LOGIN}-" in seen[0]


def test_wrong_or_missing_secret_rejected(config):
    client_id, _secret = _add_client(config)
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        wrong = _exchange(client, client_id, "not-the-secret", _code(client, client_id))
        missing = _exchange(client, client_id, None, _code(client, client_id))
    for r in (wrong, missing):
        assert r.status_code == 401
        assert r.json()["error"] == "invalid_client"


def test_pkce_still_checked_when_sent(config):
    client_id, secret = _add_client(config)
    verifier, challenge = pkce_pair()
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        code = _code(client, client_id, challenge=challenge)
        bad = _exchange(client, client_id, secret, code, code_verifier="wrong-verifier")
        code = _code(client, client_id, challenge=challenge)
        good = _exchange(client, client_id, secret, code, code_verifier=verifier)
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"
    assert good.status_code == 200


def test_redirect_is_bound_to_the_client(config):
    client_id, _secret = _add_client(config)
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        r = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://attacker.example.com/cb",
            },
            follow_redirects=False,
        )
        # And nobody can register a DCR client against the HA redirect.
        dcr = client.post("/register", json={"redirect_uris": [HA_REDIRECT]})
    assert r.status_code == 400
    assert dcr.status_code == 400


def test_code_bound_to_the_client_that_got_it(config):
    ha_id, ha_secret = _add_client(config)
    other_id, other_secret = _add_client(config, name="other")
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        r = _exchange(client, other_id, other_secret, _code(client, ha_id))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_public_client_still_needs_pkce_and_has_no_secret(config):
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        public_id = _register(client)
        no_pkce = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": public_id,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            },
            follow_redirects=False,
        )
        with_secret = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "anything",
                "client_id": public_id,
                "client_secret": "made-up",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            },
        )
    assert no_pkce.status_code == 400
    assert with_secret.status_code == 401


def test_refresh_fails_cleanly(config):
    client_id, secret = _add_client(config)
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        refresh_token = _exchange(client, client_id, secret, _code(client, client_id)).json()[
            "refresh_token"
        ]
        r = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        bad_secret = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": "wrong",
            },
        )
    # invalid_grant is what makes Home Assistant start a reauth flow.
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"
    assert bad_secret.status_code == 401


def test_add_client_cli_stores_only_a_hash(config, capsys):
    cli.main(["add-client", "--name", "ha", "--redirect-uri", HA_REDIRECT, "--db", config.db_path])
    out = dict(line.split(":", 1) for line in capsys.readouterr().out.splitlines()[:2])
    client_id, secret = out["client_id"].strip(), out["client_secret"].strip()
    conn = db.connect(config.db_path)
    stored = conn.execute(
        "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
    ).fetchone()
    assert secret not in json.dumps(dict(stored))
    assert db.client_secret_matches(conn, client_id, secret)
    assert db.get_client(conn, client_id)["name"] == "ha"


def test_old_database_gains_the_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE oauth_clients (client_id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL,"
        " created_at INTEGER NOT NULL)"
    )
    old.execute("INSERT INTO oauth_clients VALUES ('dcr-client', '[]', 0)")
    old.commit()
    old.close()
    conn = db.connect(path)
    client = db.get_client(conn, "dcr-client")
    assert client["confidential"] is False
    assert client["name"] is None


def test_list_and_remove_client_cli(config, capsys):
    cli.main(["add-client", "--name", "ha", "--redirect-uri", HA_REDIRECT, "--db", config.db_path])
    client_id = capsys.readouterr().out.splitlines()[0].split(":", 1)[1].strip()

    cli.main(["list-clients", "--db", config.db_path])
    listing = capsys.readouterr().out
    assert client_id in listing and HA_REDIRECT in listing

    cli.main(["remove-client", client_id, "--db", config.db_path])
    capsys.readouterr()
    cli.main(["list-clients", "--db", config.db_path])
    assert capsys.readouterr().out == ""

    with pytest.raises(SystemExit):
        cli.main(["remove-client", client_id, "--db", config.db_path])


def test_removed_client_can_no_longer_get_a_token(config):
    client_id, secret = _add_client(config)
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        code = _code(client, client_id)
        conn = db.connect(config.db_path)
        assert db.remove_confidential_client(conn, client_id)
        conn.close()
        r = _exchange(client, client_id, secret, code)
    assert r.status_code == 401


def test_remove_refuses_a_dcr_client(config):
    with TestClient(build_app(config, http_client=_recording_http_client([]))) as client:
        public_id = _register(client)
    conn = db.connect(config.db_path)
    assert not db.remove_confidential_client(conn, public_id)
