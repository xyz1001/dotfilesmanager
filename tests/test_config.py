"""Tests for YAML configuration persistence."""

from dotfilesmanager import config


def test_load_config_initializes_missing_file_and_missing_dotfiles_key(tmp_path):
    assert config.load_config(str(tmp_path)) == {"dotfiles": {}}

    (tmp_path / "dfm.yaml").write_text("other: value\n")
    assert config.load_config(str(tmp_path)) == {"other": "value", "dotfiles": {}}


def test_default_dotfiles_root_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert config.default_dotfiles_root() == str(tmp_path / "home" / "dotfiles")


def test_save_config_writes_unix_newlines_and_loads_data(tmp_path):
    data = {"dotfiles": {"saved/item": {"linux": {"path": "~/.item"}}}}

    config.save_config(str(tmp_path), data)

    content = (tmp_path / "dfm.yaml").read_bytes()
    assert b"\r\n" not in content
    assert content.endswith(b"\n")
    assert config.load_config(str(tmp_path)) == data
