"""Startup configuration failures and happy path."""

import subprocess

import pytest

from brain_mcp.config import BrainConfig, ConfigError, load_config


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_missing_env(tmp_path):
    with pytest.raises(ConfigError, match="BRAIN_ROOT is not set"):
        load_config({})


def test_empty_env(tmp_path):
    with pytest.raises(ConfigError, match="BRAIN_ROOT is not set"):
        load_config({"BRAIN_ROOT": ""})


def test_nonexistent_root(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config({"BRAIN_ROOT": str(tmp_path / "nope")})


def test_root_is_file(tmp_path):
    f = tmp_path / "file.md"
    f.write_text("x\n")
    with pytest.raises(ConfigError, match="not a directory"):
        load_config({"BRAIN_ROOT": str(f)})


def test_not_a_git_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    with pytest.raises(ConfigError, match="not a git repository"):
        load_config({"BRAIN_ROOT": str(d)})


def test_subdir_of_repo_rejected(tmp_path):
    repo = make_repo(tmp_path / "repo")
    sub = repo / "sub"
    sub.mkdir()
    with pytest.raises(ConfigError, match="not the top level"):
        load_config({"BRAIN_ROOT": str(sub)})


def test_bad_port(tmp_path):
    repo = make_repo(tmp_path / "brain")
    with pytest.raises(ConfigError, match="BRAIN_MCP_PORT"):
        load_config({"BRAIN_ROOT": str(repo), "BRAIN_MCP_PORT": "http"})


def test_happy_path(tmp_path):
    repo = make_repo(tmp_path / "brain")
    cfg = load_config(
        {
            "BRAIN_ROOT": str(repo),
            "BRAIN_MCP_TOKEN": "sekrit",
            "BRAIN_MCP_HOST": "0.0.0.0",
            "BRAIN_MCP_PORT": "9001",
        }
    )
    assert isinstance(cfg, BrainConfig)
    assert cfg.root == repo.resolve()
    assert cfg.token == "sekrit"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9001


def test_cli_fails_loudly_without_brain_root(monkeypatch, capsys):
    from brain_mcp import cli

    monkeypatch.delenv("BRAIN_ROOT", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2
    assert "BRAIN_ROOT" in capsys.readouterr().err


def test_defaults(tmp_path):
    repo = make_repo(tmp_path / "brain")
    cfg = load_config({"BRAIN_ROOT": str(repo)})
    assert cfg.token is None
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
