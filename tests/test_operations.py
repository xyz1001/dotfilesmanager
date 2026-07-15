"""Filesystem-contract tests for dotfile operations."""

import hashlib
import os
from types import SimpleNamespace

import pytest

from dotfilesmanager import config, operations

HASH = "d41d8cd98f00b204e9800998ecf8427e"
ONE_KEY = "files/" + HASH + "/one"
TWO_KEY = "files/" + HASH + "/two"
OBJECT_KEY = "files/" + HASH + "/item"
FOREIGN_KEY = "files/" + HASH + "/foreign"
WINDOWS_KEY = "files/" + HASH + "/windows"


def test_add_system_rejects_unsupported_platform_before_mutation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    source = home / "item"
    source.write_text("original")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "freebsd")

    with pytest.raises(ValueError, match="not a supported system"):
        operations.add(str(source), True, {"dotfiles": {}}, str(root))

    assert source.read_text() == "original"
    assert not root.exists()


def test_mutation_validation_rejects_windows_reparse_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    status = os.lstat(root)
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(operations.os.path, "islink", lambda _: False)
    monkeypatch.setattr(
        operations.os,
        "lstat",
        lambda _: SimpleNamespace(st_mode=status.st_mode, st_file_attributes=0x400),
    )
    assert "reparse-point root" in operations.validate_mutation_paths(
        [str(tmp_path / "outside")], str(root)
    )
    assert "reparse-point root" in operations.validate_view_mutation_root(str(root))


@pytest.mark.parametrize("key", [".", "foo/.."])
def test_config_rejects_saved_key_normalizing_to_root_on_windows(
    tmp_path, monkeypatch, key
):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    config = {"dotfiles": {key: {}}}
    assert "invalid saved path in dfm.yaml" in operations.validate_config(
        config, str(tmp_path / "DotFiles")
    )


def test_windows_case_equivalent_root_is_protected_mutation_path(tmp_path, monkeypatch):
    root = tmp_path / "DotFiles"
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    variant = str(root).replace("DotFiles", "dotfiles")
    assert "protected dotfiles state" in operations.validate_mutation_paths(
        [variant], str(root)
    )


def _config(rel_path, install_path, system="linux"):
    return {"dotfiles": {rel_path: {system: {"path": str(install_path)}}}}


def test_os_name_detects_android_before_linux_and_without_termux_shortcut(monkeypatch):
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    monkeypatch.setattr(operations.sys, "getandroidapilevel", lambda: 34, raising=False)
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    monkeypatch.setenv("ANDROID_DATA", "/data")
    assert operations.os_name() == "android"

    monkeypatch.setattr(
        operations.sys, "getandroidapilevel", lambda: None, raising=False
    )
    assert operations.os_name() == "android"

    monkeypatch.delenv("ANDROID_ROOT")
    monkeypatch.delenv("ANDROID_DATA")
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert operations.os_name() == "linux"

    monkeypatch.setattr(operations.platform, "system", lambda: "Android")
    assert operations.os_name() == "android"


def test_os_name_android_detection_falls_through_every_source(monkeypatch):
    monkeypatch.delenv("ANDROID_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_DATA", raising=False)
    monkeypatch.delattr(operations.sys, "getandroidapilevel", raising=False)
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    assert operations.os_name() == "linux"

    monkeypatch.setattr(operations.sys, "getandroidapilevel", 34, raising=False)
    assert operations.os_name() == "linux"

    monkeypatch.setattr(
        operations.sys,
        "getandroidapilevel",
        lambda: (_ for _ in ()).throw(ValueError("unavailable")),
        raising=False,
    )
    assert operations.os_name() == "linux"

    monkeypatch.setattr(
        operations.sys, "getandroidapilevel", lambda: "34", raising=False
    )
    monkeypatch.setenv("ANDROID_ROOT", "/system")
    assert operations.os_name() == "linux"
    monkeypatch.setenv("ANDROID_DATA", "/data")
    assert operations.os_name() == "android"

    monkeypatch.delenv("ANDROID_ROOT")
    monkeypatch.delenv("ANDROID_DATA")
    monkeypatch.setattr(operations.platform, "system", lambda: "Windows")
    assert operations.os_name() == "windows"


def test_android_targets_use_posix_validation_and_same_path_candidate(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    assert "android" in operations.SUPPORTED_SYSTEMS
    assert operations.validate_foreign_target("android", "~/.config/app") is None
    assert operations.validate_foreign_target("termux", "~/.config/app") is not None
    with pytest.raises(ValueError, match="unsupported"):
        operations.parse_target_mappings(
            ["termux=~/.config/app"], current_system="linux"
        )
    assert operations.target_candidates("~/.config/app", "android") == [
        ("~/.config/app", "~/.config/app")
    ]
    rel = f"files/{HASH}/android/item"
    assert operations.is_platform_specific_save_path(rel)
    assert not operations.is_platform_specific_save_path(f"{HASH}/android/item")


def test_android_current_namespace_never_falls_back_to_linux(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    saved = root / "files" / HASH / "saved"
    home.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "android")
    config = {
        "dotfiles": {
            f"files/{HASH}/saved": {
                "linux": {"path": "~/linux"},
                "android": {"path": "~/android"},
            }
        }
    }
    assert operations.get_path(config, f"files/{HASH}/saved") == str(home / "android")
    linux_only = {"dotfiles": {f"files/{HASH}/saved": {"linux": {"path": "~/linux"}}}}
    assert operations.get_path(linux_only, f"files/{HASH}/saved") is None
    system_save = operations.get_save_path(str(home / "item"), True, str(root))
    assert os.path.relpath(system_save, root).split(os.sep)[2] == "android"
    installed = operations.install(str(saved), config, str(root), lambda _: True)
    assert (home / "android").is_symlink()
    assert installed.messages == [f"Install files/{HASH}/saved -> {home / 'android'}"]
    entries = operations.plan_view(config, str(root))
    assert len(entries) == 2
    assert any("android" in entry.path for entry in entries)


def test_get_save_path_hashes_shrunk_parent_and_optional_system(tmp_path, monkeypatch):
    home = tmp_path / "home"
    install_path = home / ".config" / "app"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    digest = hashlib.md5(b"~/.config").hexdigest()
    assert operations.get_save_path(
        str(install_path), False, str(tmp_path / "repo")
    ) == str(tmp_path / "repo" / "files" / digest / "app")
    assert operations.get_save_path(
        str(install_path), True, str(tmp_path / "repo")
    ) == str(tmp_path / "repo" / "files" / digest / "linux" / "app")


def test_get_save_path_canonicalizes_windows_home_relative_parent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        operations,
        "expanduser",
        lambda path: r"C:\Users\Alice" if path == "~" else path,
    )

    tui_path = r"C:\Users\Alice\.config\opencode\tui.json"
    config_path = r"c:/users/alice/.config/opencode/opencode.json"
    tilde_path = r"~/.config/OPENCODE/settings.json"
    tui_save_path = operations.get_save_path(tui_path, False, str(tmp_path / "repo"))
    config_save_path = operations.get_save_path(
        config_path, False, str(tmp_path / "repo")
    )
    tilde_save_path = operations.get_save_path(
        tilde_path, False, str(tmp_path / "repo")
    )

    digest = hashlib.md5(rb"~\.config\opencode").hexdigest()
    assert os.path.dirname(tui_save_path) == os.path.dirname(config_save_path)
    assert os.path.dirname(tui_save_path) == os.path.dirname(tilde_save_path)
    assert os.path.dirname(tui_save_path) == str(tmp_path / "repo" / "files" / digest)


def test_validate_add_rejects_outside_repository_and_duplicate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "dotfiles"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    home_sibling = tmp_path / "home-sibling"
    home_sibling.write_text("sibling")
    inside_repo = repo / "already-managed"
    inside_repo.write_text("managed")
    repo_sibling = home / "dotfiles-sibling"
    repo_sibling.write_text("sibling")
    candidate = home / ".gitconfig"
    candidate.write_text("config")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    assert "must be in home" in operations.validate_add(str(outside), False, str(repo))
    assert "must be in home" in operations.validate_add(
        str(home_sibling), False, str(repo)
    )
    assert "cannot be in dotfiles" in operations.validate_add(
        str(inside_repo), False, str(repo)
    )
    assert operations.validate_add(str(repo_sibling), False, str(repo)) is None

    save_path = operations.get_save_path(str(candidate), False, str(repo))
    os.makedirs(os.path.dirname(save_path))
    open(save_path, "w").close()
    assert "has been kept in dotfiles" in operations.validate_add(
        str(candidate), False, str(repo)
    )


def test_validate_add_uses_lexical_path_for_symbolic_links(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    repo.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "item"
    outside_file.write_text("outside")
    home_file = home / "item"
    home_file.write_text("home")
    repo_link = repo / "outside-link"
    repo_link.symlink_to(outside_file)
    outside_link = outside / "home-link"
    outside_link.symlink_to(home_file)
    monkeypatch.setenv("HOME", str(home))

    assert "cannot be in dotfiles" in operations.validate_add(
        str(repo_link), False, str(repo)
    )
    assert "must be in home" in operations.validate_add(
        str(outside_link), False, str(repo)
    )


def test_add_rejects_backslash_filename_before_mutation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    install = home / "literal\\backslash"
    home.mkdir()
    install.write_text("value")
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(ValueError, match="invalid saved filename"):
        operations.add(str(install), False, {"dotfiles": {}}, str(repo))

    assert install.read_text() == "value"
    assert not repo.exists()


def test_add_moves_file_creates_absolute_link_and_records_posix_path(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    install_path = home / ".config" / "app"
    install_path.parent.mkdir(parents=True)
    install_path.write_text("settings")
    repo = home / "dotfiles"
    config = {"dotfiles": {}}
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.add(str(install_path), True, config, str(repo))

    saved = operations.get_save_path(str(install_path), True, str(repo))
    rel_saved = operations.save_path_to_key(saved, str(repo))
    assert open(saved).read() == "settings"
    assert install_path.is_symlink()
    assert os.readlink(install_path) == saved
    assert result.config["dotfiles"][rel_saved]["linux"]["path"] == "~/.config/app"
    assert result.messages == [f"Add {install_path} to {rel_saved}"]


def test_add_encrypt_adds_file_attribute_rule(tmp_path, monkeypatch):
    home = tmp_path / "home"
    install = home / ".config" / "app"
    install.parent.mkdir(parents=True)
    install.write_text("settings")
    repo = home / "dotfiles"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations.shutil, "which", lambda name: "/usr/bin/git-crypt")
    operations.add(str(install), False, {"dotfiles": {}}, str(repo), encrypt=True)

    key = operations.save_path_to_key(
        operations.get_save_path(str(install), False, str(repo)), str(repo)
    )
    assert (repo / ".gitattributes").read_text() == (
        f"{key} filter=git-crypt diff=git-crypt\n"
    )


def test_add_encrypt_missing_git_crypt_leaves_source_and_attributes_unchanged(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    install = home / ".config" / "app"
    install.parent.mkdir(parents=True)
    install.write_text("settings")
    repo = home / "dotfiles"
    repo.mkdir()
    attributes = repo / ".gitattributes"
    attributes.write_text("existing rule\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations.shutil, "which", lambda name: None)

    with pytest.raises(ValueError, match="install git-crypt and retry"):
        operations.add(str(install), False, {"dotfiles": {}}, str(repo), encrypt=True)

    assert install.read_text() == "settings"
    assert attributes.read_text() == "existing rule\n"


def test_git_crypt_attributes_preserve_and_are_idempotent(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    attributes = root / ".gitattributes"
    attributes.write_text("unrelated pattern\nfiles/existing filter=other\n")

    operations._ensure_git_crypt_attributes(root, "files/hash/item", False)
    operations._ensure_git_crypt_attributes(root, "files/hash/item", False)

    assert attributes.read_text().splitlines() == [
        "unrelated pattern",
        "files/existing filter=other",
        "files/hash/item filter=git-crypt diff=git-crypt",
    ]


def test_git_crypt_attributes_use_directory_and_system_specific_rules(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    operations._ensure_git_crypt_attributes(root, "files/hash/linux/item", True)

    assert (root / ".gitattributes").read_text() == (
        "files/hash/linux/item/** filter=git-crypt diff=git-crypt\n"
    )


def test_save_config_omits_internal_files_namespace(tmp_path):
    repo = tmp_path / "repo"
    data = {"dotfiles": {f"files/{HASH}/saved": {"linux": {"path": "~/saved"}}}}

    config.save_config(str(repo), data)

    saved_yaml = (repo / "dfm.yaml").read_text()
    assert f"{HASH}/saved:" in saved_yaml
    assert "files/" not in saved_yaml
    assert config.load_config(str(repo)) == data


def test_validate_remove_accepts_repository_link_and_rejects_outside(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("data")
    link = tmp_path / "link"
    link.symlink_to(saved)
    outside = tmp_path / "outside"
    outside.write_text("data")
    sibling = tmp_path / "repo-sibling"
    sibling.write_text("data")

    assert operations.validate_remove(str(link), str(repo)) is None
    assert "is not in dotfiles" in operations.validate_remove(str(outside), str(repo))
    assert "is not in dotfiles" in operations.validate_remove(str(sibling), str(repo))


def test_validate_remove_accepts_repo_link_and_relative_install_link(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    saved_link = repo / "files" / HASH / "saved-link"
    saved_link.parent.mkdir(parents=True)
    saved_link.symlink_to(outside)
    install_dir = tmp_path / "home" / "config"
    install_dir.mkdir(parents=True)
    install_link = install_dir / "item"
    install_link.symlink_to(f"../../repo/files/{HASH}/saved-link")

    assert operations.validate_remove(str(saved_link), str(repo)) is None
    assert operations.validate_remove(str(install_link), str(repo)) is None


def test_validate_remove_rejects_non_hash_saved_namespace(tmp_path):
    repo = tmp_path / "repo"
    saved = repo / "files" / "not-a-hash" / "item"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")

    assert "canonical saved path" in operations.validate_remove(str(saved), str(repo))


def test_remove_resolves_relative_install_link_target(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install_dir = tmp_path / "home" / "config"
    install_dir.mkdir(parents=True)
    install_link = install_dir / "item"
    install_link.symlink_to(f"../../repo/files/{HASH}/saved")
    config = {
        "dotfiles": {f"files/{HASH}/saved": {"linux": {"path": str(install_link)}}}
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(str(install_link), config, str(repo))

    assert result.messages == [f"Remove files/{HASH}/saved"]
    assert result.config == {"dotfiles": {}}
    assert install_link.read_text() == "saved"
    assert not install_link.is_symlink()
    assert not saved.exists()


def test_remove_uses_repo_symbolic_link_path_without_following_target(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    saved_link = repo / "files" / HASH / "saved-link"
    saved_link.parent.mkdir(parents=True)
    saved_link.symlink_to(outside)
    install_path = tmp_path / "home" / "item"
    install_path.parent.mkdir()
    install_path.symlink_to(saved_link)
    config = {
        "dotfiles": {f"files/{HASH}/saved-link": {"linux": {"path": str(install_path)}}}
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(str(saved_link), config, str(repo))

    assert result.messages == [f"Remove files/{HASH}/saved-link"]
    assert result.config == {"dotfiles": {}}
    assert not os.path.lexists(saved_link)
    assert install_path.is_symlink()
    assert os.readlink(install_path) == str(outside)


def test_is_within_compares_windows_paths_case_insensitively(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")

    assert operations._is_within(
        r"C:\\Users\\Alice\\Dotfiles\\item", r"c:\\users\\alice\\dotfiles"
    )
    assert not operations._is_within(
        r"C:\\Users\\Alice\\Dotfiles-old", r"c:\\users\\alice\\dotfiles"
    )


def test_install_skips_other_systems_and_reports_unknown_item(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    linux_install = tmp_path / "home" / ".linuxrc"
    darwin_install = tmp_path / "home" / ".darwinrc"
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH / "one").write_text("linux")
    (repo / "files" / HASH / "two").write_text("darwin")
    config = {
        "dotfiles": {
            ONE_KEY: {"linux": {"path": str(linux_install)}},
            TWO_KEY: {"darwin": {"path": str(darwin_install)}},
        }
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.install(None, config, str(repo), lambda _: True)
    unknown = operations.install(
        str(repo / "files" / "missing"), config, str(repo), lambda _: True
    )

    assert linux_install.is_symlink()
    assert not darwin_install.exists()
    assert result.messages == [f"Install {ONE_KEY} -> {linux_install}"]
    assert unknown.messages == ["files/missing is not kept in dotfiles"]


@pytest.mark.parametrize("existing", ["file", "directory"])
def test_install_replaces_file_or_directory_only_when_confirmed(
    tmp_path, monkeypatch, existing
):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install_path = tmp_path / "home" / "target"
    install_path.parent.mkdir()
    if existing == "file":
        install_path.write_text("existing")
    elif existing == "directory":
        install_path.mkdir()
        (install_path / "child").write_text("existing")
    config = _config(f"files/{HASH}/saved", install_path)
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    rejected = operations.install(str(saved), config, str(repo), lambda _: False)
    assert rejected.messages == []
    assert os.path.lexists(install_path)
    if existing == "file":
        assert not install_path.is_symlink()
        assert install_path.read_text() == "existing"
    elif existing == "directory":
        assert not install_path.is_symlink()
        assert (install_path / "child").read_text() == "existing"

    installed = operations.install(str(saved), config, str(repo), lambda _: True)
    assert install_path.is_symlink()
    assert os.readlink(install_path) == str(saved)
    assert installed.messages == [f"Install files/{HASH}/saved -> {install_path}"]


def test_install_replaces_dangling_destination_without_confirmation(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install_path = tmp_path / "home" / "target"
    install_path.parent.mkdir()
    missing = tmp_path / "missing"
    install_path.symlink_to(missing)
    config = _config(f"files/{HASH}/saved", install_path)
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.install(
        str(saved),
        config,
        str(repo),
        lambda _: pytest.fail("dangling destination must not request confirmation"),
    )

    assert install_path.is_symlink()
    assert os.readlink(install_path) == str(saved)
    assert result.messages == [f"Install files/{HASH}/saved -> {install_path}"]


@pytest.mark.parametrize("relative", [False, True])
def test_install_keeps_correct_absolute_or_relative_link_without_confirmation(
    tmp_path, monkeypatch, relative
):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    install_path = home / "target"
    saved.write_text("saved")
    target = os.path.relpath(saved, install_path.parent) if relative else str(saved)
    install_path.symlink_to(target)
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.install(
        str(saved),
        _config(f"files/{HASH}/saved", install_path),
        str(repo),
        lambda _: pytest.fail("correct link must not request confirmation"),
    )

    assert result.messages == []
    assert os.readlink(install_path) == target


def test_share_handles_known_unknown_and_rejected_replacement(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install_path = tmp_path / "home" / "target"
    install_path.parent.mkdir()
    install_path.write_text("existing")
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/old"}}}}
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    unknown = operations.share(
        str(repo / "files" / "unknown"),
        str(install_path),
        config,
        str(repo),
        lambda _: True,
    )
    rejected = operations.share(
        str(saved), str(install_path), config, str(repo), lambda _: False
    )

    assert unknown.messages == ["files/unknown is not kept in dotfiles"]
    assert rejected.messages == []
    assert install_path.read_text() == "existing"
    assert "linux" not in rejected.config["dotfiles"][f"files/{HASH}/saved"]


def test_share_links_known_item_creates_parent_and_preserves_other_system(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install_path = tmp_path / "home" / "nested" / "target"
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/old"}}}}
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.share(
        str(saved), str(install_path), config, str(repo), lambda _: True
    )

    assert install_path.is_symlink()
    assert os.readlink(install_path) == str(saved)
    assert result.config["dotfiles"][f"files/{HASH}/saved"] == {
        "darwin": {"path": "~/old"},
        "linux": {"path": "~/nested/target"},
    }
    assert result.messages == [f"share files/{HASH}/saved -> {install_path}"]


def test_share_matching_current_mapping_link_state_matrix(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    target = home / "target"
    home.mkdir()
    saved.write_text("saved")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {"dotfiles": {f"files/{HASH}/saved": {"linux": {"path": "~/target"}}}}

    # A correct current mapping and link is a genuine no-op, including relative links.
    target.symlink_to(f"../repo/files/{HASH}/saved")
    correct = operations.share(
        str(saved), str(target), config, str(repo), lambda _: True
    )
    assert correct.config == config
    assert correct.messages == []
    assert os.readlink(target) == f"../repo/files/{HASH}/saved"

    # Missing links are rebuilt without changing the matching mapping.
    target.unlink()
    rebuilt = operations.share(
        str(saved), str(target), config, str(repo), lambda _: True
    )
    assert target.is_symlink()
    assert rebuilt.config == config
    assert rebuilt.messages == [f"share files/{HASH}/saved -> {target}"]

    # A conflicting object is left alone when refused and replaced only when approved.
    target.unlink()
    target.write_text("conflict")
    refused = operations.share(
        str(saved), str(target), config, str(repo), lambda _: False
    )
    assert refused.config == config
    assert refused.messages == []
    assert target.read_text() == "conflict"
    replaced = operations.share(
        str(saved), str(target), config, str(repo), lambda _: True
    )
    assert replaced.config == config
    assert target.is_symlink()


def test_windows_current_mapping_and_link_state_are_case_insensitive(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        operations,
        "expanduser",
        lambda path: r"C:\Users\Alice" + path[1:] if path.startswith("~") else path,
    )
    assert operations._current_paths_equal(r"~\TARGET", r"c:\users\alice\target")

    monkeypatch.setattr(operations.os.path, "lexists", lambda _: True)
    monkeypatch.setattr(operations.os.path, "islink", lambda _: True)
    monkeypatch.setattr(operations.os.path, "exists", lambda _: True)
    monkeypatch.setattr(operations.os, "readlink", lambda _: r"..\SAVED")
    assert (
        operations._link_state(r"c:\users\alice\Saved", r"C:\Users\Alice\Target\link")
        == "correct"
    )


def test_target_mappings_are_portable_and_never_replace_conflicts(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    assert operations.parse_target_mappings(["darwin=~/.config/app"]) == {
        "darwin": "~/.config/app"
    }
    with pytest.raises(ValueError, match="current platform"):
        operations.parse_target_mappings(["linux=~/item"])
    with pytest.raises(ValueError, match="safe path"):
        operations.parse_target_mappings(["windows=~/one/../two"])
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/old"}}}}
    with pytest.raises(ValueError, match="conflicting"):
        operations.merge_targets(config, f"files/{HASH}/saved", {"darwin": "~/new"})


def test_windows_targets_compare_case_insensitively_and_reject_dotfiles(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {
        "dotfiles": {f"files/{HASH}/saved": {"windows": {"path": r"~\AppData\Tool"}}}
    }
    merged = operations.merge_targets(
        config, f"files/{HASH}/saved", {"windows": "~/appdata/tool"}
    )
    assert merged == config
    assert operations.validate_foreign_target("windows", "~/DOTFILES/tool") is not None


def test_windows_wizard_candidates_canonicalize_home_relative_config(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        operations,
        "expanduser",
        lambda path: r"C:\Users\Alice" if path == "~" else path,
    )
    monkeypatch.setattr(operations, "_current_category_roots", lambda: {})
    monkeypatch.setattr(operations, "_current_direct_only_roots", lambda: ())
    candidates = operations.target_candidates(
        r"C:\Users\Alice\.config\nvim\init", "darwin"
    )
    assert candidates == [("~/.config/nvim/init", "~/.config/nvim/init")]


def test_target_candidates_use_literal_standard_bases_and_preserve_suffix(monkeypatch):
    monkeypatch.setenv("HOME", "/unexpected/home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("APPDATA", r"C:\unexpected\appdata")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    source = "~/.config/nvim/lua/init.lua"
    assert operations.target_candidates(source, "linux") == [(source, source)]
    assert operations.target_candidates(source, "android") == [(source, source)]
    assert operations.target_candidates(source, "darwin") == [
        (source, source),
        (
            "~/Library/Application Support/nvim/lua/init.lua",
            "~/Library/Application Support/nvim/lua/init.lua",
        ),
    ]
    assert operations.target_candidates(source, "windows") == [
        (
            "~/AppData/Roaming/nvim/lua/init.lua",
            "~/AppData/Roaming/nvim/lua/init.lua",
        ),
        (source, source),
    ]


def test_target_candidates_keep_nonstandard_paths_direct_only(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    assert operations.target_candidates("~/.vimrc", "windows") == [
        ("~/.vimrc", "~/.vimrc")
    ]


def test_macos_candidates_prefer_unix_data_template(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {"config": "/home/a/.config", "data": "/home/a/.local/share"},
    )
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: "/home/a/.local/share/app"
    )

    assert operations.target_candidates("~/.local/share/app", "darwin") == [
        ("~/.local/share/app", "~/.local/share/app"),
        (
            "~/Library/Application Support/app",
            "~/Library/Application Support/app",
        ),
    ]


def test_platformdirs_private_append_helper_adapter_is_exact(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    assert (
        operations._standard_target_path("linux", "config", "app/child")
        == "~/.config/app/child"
    )
    assert (
        operations._standard_target_path("darwin", "config", "app/child")
        == "~/Library/Application Support/app/child"
    )
    assert (
        operations._standard_target_path("windows", "config", "app/child")
        == "~/AppData/Roaming/app/child"
    )


def test_standard_candidates_never_access_platformdirs_public_directories(monkeypatch):
    def forbidden(_):
        raise AssertionError("host directory discovery must not run")

    monkeypatch.setattr(operations.MacOS, "user_data_dir", property(forbidden))
    monkeypatch.setattr(operations.Windows, "user_data_dir", property(forbidden))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        operations, "_current_category_roots", lambda: {"config": "/home/test/.config"}
    )
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: "/home/test/.config/app"
    )
    assert operations.target_candidates("~/.config/app", "darwin")[0] == (
        "~/.config/app",
        "~/.config/app",
    )
    assert operations.target_candidates("~/.config/app", "windows")[0] == (
        "~/AppData/Roaming/app",
        "~/AppData/Roaming/app",
    )


def test_category_classifier_keeps_config_data_aliases_and_rejects_other_categories(
    monkeypatch,
):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": "/home/a/.config",
            "data": "/home/a/.local/share",
        },
    )
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: "/home/a/.local/state/log/tool/file"
    )
    assert operations._classify_source_categories("ignored") == ((), None)

    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": "/home/a/Library/Application Support",
            "data": "/home/a/Library/Application Support",
        },
    )
    monkeypatch.setattr(
        operations,
        "normalize_path",
        lambda _: "/home/a/Library/Application Support/tool/settings",
    )
    assert operations._classify_source_categories("ignored") == (
        ("config", "data"),
        "tool/settings",
    )


def test_category_candidates_honor_xdg_current_root_and_windows_config_order(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    xdg = home / "redirected-config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    source = str(xdg / "app" / "nested")
    assert operations._classify_source_categories(source) == (("config",), "app/nested")
    assert operations.target_candidates(source, "windows") == [
        ("~/AppData/Roaming/app/nested", "~/AppData/Roaming/app/nested"),
        ("~/.config/app/nested", "~/.config/app/nested"),
        ("~/redirected-config/app/nested", "~/redirected-config/app/nested"),
    ]


def test_windows_local_cache_state_log_paths_stay_direct_only(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        operations,
        "_current_direct_only_roots",
        lambda: (r"C:\Users\A\AppData\Local\Logs",),
    )
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": r"C:\Users\A\AppData\Roaming",
            "data": r"C:\Users\A\AppData\Local",
        },
    )
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: r"C:\Users\A\AppData\Local\tool"
    )
    assert operations._classify_source_categories("ignored") == (("data",), "tool")
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: r"C:\Users\A\AppData\Local\Logs\tool"
    )
    assert operations._classify_source_categories("ignored") == ((), None)


@pytest.mark.parametrize(
    "source",
    ["~/.cache/tool", "~/.local/state/tool", "~/.local/state/log/tool"],
)
def test_cache_state_log_sources_are_direct_only_candidates(monkeypatch, source):
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {"config": "/home/a/.config", "data": "/home/a/.local/share"},
    )
    monkeypatch.setattr(operations, "normalize_path", lambda path: "/home/a" + path[1:])
    assert operations.target_candidates(source, "windows") == [(source, source)]


@pytest.mark.parametrize(
    ("current", "source", "roots", "direct_roots"),
    [
        (
            "android",
            "~/.cache/tool",
            {"config": "/home/a/.config", "data": "/home/a/.local/share"},
            ("/home/a/.cache", "/home/a/.local/state", "/home/a/.local/state/log"),
        ),
        (
            "darwin",
            "~/Library/Caches/tool",
            {
                "config": "/home/a/Library/Application Support",
                "data": "/home/a/Library/Application Support",
            },
            ("/home/a/Library/Caches", "/home/a/Library/Logs"),
        ),
        (
            "windows",
            "~/AppData/Local/Logs/tool",
            {
                "config": r"C:\Users\A\AppData\Roaming",
                "data": r"C:\Users\A\AppData\Local",
            },
            (r"C:\Users\A\AppData\Local\Logs",),
        ),
    ],
)
def test_distinguishable_non_data_roots_are_direct_only(
    monkeypatch, current, source, roots, direct_roots
):
    monkeypatch.setattr(operations, "os_name", lambda: current)
    monkeypatch.setattr(operations, "_current_category_roots", lambda: roots)
    monkeypatch.setattr(operations, "_current_direct_only_roots", lambda: direct_roots)
    normalized = (
        r"C:\Users\A\AppData\Local\Logs\tool"
        if current == "windows"
        else "/home/a" + source[1:]
    )
    monkeypatch.setattr(operations, "normalize_path", lambda _: normalized)
    monkeypatch.setattr(operations, "_wizard_source_path", lambda _: source)
    assert operations.target_candidates(source, "linux") == [(source, source)]


def test_macos_and_windows_data_aliases_remain_data_convertible(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "darwin")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": "/home/a/Library/Application Support",
            "data": "/home/a/Library/Application Support",
        },
    )
    monkeypatch.setattr(
        operations,
        "_current_direct_only_roots",
        lambda: ("/home/a/Library/Caches", "/home/a/Library/Logs"),
    )
    monkeypatch.setattr(
        operations,
        "normalize_path",
        lambda _: "/home/a/Library/Application Support/tool",
    )
    assert operations._classify_source_categories("ignored")[0] == ("config", "data")

    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": r"C:\Users\A\AppData\Roaming",
            "data": r"C:\Users\A\AppData\Local",
        },
    )
    monkeypatch.setattr(
        operations,
        "_current_direct_only_roots",
        lambda: (r"C:\Users\A\AppData\Local\Logs",),
    )
    monkeypatch.setattr(
        operations, "normalize_path", lambda _: r"C:\Users\A\AppData\Local\tool"
    )
    assert operations._classify_source_categories("ignored")[0] == ("data",)


def test_macos_alias_categories_generate_deduplicated_windows_union(monkeypatch):
    monkeypatch.setattr(operations, "os_name", lambda: "darwin")
    monkeypatch.setattr(
        operations,
        "_current_category_roots",
        lambda: {
            "config": "/home/a/Library/Application Support",
            "data": "/home/a/Library/Application Support",
        },
    )
    monkeypatch.setattr(
        operations,
        "normalize_path",
        lambda _: "/home/a/Library/Application Support/tool",
    )
    assert operations.target_candidates(
        "~/Library/Application Support/tool", "windows"
    ) == [
        ("~/AppData/Roaming/tool", "~/AppData/Roaming/tool"),
        ("~/AppData/Local/tool", "~/AppData/Local/tool"),
        ("~/.config/tool", "~/.config/tool"),
        ("~/Library/Application Support/tool", "~/Library/Application Support/tool"),
    ]


def test_current_category_provider_selection_uses_unix_macos_and_windows_modes(
    monkeypatch,
):
    monkeypatch.setattr(
        operations.Unix, "user_config_dir", property(lambda _: "/unix/config")
    )
    monkeypatch.setattr(
        operations.Unix, "user_data_dir", property(lambda _: "/unix/data")
    )
    monkeypatch.setattr(operations, "os_name", lambda: "android")
    assert operations._current_category_roots() == {
        "config": "/unix/config",
        "data": "/unix/data",
    }

    monkeypatch.setattr(
        operations.MacOS,
        "user_config_dir",
        property(lambda _: "/mac/Application Support"),
    )
    monkeypatch.setattr(
        operations.MacOS,
        "user_data_dir",
        property(lambda _: "/mac/Application Support"),
    )
    monkeypatch.setattr(operations, "os_name", lambda: "darwin")
    mac_roots = operations._current_category_roots()
    assert mac_roots["config"] == mac_roots["data"]

    def windows_config(instance):
        assert instance.roaming is True
        return r"C:\Roaming"

    def windows_local(instance):
        assert instance.roaming is False
        return r"C:\Local"

    monkeypatch.setattr(operations.Windows, "user_config_dir", property(windows_config))
    monkeypatch.setattr(operations.Windows, "user_data_dir", property(windows_local))
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    assert operations._current_category_roots() == {
        "config": r"C:\Roaming",
        "data": r"C:\Local",
    }


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("linux", "~/.local/share/app"),
        ("android", "~/.local/share/app"),
        ("darwin", "~/Library/Application Support/app"),
        ("windows", "~/AppData/Local/app"),
    ],
)
def test_category_target_layouts_for_data(system, expected):
    assert {"data": operations._standard_target_path(system, "data", "app")} == {
        "data": expected
    }


def test_platform_specific_paths_reject_foreign_targets_and_share_move(
    tmp_path, monkeypatch
):
    rel = f"files/{HASH}/darwin/item"
    assert operations.is_platform_specific_save_path(rel)
    with pytest.raises(ValueError, match="platform-specific"):
        operations.merge_targets({"dotfiles": {rel: {}}}, rel, {"linux": "~/item"})
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = _config(f"files/{HASH}/saved", "~/old")
    with pytest.raises(ValueError, match="different path"):
        operations.share(
            str(saved), str(home / "new"), config, str(repo), lambda _: True
        )


def test_add_moves_directory_and_records_link(tmp_path, monkeypatch):
    home = tmp_path / "home"
    install_path = home / ".config" / "app"
    install_path.mkdir(parents=True)
    (install_path / "settings").write_text("settings")
    repo = home / "dotfiles"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.add(str(install_path), False, {"dotfiles": {}}, str(repo))

    saved = operations.get_save_path(str(install_path), False, str(repo))
    assert install_path.is_symlink()
    assert (
        tmp_path / "home" / ".config" / "app" / "settings"
    ).read_text() == "settings"
    assert result.config["dotfiles"][operations.save_path_to_key(saved, repo)]["linux"][
        "path"
    ] == ("~/.config/app")


def test_remove_copies_shared_file_and_moves_unique_file_back(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shared_saved = repo / "files" / HASH / "shared"
    unique_saved = repo / "files" / HASH / "unique"
    shared_saved.parent.mkdir(parents=True)
    unique_saved.parent.mkdir(parents=True, exist_ok=True)
    shared_saved.write_text("shared")
    unique_saved.write_text("unique")
    shared_install = tmp_path / "home" / "shared"
    unique_install = tmp_path / "home" / "unique"
    shared_install.parent.mkdir()
    shared_install.symlink_to(shared_saved)
    unique_install.symlink_to(unique_saved)
    config = {
        "dotfiles": {
            f"files/{HASH}/shared": {
                "linux": {"path": str(shared_install)},
                "darwin": {"path": "~/shared"},
            },
            f"files/{HASH}/unique": {"linux": {"path": str(unique_install)}},
        }
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    shared_result = operations.remove(str(shared_install), config, str(repo))
    unique_result = operations.remove(str(unique_install), config, str(repo))

    assert shared_install.read_text() == "shared"
    assert not shared_install.is_symlink()
    assert shared_saved.exists()
    assert "linux" not in shared_result.config["dotfiles"][f"files/{HASH}/shared"]
    assert unique_install.read_text() == "unique"
    assert not unique_install.is_symlink()
    assert not unique_saved.exists()
    assert "unique/item" not in unique_result.config["dotfiles"]


def test_remove_copies_shared_directory_and_moves_unique_directory_back(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    shared_saved = repo / "files" / HASH / "shared"
    unique_saved = repo / "files" / HASH / "unique"
    shared_saved.mkdir(parents=True)
    unique_saved.mkdir(parents=True)
    (shared_saved / "settings").write_text("shared")
    (unique_saved / "settings").write_text("unique")
    shared_install = tmp_path / "home" / "shared"
    unique_install = tmp_path / "home" / "unique"
    shared_install.parent.mkdir()
    shared_install.symlink_to(shared_saved, target_is_directory=True)
    unique_install.symlink_to(unique_saved, target_is_directory=True)
    config = {
        "dotfiles": {
            f"files/{HASH}/shared": {
                "linux": {"path": str(shared_install)},
                "darwin": {"path": "~/shared"},
            },
            f"files/{HASH}/unique": {"linux": {"path": str(unique_install)}},
        }
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    operations.remove(str(shared_install), config, str(repo))
    operations.remove(str(unique_install), config, str(repo))

    assert shared_install.is_dir() and not shared_install.is_symlink()
    assert (shared_install / "settings").read_text() == "shared"
    assert shared_saved.is_dir()
    assert unique_install.is_dir() and not unique_install.is_symlink()
    assert (unique_install / "settings").read_text() == "unique"
    assert not unique_saved.exists()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_remove_all_restores_local_shared_object_and_deletes_all_registrations(
    tmp_path, monkeypatch, kind
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "shared"
    install = tmp_path / "home" / "item"
    saved.parent.mkdir(parents=True)
    install.parent.mkdir()
    if kind == "file":
        saved.write_text("shared")
    else:
        saved.mkdir()
        (saved / "settings").write_text("shared")
    key = f"files/{HASH}/shared"
    attributes = repo / ".gitattributes"
    attributes.write_text(
        "unrelated pattern\n"
        + operations._git_crypt_rule(key, kind == "directory")
        + "\n"
    )
    install.symlink_to(saved, target_is_directory=kind == "directory")
    config = {
        "dotfiles": {
            f"files/{HASH}/shared": {
                "linux": {"path": str(install)},
                "darwin": {"path": "~/inaccessible"},
            }
        }
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(str(install), config, str(repo), all_platforms=True)

    assert result.config == {"dotfiles": {}}
    assert not os.path.lexists(saved)
    assert not install.is_symlink()
    if kind == "file":
        assert install.read_text() == "shared"
    else:
        assert (install / "settings").read_text() == "shared"
    assert attributes.read_text() == "unrelated pattern\n"


def test_remove_all_without_current_registration_never_touches_foreign_path(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    foreign = "/inaccessible/foreign/install"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    attributes = repo / ".gitattributes"
    attributes.write_text(
        "unrelated pattern\n"
        + operations._git_crypt_rule(f"files/{HASH}/saved", False)
        + "\n"
    )
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": foreign}}}}
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    original_lexists = operations.os.path.lexists

    def reject_foreign(path):
        assert path != foreign
        return original_lexists(path)

    monkeypatch.setattr(operations.os.path, "lexists", reject_foreign)

    result = operations.remove(str(saved), config, str(repo), all_platforms=True)

    assert result.config == {"dotfiles": {}}
    assert not saved.exists()
    assert attributes.read_text() == "unrelated pattern\n"


def test_remove_selected_foreign_mappings_never_touches_install_paths(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    install = tmp_path / "home" / "install"
    foreign = "/inaccessible/foreign/install"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    attributes = repo / ".gitattributes"
    rule = operations._git_crypt_rule(f"files/{HASH}/saved", False)
    attributes.write_text(f"unrelated pattern\n{rule}\n")
    install.parent.mkdir()
    install.symlink_to(saved)
    config = {
        "dotfiles": {
            f"files/{HASH}/saved": {
                "linux": {"path": str(install)},
                "darwin": {"path": foreign},
                "windows": {"path": "~/other"},
            }
        }
    }
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    original_lexists = operations.os.path.lexists

    def reject_foreign(path):
        assert path != foreign
        return original_lexists(path)

    monkeypatch.setattr(operations.os.path, "lexists", reject_foreign)
    result = operations.remove(
        str(saved), config, str(repo), selected_systems={"darwin"}
    )

    assert result.config["dotfiles"][f"files/{HASH}/saved"] == {
        "linux": {"path": str(install)},
        "windows": {"path": "~/other"},
    }
    assert saved.read_text() == "saved"
    assert install.is_symlink()
    assert attributes.read_text() == f"unrelated pattern\n{rule}\n"


def test_remove_selected_last_foreign_mapping_deletes_saved_object(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    attributes = repo / ".gitattributes"
    attributes.write_text(
        "unrelated pattern\n"
        + operations._git_crypt_rule(f"files/{HASH}/saved", False)
        + "\n"
    )
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/foreign"}}}}
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(
        str(saved), config, str(repo), selected_systems={"darwin"}
    )

    assert result.config == {"dotfiles": {}}
    assert not saved.exists()
    assert attributes.read_text() == "unrelated pattern\n"


def test_remove_selected_current_and_foreign_mappings(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    install = tmp_path / "home" / "install"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    attributes = repo / ".gitattributes"
    attributes.write_text(
        "unrelated pattern\n"
        + operations._git_crypt_rule(f"files/{HASH}/saved", False)
        + "\n"
    )
    install.parent.mkdir()
    install.symlink_to(saved)
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {
        "dotfiles": {
            f"files/{HASH}/saved": {
                "linux": {"path": str(install)},
                "darwin": {"path": "~/darwin"},
                "windows": {"path": "~/windows"},
            }
        }
    }

    result = operations.remove(
        str(saved), config, str(repo), selected_systems={"linux", "darwin"}
    )

    assert result.config["dotfiles"][f"files/{HASH}/saved"] == {
        "windows": {"path": "~/windows"}
    }
    assert saved.read_text() == "saved"
    assert install.read_text() == "saved"


def test_remove_selected_current_and_all_mappings_moves_saved_object(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    install = tmp_path / "home" / "install"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    attributes = repo / ".gitattributes"
    attributes.write_text(
        "unrelated pattern\n"
        + operations._git_crypt_rule(f"files/{HASH}/saved", False)
        + "\n"
    )
    install.parent.mkdir()
    install.symlink_to(saved)
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {
        "dotfiles": {
            f"files/{HASH}/saved": {
                "linux": {"path": str(install)},
                "darwin": {"path": "~/darwin"},
            }
        }
    }

    result = operations.remove(
        str(saved), config, str(repo), selected_systems={"linux", "darwin"}
    )

    assert result.config == {"dotfiles": {}}
    assert not saved.exists()
    assert install.read_text() == "saved"
    assert attributes.read_text() == "unrelated pattern\n"


@pytest.mark.parametrize("selected_systems", [set(), {"android"}])
def test_remove_selected_empty_or_unknown_is_noop(
    tmp_path, monkeypatch, selected_systems
):
    repo = tmp_path / "repo"
    saved = repo / "files" / HASH / "saved"
    install = tmp_path / "home" / "install"
    repo.mkdir()
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install.parent.mkdir()
    install.symlink_to(saved)
    config = {"dotfiles": {f"files/{HASH}/saved": {"linux": {"path": str(install)}}}}
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(
        str(saved), config, str(repo), selected_systems=selected_systems
    )

    assert result.messages == []
    assert result.config == config
    assert saved.read_text() == "saved"
    assert install.is_symlink()


def test_remove_is_silent_when_current_system_is_not_registered(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    config = {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/old"}}}}
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    result = operations.remove(str(saved), config, str(repo))

    assert result.messages == []
    assert result.config == config
    assert saved.exists()


def test_remove_refuses_to_overwrite_non_link_install_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install = tmp_path / "home" / "install"
    install.parent.mkdir()
    install.write_text("unmanaged")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    with pytest.raises(ValueError, match="not a managed link"):
        operations.remove(
            str(saved), _config(f"files/{HASH}/saved", install), str(repo)
        )
    assert install.read_text() == "unmanaged"
    assert saved.read_text() == "saved"


def test_config_only_validates_current_platform_install_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {
        "dotfiles": {
            f"files/{HASH}/saved": {
                "linux": {"path": "~/ok"},
                "darwin": {"path": "/Users/example/ok"},
            }
        }
    }

    assert operations.validate_config(config, str(repo)) == []


def test_view_plan_uses_readable_all_platform_relative_links(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    saved = repo / "files" / HASH / "item"
    foreign = repo / "files" / HASH / "foreign"
    windows_saved = repo / "files" / HASH / "windows"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    foreign.write_text("foreign value")
    windows_saved.write_text("windows value")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {
        "dotfiles": {
            OBJECT_KEY: {"linux": {"path": "~/.config/app/item"}},
            FOREIGN_KEY: {"darwin": {"path": "~/Library/item"}},
            WINDOWS_KEY: {"windows": {"path": "~/AppData/Local/app"}},
        }
    }

    result = operations.view(config, str(repo))
    assert result.messages == ["View 3 item(s)"]
    entries = operations.plan_view(config, str(repo))
    link = next(entry.path for entry in entries if entry.target == str(saved))
    assert link == str(repo / "view" / "linux" / "home" / ".config" / "app" / "item")
    assert os.path.islink(link)
    assert os.readlink(link) == os.path.relpath(saved, os.path.dirname(link))
    assert os.path.realpath(link) == str(saved)
    foreign_link = next(entry.path for entry in entries if entry.target == str(foreign))
    assert os.path.islink(foreign_link)
    assert os.readlink(foreign_link) == os.path.relpath(
        foreign, os.path.dirname(foreign_link)
    )
    assert os.path.realpath(foreign_link) == str(foreign)
    windows_link = next(
        entry.path for entry in entries if entry.target == str(windows_saved)
    )
    assert windows_link == str(
        repo / "view" / "windows" / "home" / "AppData" / "Local" / "app"
    )
    assert os.path.islink(windows_link)
    assert result.config is config


def test_view_rejects_legacy_backslash_saved_key_on_linux(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    saved = repo / "files" / HASH / "saved" / "item"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    (repo / "dfm.yaml").write_text(
        "dotfiles: {'saved\\item': {linux: {path: '~/.item'}}}\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        config.load_config(str(repo))


def test_view_projects_legacy_platform_paths_without_host_path_components(
    tmp_path, monkeypatch
):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {"dotfiles": {}}
    paths = {
        "posix": ("darwin", "/Users/alice/.config/app"),
        "home": ("windows", r"~\App\settings"),
        "drive": ("windows", r"C:\Users\Alice\settings"),
        "unc": ("windows", r"\\server\share\settings"),
        "legacy": ("unknown/../CON", r"C:\legacy\settings"),
    }
    for name, (system, path) in paths.items():
        (repo / "files").mkdir(exist_ok=True)
        (repo / "files" / name).write_text(name)
        config["dotfiles"][name] = {system: {"path": path}}

    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        operations.plan_view(config, str(repo))


def test_view_accepts_unsafe_platform_names_with_safe_projection(tmp_path, monkeypatch):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH).mkdir(exist_ok=True)
    (repo / "files" / HASH / "saved").write_text("saved")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    entry = operations.plan_view(
        {"dotfiles": {f"files/{HASH}/saved": {"../CON": {"path": "legacy/path"}}}},
        str(repo),
    )[0]

    assert operations._view_path_within(entry.path, str(repo / "view"))
    assert "CON" not in os.path.relpath(entry.path, repo / "view").split(os.sep)


def test_view_uses_windows_target_case_rules_but_not_darwin_case_rules(
    tmp_path, monkeypatch
):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH / "one").write_text("one")
    (repo / "files" / HASH / "two").write_text("two")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    with pytest.raises(ValueError, match="duplicate or overlap"):
        operations.plan_view(
            {
                "dotfiles": {
                    ONE_KEY: {"windows": {"path": r"~\Foo"}},
                    TWO_KEY: {"windows": {"path": r"~\foo"}},
                }
            },
            str(repo),
        )
    with pytest.raises(ValueError, match="duplicate or overlap"):
        operations.plan_view(
            {
                "dotfiles": {
                    ONE_KEY: {"windows": {"path": r"C:\Foo"}},
                    TWO_KEY: {"windows": {"path": r"c:\foo"}},
                }
            },
            str(repo),
        )
    entries = operations.plan_view(
        {
            "dotfiles": {
                ONE_KEY: {"darwin": {"path": "~/Foo"}},
                TWO_KEY: {"darwin": {"path": "~/foo"}},
            }
        },
        str(repo),
    )
    assert len(entries) == 2
    assert entries[0].path != entries[1].path


def test_view_preserves_colons_on_posix_hosts_and_escapes_them_on_windows(
    tmp_path, monkeypatch
):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH / "one").write_text("one")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = {"dotfiles": {ONE_KEY: {"darwin": {"path": "~/foo:bar"}}}}

    entry = operations.plan_view(config, str(repo))[0]

    assert entry.path.endswith(os.path.join("darwin", "home", "foo:bar"))
    monkeypatch.setattr(operations.os, "name", "nt")
    entry = operations.plan_view(config, str(repo))[0]
    assert os.path.basename(entry.path) == "v666f6f3a626172"


def test_view_rejects_escaped_projection_parent_overlap(tmp_path, monkeypatch):
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH / "one").write_text("one")
    (repo / "files" / HASH / "two").write_text("two")
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(operations.os, "name", "nt")

    with pytest.raises(ValueError, match="projection paths duplicate or overlap"):
        operations.plan_view(
            {
                "dotfiles": {
                    ONE_KEY: {"darwin": {"path": "~/:"}},
                    TWO_KEY: {"darwin": {"path": "~/v3a/child"}},
                }
            },
            str(repo),
        )


def test_view_staging_failure_keeps_existing_view(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    repo.mkdir(parents=True)
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH).mkdir(exist_ok=True)
    (repo / "files" / HASH / "saved").write_text("saved")
    view = repo / "view"
    view.mkdir()
    marker = view / "old"
    marker.write_text("old")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        operations.windows,
        "create_symlink",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("failed")),
    )

    with pytest.raises(OSError, match="failed"):
        operations.view(_config(f"files/{HASH}/saved", "~/item"), str(repo), force=True)

    assert marker.read_text() == "old"
    assert not list(repo.glob(".view-staging-*"))


def test_view_second_replace_failure_restores_old_view(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    repo.mkdir(parents=True)
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH).mkdir(exist_ok=True)
    (repo / "files" / HASH / "saved").write_text("saved")
    view = repo / "view"
    view.mkdir()
    marker = view / "old"
    marker.write_text("old")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    replace = operations.os.replace
    calls = []

    def fail_staging_replace(source, destination):
        calls.append((source, destination))
        if len(calls) == 2:
            raise OSError("replace failed")
        return replace(source, destination)

    monkeypatch.setattr(operations.os, "replace", fail_staging_replace)

    with pytest.raises(OSError, match="replace failed"):
        operations.view(_config(f"files/{HASH}/saved", "~/item"), str(repo), force=True)

    assert marker.read_text() == "old"
    assert not list(repo.glob(".view-staging-*"))


def test_view_backup_cleanup_failure_keeps_committed_view(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    repo.mkdir(parents=True)
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH).mkdir(exist_ok=True)
    (repo / "files" / HASH / "saved").write_text("saved")
    (repo / "view").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    rmtree = operations.shutil.rmtree

    def fail_backup_cleanup(path):
        if os.path.basename(path).startswith(".view-backup-"):
            raise OSError("cleanup failed")
        return rmtree(path)

    monkeypatch.setattr(operations.shutil, "rmtree", fail_backup_cleanup)

    result = operations.view(
        _config(f"files/{HASH}/saved", "~/item"), str(repo), force=True
    )

    assert result.messages == ["View 1 item(s)"]
    assert os.path.islink(
        operations.plan_view(_config(f"files/{HASH}/saved", "~/item"), str(repo))[
            0
        ].path
    )
    assert list(repo.glob(".view-backup-*"))


def test_view_preserves_canonical_saved_symlink_and_directory_type(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    target = repo / "files" / HASH / "directory"
    target.mkdir(parents=True)
    saved = repo / "files" / HASH / "canonical-directory"
    saved.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    config = _config(f"files/{HASH}/canonical-directory", "~/item")

    entry = operations.plan_view(config, str(repo))[0]
    operations.view(config, str(repo))
    link = entry.path

    assert entry.target == str(saved)
    assert entry.is_directory is True
    assert os.readlink(link) == os.path.relpath(saved, os.path.dirname(link))


def test_view_passes_directory_flag_to_symlink(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    saved = repo / "files" / HASH / "directory"
    saved.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "windows")
    symlink = pytest.MonkeyPatch()
    calls = []
    symlink.setattr(
        operations.os, "symlink", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    try:
        operations.view(
            _config(f"files/{HASH}/directory", "~/item", "windows"), str(repo)
        )
    finally:
        symlink.undo()

    assert calls[0][1]["target_is_directory"] is True


@pytest.mark.parametrize("rel_path", ["view/item", "view", "view/../saved"])
def test_view_namespace_is_rejected_as_configured_saved_path(tmp_path, rel_path):
    assert operations.validate_config(
        {"dotfiles": {rel_path: {}}}, str(tmp_path / "repo")
    ) == ["invalid saved path in dfm.yaml"]


def test_view_namespace_keeps_posix_key_backslashes_as_filenames(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    key = "view\\filename"
    (repo / key).write_text("file")

    assert operations.validate_config({"dotfiles": {key: {}}}, str(repo)) == [
        "invalid saved path in dfm.yaml"
    ]


def test_view_rejects_saved_alias_into_view_and_missing_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    generated = repo / "view" / "item"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated")
    (repo / "files").mkdir()
    (repo / "files" / "alias").symlink_to(generated)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")

    assert operations.validate_config(_config("alias", "~/item"), str(repo)) == [
        "invalid saved path in dfm.yaml"
    ]
    assert operations.validate_config(
        _config("alias/missing", "~/item"), str(repo)
    ) == ["invalid saved path in dfm.yaml"]
    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        operations.plan_view(_config("alias", "~/item"), str(repo))
    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        operations.plan_view(_config("missing", "~/item"), str(repo))


def test_view_alias_resolver_accepts_direct_hash_targets_and_rejects_others(tmp_path):
    repo = tmp_path / "dotfiles"
    namespace = HASH
    saved = repo / "files" / namespace / "saved"
    directory = repo / "files" / namespace / "directory"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    directory.mkdir()
    internal_target = repo / "internal-target"
    internal_target.write_text("internal")
    internal_saved = repo / "files" / namespace / "internal"
    internal_saved.symlink_to(internal_target)
    view = repo / "view"
    view.mkdir()
    relative = view / "relative"
    absolute = view / "absolute"
    relative.symlink_to(os.path.relpath(saved, relative.parent))
    absolute.symlink_to(saved)
    internal_link = view / "internal"
    internal_link.symlink_to(internal_saved)

    assert operations.resolve_view_save_path(str(relative), str(repo)) == str(saved)
    assert operations.resolve_view_save_path(str(absolute), str(repo)) == str(saved)
    assert operations.resolve_view_save_path(str(internal_link), str(repo)) == str(
        internal_saved
    )

    outside = tmp_path / "outside"
    outside.write_text("outside")
    non_hash = repo / "plain"
    non_hash.write_text("plain")
    legacy_saved = repo / namespace / "legacy"
    legacy_saved.parent.mkdir(parents=True)
    legacy_saved.write_text("legacy")
    outside_link = view / "outside"
    non_hash_link = view / "plain"
    legacy_link = view / "legacy"
    directory_link = view / "directory"
    dangling_link = view / "dangling"
    escaping_saved = repo / "files" / namespace / "escaping"
    escaping_link = view / "escaping"
    regular_view_file = view / "regular"
    outside_link.symlink_to(outside)
    non_hash_link.symlink_to(non_hash)
    legacy_link.symlink_to(legacy_saved)
    directory_link.symlink_to(directory, target_is_directory=True)
    dangling_link.symlink_to(repo / "files" / namespace / "missing")
    escaping_saved.symlink_to(outside)
    escaping_link.symlink_to(escaping_saved)
    regular_view_file.write_text("regular")
    descendant = directory_link / "child"
    (directory / "child").write_text("child")
    external_view = tmp_path / "external-view"
    external_view.mkdir()
    nested_alias = external_view / "alias"
    nested_alias.symlink_to(saved)
    nested_parent = view / "nested"
    nested_parent.symlink_to(external_view, target_is_directory=True)

    assert operations.resolve_view_save_path(str(directory_link), str(repo)) == str(
        directory
    )

    for path in (
        outside_link,
        non_hash_link,
        legacy_link,
        dangling_link,
        escaping_link,
        regular_view_file,
        descendant,
        nested_parent / "alias",
        view / "missing",
    ):
        assert operations.resolve_view_save_path(str(path), str(repo)) == str(path)


@pytest.mark.parametrize("trailing_slash", [False, True])
def test_view_alias_resolver_rejects_view_root_symlink(tmp_path, trailing_slash):
    repo = tmp_path / "dotfiles"
    saved = repo / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    view = repo / "view"
    view.symlink_to(saved)
    path = str(view) + (os.sep if trailing_slash else "")

    assert operations.resolve_view_save_path(path, str(repo)) == str(view)


def test_view_rejects_home_conflicts_and_unsafe_sources(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = home / "dotfiles"
    repo.mkdir(parents=True)
    (repo / "files" / HASH).mkdir(parents=True)
    (repo / "files" / HASH / "one").write_text("one")
    (repo / "files" / HASH / "two").write_text("two")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    with pytest.raises(ValueError, match="home itself"):
        operations.plan_view(_config(ONE_KEY, "~"), str(repo))
    with pytest.raises(ValueError, match="duplicate or overlap"):
        operations.plan_view(
            {
                "dotfiles": {
                    ONE_KEY: {"linux": {"path": "~/.config"}},
                    TWO_KEY: {"linux": {"path": "~/.config/app"}},
                }
            },
            str(repo),
        )
    (repo / ".git").mkdir()
    (repo / ".git" / "object").write_text("bad")
    (repo / "files" / "alias").symlink_to(repo / ".git" / "object")
    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        operations.plan_view(_config("alias", "~/item"), str(repo))
    outside = tmp_path / "outside"
    outside.write_text("bad")
    (repo / "files" / "outside-link").symlink_to(outside)
    with pytest.raises(ValueError, match="invalid saved path in dfm.yaml"):
        operations.plan_view(_config("outside-link", "~/item"), str(repo))


def test_view_root_requires_force_and_rejects_non_directories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    view = repo / "view"
    view.mkdir()
    assert "use --force" in operations.validate_view_root(str(repo))
    assert operations.validate_view_root(str(repo), True) is None
    view.rmdir()
    view.write_text("bad")
    assert "real directory" in operations.validate_view_root(str(repo), True)
    view.unlink()
    view.symlink_to(repo / "elsewhere")
    assert "real directory" in operations.validate_view_root(str(repo), True)
    view.unlink()
    os.mkfifo(view)
    assert "real directory" in operations.validate_view_root(str(repo), True)
