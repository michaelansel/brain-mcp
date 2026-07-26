from starlette.testclient import TestClient

from oauth_gateway.app import build_app
from tests.conftest import make_mock_http_client


def test_authorization_server_metadata_shape(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == "https://test-vm.exe.xyz"
    assert body["authorization_endpoint"] == "https://test-vm.exe.xyz/authorize"
    assert body["token_endpoint"] == "https://test-vm.exe.xyz/token"
    assert body["registration_endpoint"] == "https://test-vm.exe.xyz/register"
    assert body["grant_types_supported"] == ["authorization_code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


def test_protected_resource_metadata_shape(config):
    app = build_app(config, http_client=make_mock_http_client())
    with TestClient(app) as client:
        r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == "https://test-vm.exe.xyz/mcp"
    assert body["authorization_servers"] == ["https://test-vm.exe.xyz"]
