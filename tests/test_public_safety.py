"""This repository is public: tracked files must not carry private data.

A tripwire, not a guarantee — it catches the shapes of things that leak
(emails, the live hostname, home paths, tokens), not a real note or a name.
See CLAUDE.md for the full rules.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Generated, and full of package hashes that look like tokens.
SKIP = {"uv.lock", "gateway/uv.lock"}

PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "localhost"}
PLACEHOLDER_VMS = {"<vm>", "my-vm", "myvm", "test-vm"}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,}|localhost)")
EXE_HOST = re.compile(r"([A-Za-z0-9<>_-]+)\.exe\.xyz")
FORBIDDEN = {
    "home-directory path": re.compile(r"/(Users|home)/[a-z][a-z0-9_-]*/"),
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "exe.dev token": re.compile(r"exe1\.[A-Za-z0-9_-]{30,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode()
    for rel in filter(None, out.split("\0")):
        if rel in SKIP:
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary (icons) or deleted in the working tree
        yield rel, text


FILES = list(tracked_text_files())


def findings(check):
    return [
        f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}"
        for rel, text in FILES
        for m in check(text)
    ]


def test_emails_are_placeholders():
    bad = findings(
        lambda t: (
            m for m in EMAIL.finditer(t) if m.group(1).lower() not in PLACEHOLDER_EMAIL_DOMAINS
        )
    )
    assert not bad, "use an @example.com address:\n" + "\n".join(bad)


def test_exe_dev_hostnames_are_placeholders():
    bad = findings(lambda t: (m for m in EXE_HOST.finditer(t) if m.group(1) not in PLACEHOLDER_VMS))
    assert not bad, "use <vm>.exe.xyz:\n" + "\n".join(bad)


@pytest.mark.parametrize("kind", FORBIDDEN)
def test_no_secrets_or_local_paths(kind):
    bad = findings(FORBIDDEN[kind].finditer)
    assert not bad, f"{kind} in a tracked file:\n" + "\n".join(bad)
