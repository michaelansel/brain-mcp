import pytest

from oauth_gateway.config import ConfigError, load_config

BASE_ENV = {
    "GATEWAY_GITHUB_CLIENT_ID": "gh-id",
    "GATEWAY_GITHUB_CLIENT_SECRET": "gh-secret",
    "GATEWAY_EXEDEV_API_TOKEN": "exedev-token",
    "GATEWAY_VM_NAME": "my-vm",
    "GATEWAY_ISSUER_URL": "https://my-vm.exe.xyz/",
    "GATEWAY_DB_PATH": "/tmp/gateway-test.db",
    "GATEWAY_ALLOWED_GITHUB_IDS": "12345",
    "GATEWAY_ALLOWED_REDIRECT_URIS": "https://claude.ai/api/mcp/auth_callback",
}


def test_loads_with_all_required_vars():
    config = load_config(BASE_ENV)
    assert config.allowed_github_ids == frozenset({"12345"})
    assert config.allowed_redirect_uris == frozenset({"https://claude.ai/api/mcp/auth_callback"})
    assert config.issuer_url == "https://my-vm.exe.xyz"  # trailing slash stripped


@pytest.mark.parametrize("missing", list(BASE_ENV.keys()))
def test_missing_required_var_raises(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError):
        load_config(env)


def test_sub_day_vm_token_ttl_rejected():
    env = BASE_ENV | {"GATEWAY_VM_TOKEN_TTL_SECONDS": "3600"}
    with pytest.raises(ConfigError, match="86400"):
        load_config(env)


def test_redirect_uri_must_be_https():
    env = BASE_ENV | {"GATEWAY_ALLOWED_REDIRECT_URIS": "http://claude.ai/callback"}
    with pytest.raises(ConfigError, match="https://"):
        load_config(env)


def test_pending_authorization_ttl_is_configurable():
    env = BASE_ENV | {"GATEWAY_PENDING_AUTHORIZATION_TTL_SECONDS": "120"}
    config = load_config(env)
    assert config.pending_authorization_ttl_seconds == 120
