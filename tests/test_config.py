"""Tests for YAML configuration persistence."""

import errno

import pytest

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


def test_load_normalizes_only_schema_paths_and_save_writes_slashes(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        "other: {kept: 'a"
        "\\"
        "b'}\n"
        "dotfiles:\n"
        "  'saved"
        "\\"
        "item':\n"
        "    windows: {path: 'C:"
        "\\"
        "Users"
        "\\"
        "Alice"
        "\\"
        "item'}\n"
    )

    data = config.load_config(str(tmp_path))

    assert data["other"] == {"kept": "a\\b"}
    assert data["dotfiles"] == {
        "saved/item": {"windows": {"path": "C:/Users/Alice/item"}}
    }
    config.save_config(str(tmp_path), data)
    content = (tmp_path / "dfm.yaml").read_bytes()
    assert b"saved/item" in content
    assert b"C:/Users/Alice/item" in content


def test_load_rejects_colliding_normalized_saved_paths(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        "dotfiles: {'saved/item': {}, 'saved\\item': {}}\n"
    )

    with pytest.raises(ValueError, match="normalized saved paths collide"):
        config.load_config(str(tmp_path))


def test_load_normalization_does_not_mutate_top_level_yaml_alias(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        "shared: &shared {path: 'C:"
        "\\"
        "legacy'}\n"
        "dotfiles: {'saved"
        "\\"
        "item': {windows: *shared}}\n"
    )

    data = config.load_config(str(tmp_path))

    assert data["shared"]["path"] == "C:\\legacy"
    assert data["dotfiles"]["saved/item"]["windows"]["path"] == "C:/legacy"


def test_save_collision_does_not_mutate_input(tmp_path):
    data = {
        "dotfiles": {
            "saved\\item": {"linux": {"path": "~\\one"}},
            "saved/item": {"linux": {"path": "~\\two"}},
        }
    }

    with pytest.raises(ValueError, match="normalized saved paths collide"):
        config.save_config(str(tmp_path), data)

    assert data == {
        "dotfiles": {
            "saved\\item": {"linux": {"path": "~\\one"}},
            "saved/item": {"linux": {"path": "~\\two"}},
        }
    }


def test_save_write_failure_does_not_mutate_input(tmp_path, monkeypatch):
    data = {"dotfiles": {"saved\\item": {"windows": {"path": "C:\\item"}}}}
    monkeypatch.setattr(
        config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace"))
    )

    with pytest.raises(OSError, match="replace"):
        config.save_config(str(tmp_path), data)

    assert data == {"dotfiles": {"saved\\item": {"windows": {"path": "C:\\item"}}}}


def test_save_failure_keeps_existing_config_and_cleans_temporary(tmp_path, monkeypatch):
    config.save_config(str(tmp_path), {"dotfiles": {"old": {}}})
    monkeypatch.setattr(
        config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace"))
    )

    with pytest.raises(OSError, match="replace"):
        config.save_config(str(tmp_path), {"dotfiles": {"new": {}}})

    assert config.load_config(str(tmp_path)) == {"dotfiles": {"old": {}}}
    assert not list(tmp_path.glob(".dfm.yaml.*"))


def test_directory_sync_tolerates_only_supported_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config.os,
        "open",
        lambda *args: (_ for _ in ()).throw(OSError(errno.EINVAL, "no")),
    )
    config._sync_directory(str(tmp_path))

    monkeypatch.setattr(
        config.os,
        "open",
        lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, "bad")),
    )
    with pytest.raises(OSError):
        config._sync_directory(str(tmp_path))


def test_windows_directory_access_error_is_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setattr(
        config.os,
        "open",
        lambda *args: (_ for _ in ()).throw(OSError(errno.EACCES, "no")),
    )
    config._sync_directory(str(tmp_path))
