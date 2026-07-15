"""Tests for YAML configuration persistence."""

import errno

import pytest

from dotfilesmanager import config, operations

HASH = "a" * 32


def test_load_config_initializes_missing_file_and_missing_dotfiles_key(tmp_path):
    assert config.load_config(str(tmp_path)) == {"dotfiles": {}}

    (tmp_path / "dfm.yaml").write_text("other: value\n")
    assert config.load_config(str(tmp_path)) == {"other": "value", "dotfiles": {}}


@pytest.mark.parametrize(
    "content, message",
    [
        ("", "must contain a mapping"),
        ("- item\n", "must contain a mapping"),
        ("[key]: value\n", "invalid YAML mapping key"),
        ("dotfiles: [\n", "invalid dfm.yaml syntax"),
    ],
)
def test_load_rejects_malformed_yaml_with_value_error(tmp_path, content, message):
    (tmp_path / "dfm.yaml").write_text(content)

    with pytest.raises(ValueError, match=message):
        config.load_config(str(tmp_path))


def test_load_rejects_invalid_utf8_with_value_error(tmp_path):
    (tmp_path / "dfm.yaml").write_bytes(b"\xff")

    with pytest.raises(ValueError, match="encoding; expected UTF-8"):
        config.load_config(str(tmp_path))


def test_default_dotfiles_root_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert config.default_dotfiles_root() == str(tmp_path / "home" / "dotfiles")


def test_save_config_writes_unix_newlines_and_loads_data(tmp_path):
    data = {
        "label": "café",
        "dotfiles": {f"files/{HASH}/item": {"linux": {"path": "~/.item"}}},
    }

    config.save_config(str(tmp_path), data)

    content = (tmp_path / "dfm.yaml").read_bytes()
    assert b"\r\n" not in content
    assert content.endswith(b"\n")
    assert f"files/{HASH}/item".encode() not in content
    assert f"{HASH}/item".encode() in content
    assert config.load_config(str(tmp_path)) == data


def test_load_converts_backslash_yaml_key_to_internal_files_key(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        "other: {kept: 'a"
        "\\"
        "b'}\n"
        "dotfiles:\n"
        f"  '{HASH}"
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
        f"files/{HASH}/item": {"windows": {"path": "C:/Users/Alice/item"}}
    }
    config.save_config(str(tmp_path), data)
    content = (tmp_path / "dfm.yaml").read_bytes()
    assert config.load_config(str(tmp_path))["dotfiles"] == data["dotfiles"]
    assert b"C:/Users/Alice/item" in content


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_load_converts_yaml_key_separators_to_internal_files_key(tmp_path, separator):
    yaml_key = f"{HASH}{separator}linux{separator}item"
    (tmp_path / "dfm.yaml").write_text(f"dotfiles: {{'{yaml_key}': {{}}}}\n")

    assert config.load_config(str(tmp_path))["dotfiles"] == {
        f"files/{HASH}/linux/item": {}
    }


def test_load_rejects_colliding_yaml_key_separators(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        f"dotfiles: {{'{HASH}/item': {{}}, '{HASH}\\item': {{}}}}\n"
    )

    with pytest.raises(ValueError, match="normalized saved paths collide"):
        config.load_config(str(tmp_path))


def test_load_rejects_duplicate_raw_yaml_key(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        f"dotfiles: {{'{HASH}/item': {{}}, '{HASH}/item': {{}}}}\n"
    )

    with pytest.raises(ValueError, match="duplicate YAML key"):
        config.load_config(str(tmp_path))


@pytest.mark.parametrize(
    "yaml_key",
    [f"{HASH}/freebsd/item", f"{HASH}/files/item", f"{HASH}/../item"],
)
def test_load_rejects_invalid_yaml_key(tmp_path, yaml_key):
    (tmp_path / "dfm.yaml").write_text(f"dotfiles: {{'{yaml_key}': {{}}}}\n")

    with pytest.raises(ValueError, match="invalid saved path"):
        config.load_config(str(tmp_path))


def test_load_converts_mixed_yaml_key_separators_to_internal_files_key(tmp_path):
    yaml_key = HASH + chr(92) + "linux/item"
    (tmp_path / "dfm.yaml").write_text(f"dotfiles: {{'{yaml_key}': {{}}}}\n")

    assert config.load_config(str(tmp_path))["dotfiles"] == {
        f"files/{HASH}/linux/item": {}
    }


def test_load_rejects_colliding_mixed_yaml_key_separators(tmp_path):
    mixed_key = HASH + chr(92) + "linux/item"
    equivalent_key = f"{HASH}/linux/item"
    (tmp_path / "dfm.yaml").write_text(
        f"dotfiles: {{'{mixed_key}': {{}}, '{equivalent_key}': {{}}}}\n"
    )

    with pytest.raises(ValueError, match="normalized saved paths collide"):
        config.load_config(str(tmp_path))


def test_loaded_backslash_hash_key_is_accepted_by_config_validation(tmp_path):
    key = HASH + "\\linux\\item"
    (tmp_path / "dfm.yaml").write_text(f"dotfiles: {{'{key}': {{}}}}\n")

    loaded = config.load_config(str(tmp_path))

    assert f"files/{HASH}/linux/item" in loaded["dotfiles"]
    assert operations.validate_config(loaded, str(tmp_path)) == []


def test_load_normalization_does_not_mutate_top_level_yaml_alias(tmp_path):
    (tmp_path / "dfm.yaml").write_text(
        "shared: &shared {path: 'C:"
        "\\"
        "legacy'}\n"
        f"dotfiles: {{'{HASH}"
        "\\"
        "item': {windows: *shared}}\n"
    )

    data = config.load_config(str(tmp_path))

    assert data["shared"]["path"] == "C:\\legacy"
    assert data["dotfiles"][f"files/{HASH}/item"]["windows"]["path"] == "C:/legacy"


@pytest.mark.parametrize(
    "key",
    [
        "saved/item",
        f"objects/{HASH}/item",
        f"{HASH}/item",
        f"files/{HASH}\\item",
        f"files/{HASH}/directory\\item",
        f"files/files/{HASH}/item",
        f"files/{HASH}/../item",
        f"files/{HASH}/./item",
        f"files/{HASH}//item",
        f"files/{HASH}/freebsd/item",
        f"files/{HASH.upper()}/item",
    ],
)
def test_save_rejects_invalid_internal_dotfiles_keys(tmp_path, key):
    data = {
        "dotfiles": {
            key: {"linux": {"path": "~/.item"}},
        }
    }

    with pytest.raises(ValueError, match="invalid internal saved path"):
        config.save_config(str(tmp_path), data)


@pytest.mark.parametrize(
    "key",
    [f"files/{HASH}/foo\\bar", f"files/{HASH}/foo\\..\\bar"],
)
def test_operations_rejects_backslash_internal_keys(tmp_path, key):
    assert operations.canonical_save_key(key) is None
    with pytest.raises(ValueError, match="invalid saved path"):
        operations.key_to_save_path(key, str(tmp_path))
    assert operations.validate_config({"dotfiles": {key: {}}}, str(tmp_path)) == [
        "invalid saved path in dfm.yaml"
    ]
    path = tmp_path / "files" / HASH / key.rsplit("/", maxsplit=1)[-1]
    assert operations.validate_save_path(str(path), str(tmp_path)).endswith(
        "is not a canonical saved path"
    )


def test_save_write_failure_does_not_mutate_input(tmp_path, monkeypatch):
    data = {"dotfiles": {f"files/{HASH}/item": {"windows": {"path": "C:\\item"}}}}
    monkeypatch.setattr(
        config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace"))
    )

    with pytest.raises(OSError, match="replace"):
        config.save_config(str(tmp_path), data)

    assert data == {
        "dotfiles": {f"files/{HASH}/item": {"windows": {"path": "C:\\item"}}}
    }


def test_save_failure_keeps_existing_config_and_cleans_temporary(tmp_path, monkeypatch):
    old_key = f"files/{HASH}/old"
    new_key = f"files/{HASH}/new"
    config.save_config(str(tmp_path), {"dotfiles": {old_key: {}}})
    monkeypatch.setattr(
        config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace"))
    )

    with pytest.raises(OSError, match="replace"):
        config.save_config(str(tmp_path), {"dotfiles": {new_key: {}}})

    assert config.load_config(str(tmp_path)) == {"dotfiles": {old_key: {}}}
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
