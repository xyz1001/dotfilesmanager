import io
import json
import subprocess

import pytest
from click.testing import CliRunner

from dotfilesmanager import encryption
from dotfilesmanager.cli import click_app


def test_aes_siv_is_deterministic_and_round_trips():
    key = b"k" * 64
    ciphertext = encryption._crypt(key, b"secret")
    assert ciphertext == encryption._crypt(key, b"secret")
    assert encryption._crypt(key, ciphertext, True) == b"secret"


@pytest.mark.parametrize(
    ("filename", "content"),
    [("config.json", b'{"password": "secret", "other": "visible"}\n'),
     ("config.yaml", b"password: secret\nother: visible\n")],
)
def test_keys_config_clean_smudge_and_existing_envelope(tmp_path, monkeypatch,
                                                         filename, content):
    (tmp_path / "rules.json").write_text(json.dumps({"*": {"keys": ["password"]}}))
    monkeypatch.setattr(encryption, "_key", lambda _: b"k" * 64)
    clean = encryption.transform(str(tmp_path), filename, content)
    assert b"secret" not in clean
    assert encryption.transform(str(tmp_path), filename, clean) == clean
    assert encryption.transform(str(tmp_path), filename, clean, True) == content


def test_filter_reads_only_local_cached_key(tmp_path, monkeypatch):
    (tmp_path / "rules.json").write_text('{"*.yaml":{"keys":["token"]}}')
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "line-crypt.key").write_bytes(b"k" * 64)
    monkeypatch.setattr(encryption, "repo_root", lambda: str(tmp_path))
    monkeypatch.setattr(encryption, "_git", lambda *args: pytest.fail("git gpg"))
    streams = {"rb": io.BytesIO(b"token: secret\n"), "wb": io.BytesIO()}
    monkeypatch.setattr(encryption.os, "fdopen", lambda _, mode, closefd=False: streams[mode])
    encryption.filter_command("clean", "config.yaml")
    assert b"secret" not in streams["wb"].getvalue()


def test_init_creates_files_configures_filter_and_stages(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "rules.json").write_text('{"*.json":{"keys":["password"]}}')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "gpg":
            return subprocess.CompletedProcess(command, 0, stdout=b"wrapped")
        return subprocess.CompletedProcess(command, 0, stdout=b"")

    monkeypatch.setattr(encryption.subprocess, "run", run)
    monkeypatch.setattr(encryption, "_git", lambda root, *args: b"")
    monkeypatch.setattr(encryption.os, "urandom", lambda size: b"k" * size)
    encryption.init(str(tmp_path), "alice")
    assert (tmp_path / ".git-filters/key.gpg").read_bytes() == b"wrapped"
    assert (tmp_path / ".git/line-crypt.key").read_bytes() == b"k" * 64
    assert "*.json filter=dfm-encrypt" in (tmp_path / ".gitattributes").read_text()
    assert ["git", "add", ".git-filters/key.gpg", "rules.json", ".gitattributes"] in calls


def test_encrypt_has_no_lock_or_unlock_commands():
    result = CliRunner().invoke(click_app, ["encrypt", "--help"])
    assert result.exit_code == 0
    assert "init" in result.output and "filter" in result.output
    assert "lock" not in result.output and "unlock" not in result.output
