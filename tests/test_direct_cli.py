"""Direct command integration coverage."""

import os
import sys
from pathlib import Path

import pytest

from dotfilesmanager import cli, config, operations

HASH = "a" * 32


def _environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = home / "dotfiles"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "default_dotfiles_root", lambda: str(root))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    return home, root


def _run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["dfm", *map(str, arguments)])
    cli.main()


def test_direct_add_then_rm_all(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    install = home / ".item"
    install.write_text("value")

    _run(monkeypatch, "add", install, "--system", "--non-interactive")
    assert install.is_symlink()
    assert (root / "dfm.yaml").exists()

    _run(monkeypatch, "rm", install, "--all", "--force")
    assert install.read_text() == "value"
    assert config.load_config(str(root)) == {"dotfiles": {}}


def test_doctor_fix_preserves_conflicts_and_does_not_follow_orphan_links(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    saved = root / "files" / HASH / "item"
    install = home / ".item"
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    install.write_text("existing")
    external = tmp_path / "external"
    external.write_text("outside")
    orphan = root / "files" / HASH / "orphan"
    orphan.write_text("remove")
    orphan_link = root / "files" / HASH / "orphan-link"
    orphan_link.symlink_to(external)
    config.save_config(
        str(root),
        {"dotfiles": {f"files/{HASH}/item": {"linux": {"path": str(install)}}}},
    )

    with pytest.raises(SystemExit):
        _run(monkeypatch, "doctor", "--fix")

    assert install.read_text() == "existing"
    assert not orphan.exists()
    assert not os.path.lexists(orphan_link)
    assert external.read_text() == "outside"


def test_direct_rm_all_restores_current_and_removes_foreign_registration(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    install = home / ".item"
    saved.write_text("value")
    install.symlink_to(saved)
    config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/saved": {
                    "linux": {"path": str(install)},
                    "darwin": {"path": "~/inaccessible"},
                }
            }
        },
    )

    _run(monkeypatch, "rm", saved, "--all", "--force")

    assert install.read_text() == "value"
    assert not saved.exists()
    assert config.load_config(str(root)) == {"dotfiles": {}}


def test_raw_backslash_key_install_dry_run_preserves_yaml_key(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    install = home / ".item"
    raw_yaml_key = f"{HASH}\\saved"
    (root / "dfm.yaml").write_text(
        f"dotfiles:\n  {raw_yaml_key}:\n    linux:\n      path: {install}\n"
    )
    before = (root / "dfm.yaml").read_bytes()

    _run(monkeypatch, "install", saved, "--dry-run")

    assert not install.exists()
    assert (root / "dfm.yaml").read_bytes() == before
    assert f"files/{HASH}/saved" in config.load_config(str(root))["dotfiles"]


def test_raw_backslash_key_share_and_rm_keep_raw_mapping(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    install = home / ".item"
    raw_yaml_key = f"{HASH}\\saved"
    (root / "dfm.yaml").write_text(
        f"dotfiles:\n  {raw_yaml_key}:\n    linux:\n      path: {install}\n"
    )

    _run(
        monkeypatch,
        "share",
        saved,
        install,
        "--non-interactive",
        "--target=darwin=~/.item",
    )
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: {"darwin"})
    _run(monkeypatch, "rm", saved, "--force")

    keys = config.load_config(str(root))["dotfiles"]
    assert keys[f"files/{HASH}/saved"] == {"linux": {"path": str(install)}}
    assert f"{HASH}/saved:" in (root / "dfm.yaml").read_text()
    assert f"files/{HASH}/saved" not in (root / "dfm.yaml").read_text()


def test_rm_last_foreign_selection_rejects_saved_symlink_parent_before_mutation(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    namespace = "a" * 32
    external = tmp_path / "external"
    external.mkdir()
    saved = root / "files" / namespace / "saved"
    (external / "saved").write_text("value")
    (root / "files").mkdir()
    (root / "files" / namespace).symlink_to(external, target_is_directory=True)
    config.save_config(
        str(root),
        {"dotfiles": {f"files/{namespace}/saved": {"darwin": {"path": "~/x"}}}},
    )
    before = (root / "dfm.yaml").read_bytes()
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: {"darwin"})

    for extra in (("--dry-run",), ()):
        with pytest.raises(SystemExit):
            _run(monkeypatch, "rm", saved, "--force", *extra)
        assert saved.read_text() == "value"
        assert (root / "dfm.yaml").read_bytes() == before


@pytest.mark.parametrize("selection", [set(), {"linux"}])
def test_rm_noop_selection_preserves_config_bytes(tmp_path, monkeypatch, selection):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    systems = {"linux": {"path": str(home / ".item")}}
    if selection == {"linux"}:
        systems = {"darwin": {"path": "~/foreign"}}
    config.save_config(str(root), {"dotfiles": {f"files/{HASH}/saved": systems}})
    before = (root / "dfm.yaml").read_bytes()
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: selection)

    _run(monkeypatch, "rm", saved, "--force")

    assert saved.read_text() == "value"
    assert (root / "dfm.yaml").read_bytes() == before


def test_rm_foreign_selection_saves_changed_config(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    install = home / ".item"
    saved.write_text("value")
    install.symlink_to(saved)
    config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/saved": {
                    "linux": {"path": str(install)},
                    "darwin": {"path": "~/foreign"},
                }
            }
        },
    )
    before = (root / "dfm.yaml").read_bytes()
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: {"darwin"})

    _run(monkeypatch, "rm", saved, "--force")

    assert (root / "dfm.yaml").read_bytes() != before
    assert config.load_config(str(root))["dotfiles"][f"files/{HASH}/saved"] == {
        "linux": {"path": str(install)}
    }


def test_rm_last_foreign_selection_deletes_saved_object(tmp_path, monkeypatch):
    _, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    config.save_config(
        str(root),
        {"dotfiles": {f"files/{HASH}/saved": {"darwin": {"path": "~/foreign"}}}},
    )
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: {"darwin"})

    _run(monkeypatch, "rm", saved, "--force")

    assert not saved.exists()
    assert config.load_config(str(root)) == {"dotfiles": {}}


def test_direct_share_install_and_noninteractive_target_persistence(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    install = home / ".shared"
    stale_view = root / "view"
    stale_view.mkdir()
    (stale_view / "stale").write_text("stale")
    saved = Path(operations.get_save_path(str(install), False, str(root)))
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    rel = operations.save_path_to_key(str(saved), str(root))
    config.save_config(str(root), {"dotfiles": {rel: {}}})

    _run(
        monkeypatch,
        "share",
        saved,
        install,
        "--non-interactive",
        "--target=darwin=~/.shared",
    )
    assert install.is_symlink()
    data = config.load_config(str(root))
    assert data["dotfiles"][rel]["darwin"]["path"] == "~/.shared"
    assert not (stale_view / "stale").exists()

    install.unlink()
    _run(monkeypatch, "install", saved, "--force")
    assert install.is_symlink()


def test_dry_run_and_doctor_missing_root_are_read_only(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    install = home / ".item"
    install.write_text("value")
    _run(monkeypatch, "add", install, "--system", "--non-interactive", "--dry-run")
    assert not root.exists()

    monkeypatch.setattr(sys, "argv", ["dfm", "doctor"])
    with pytest.raises(SystemExit):
        cli.main()
    assert not root.exists()


def test_view_force_and_direct_partial_state_on_config_failure(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = Path(operations.get_save_path(str(home / ".shared"), False, str(root)))
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    install = home / ".shared"
    rel = operations.save_path_to_key(str(saved), str(root))
    config.save_config(str(root), {"dotfiles": {rel: {}}})
    _run(monkeypatch, "share", saved, install, "--non-interactive")
    entries = operations.plan_view(config.load_config(str(root)), str(root))
    assert len(entries) == 1
    assert os.path.islink(entries[0].path)
    _run(monkeypatch, "view")
    with pytest.raises(SystemExit):
        _run(monkeypatch, "view", "--force")

    other = home / ".other"
    other.write_text("other")
    monkeypatch.setattr(
        config, "save_config", lambda *_: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError, match="disk"):
        _run(monkeypatch, "add", other, "--system", "--non-interactive")
    assert other.is_symlink()  # filesystem mutation remains after failed YAML write


def test_direct_1314_guidance_orders_setup_before_repair_and_retry(
    tmp_path, monkeypatch, capsys
):
    home, _ = _environment(tmp_path, monkeypatch)
    install = home / ".item"
    install.write_text("value")
    error = OSError("privilege")
    error.winerror = 1314
    monkeypatch.setattr(
        cli.windows.os, "symlink", lambda *args, **kwargs: (_ for _ in ()).throw(error)
    )

    monkeypatch.setattr(
        sys, "argv", ["dfm", "add", str(install), "--system", "--non-interactive"]
    )
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    assert (
        output.index("dfm setup")
        < output.index("inspect and repair")
        < output.index("retrying")
    )


def test_doctor_reports_broken_configured_entry_without_mutating(
    tmp_path, monkeypatch, capsys
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    install = home / ".broken"
    rel = "files/" + "0" * 32 + "/broken"
    config.save_config(
        str(root), {"dotfiles": {rel: {"linux": {"path": str(install)}}}}
    )
    before = (root / "dfm.yaml").read_bytes()

    monkeypatch.setattr(sys, "argv", ["dfm", "doctor"])
    with pytest.raises(SystemExit):
        cli.main()

    output = capsys.readouterr().out
    assert f"missing saved path: {rel}" in output
    assert f"missing install link: {install}" in output
    assert (root / "dfm.yaml").read_bytes() == before
    assert not install.exists()


def test_doctor_scans_only_unreferenced_objects_in_files_namespace(
    tmp_path, monkeypatch, capsys
):
    home, root = _environment(tmp_path, monkeypatch)
    namespace = "a" * 32
    referenced = root / "files" / namespace / "referenced"
    unreferenced = root / "files" / namespace / "unreferenced"
    legacy = root / namespace / "legacy"
    referenced.parent.mkdir(parents=True)
    referenced.write_text("referenced")
    unreferenced.write_text("unreferenced")
    legacy.parent.mkdir()
    legacy.write_text("legacy")
    install = home / ".referenced"
    install.symlink_to(referenced)
    rel = f"files/{namespace}/referenced"
    config.save_config(
        str(root), {"dotfiles": {rel: {"linux": {"path": str(install)}}}}
    )

    with pytest.raises(SystemExit):
        _run(monkeypatch, "doctor")

    output = capsys.readouterr().out
    assert f"unreferenced saved object: files/{namespace}/unreferenced" in output
    assert f"unreferenced saved object: files/{namespace}/referenced" not in output
    assert f"unreferenced saved object: files/{namespace}/legacy" not in output


def test_doctor_skips_files_root_marked_as_reparse_point(tmp_path, monkeypatch):
    _, root = _environment(tmp_path, monkeypatch)
    files_root = root / "files"
    saved = files_root / ("a" * 32) / "unreferenced"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    config.save_config(str(root), {"dotfiles": {}})
    original = operations._is_link_or_reparse
    monkeypatch.setattr(
        operations,
        "_is_link_or_reparse",
        lambda path: path == str(files_root) or original(path),
    )

    _run(monkeypatch, "doctor")


def test_doctor_skips_reparse_point_hash_namespace(tmp_path, monkeypatch):
    _, root = _environment(tmp_path, monkeypatch)
    namespace = root / "files" / ("a" * 32)
    saved = namespace / "unreferenced"
    saved.parent.mkdir(parents=True)
    saved.write_text("value")
    config.save_config(str(root), {"dotfiles": {}})
    original = operations._is_link_or_reparse
    monkeypatch.setattr(
        operations,
        "_is_link_or_reparse",
        lambda path: path == str(namespace) or original(path),
    )

    _run(monkeypatch, "doctor")


def test_doctor_reports_but_does_not_traverse_reparse_point_descendants(
    tmp_path, monkeypatch, capsys
):
    _, root = _environment(tmp_path, monkeypatch)
    namespace = root / "files" / ("a" * 32)
    unreferenced = namespace / "unreferenced"
    reparse_directory = namespace / "reparse"
    nested = reparse_directory / "nested"
    namespace.mkdir(parents=True)
    unreferenced.write_text("value")
    reparse_directory.mkdir()
    nested.write_text("value")
    config.save_config(str(root), {"dotfiles": {}})
    original = operations._is_link_or_reparse
    monkeypatch.setattr(
        operations,
        "_is_link_or_reparse",
        lambda path: path == str(reparse_directory) or original(path),
    )

    with pytest.raises(SystemExit):
        _run(monkeypatch, "doctor")

    output = capsys.readouterr().out
    assert f"unreferenced saved object: files/{namespace.name}/unreferenced" in output
    assert f"unreferenced saved object: files/{namespace.name}/reparse" in output
    assert (
        f"unreferenced saved object: files/{namespace.name}/reparse/nested"
        not in output
    )


def test_doctor_does_not_report_referenced_reparse_point_descendant(
    tmp_path, monkeypatch, capsys
):
    _, root = _environment(tmp_path, monkeypatch)
    namespace = root / "files" / ("a" * 32)
    reparse_directory = namespace / "reparse"
    nested = reparse_directory / "nested"
    namespace.mkdir(parents=True)
    reparse_directory.mkdir()
    nested.write_text("value")
    rel = f"files/{namespace.name}/reparse"
    config.save_config(str(root), {"dotfiles": {rel: {"darwin": {"path": "~/x"}}}})
    original = operations._is_link_or_reparse
    monkeypatch.setattr(
        operations,
        "_is_link_or_reparse",
        lambda path: path == str(reparse_directory) or original(path),
    )

    _run(monkeypatch, "doctor")
    assert capsys.readouterr().out == "No configuration problems found\n"


def test_add_dry_run_rejects_symlink_root_without_writes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    root = home / "dotfiles"
    root.symlink_to(redirected, target_is_directory=True)
    install = home / ".item"
    install.write_text("value")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "default_dotfiles_root", lambda: str(root))
    monkeypatch.setattr(operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        sys,
        "argv",
        ["dfm", "add", str(install), "--system", "--non-interactive", "--dry-run"],
    )
    with pytest.raises(SystemExit):
        cli.main()

    assert root.is_symlink()
    assert install.read_text() == "value"
    assert not (redirected / "dfm.yaml").exists()


def test_install_rejects_destination_race_without_overwriting(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    install = home / ".race"
    saved = Path(operations.get_save_path(str(install), False, str(root)))
    saved.parent.mkdir(parents=True)
    saved.write_text("saved")
    rel = operations.save_path_to_key(str(saved), str(root))
    config.save_config(
        str(root), {"dotfiles": {rel: {"linux": {"path": str(install)}}}}
    )
    original_state = operations._link_state
    calls = 0

    def race_state(saved_path, install_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            install.write_text("raced")
        return original_state(saved_path, install_path)

    monkeypatch.setattr(operations, "_link_state", race_state)
    monkeypatch.setattr(sys, "argv", ["dfm", "install", str(saved), "--force"])
    with pytest.raises(ValueError, match="changed after install preflight"):
        cli.main()

    assert install.read_text() == "raced"


@pytest.mark.parametrize("relative", [False, True])
def test_direct_install_keeps_correct_link_without_prompt_or_output(
    tmp_path, monkeypatch, capsys, relative
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "files" / HASH / "saved"
    saved.parent.mkdir(parents=True)
    install = home / ".installed"
    saved.write_text("saved")
    target = os.path.relpath(saved, install.parent) if relative else str(saved)
    install.symlink_to(target)
    config.save_config(
        str(root),
        {"dotfiles": {f"files/{HASH}/saved": {"linux": {"path": str(install)}}}},
    )
    monkeypatch.setattr(
        cli,
        "_confirm_replace",
        lambda _: pytest.fail("correct link must not request confirmation"),
    )

    _run(monkeypatch, "install", saved)

    assert os.readlink(install) == target
    assert "Install" not in capsys.readouterr().out


def test_view_paths_resolve_for_rm_install_and_share(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    namespace = "a" * 32
    saved_rm = root / "files" / namespace / "saved-rm"
    saved_install = root / "files" / namespace / "saved-install"
    saved_share = root / "files" / namespace / "saved-share"
    saved_rm.parent.mkdir(parents=True)
    for saved in (saved_rm, saved_install, saved_share):
        saved.write_text(saved.name)
    install_rm = home / ".rm"
    install_install = home / ".install"
    install_share = home / ".share"
    install_rm.symlink_to(saved_rm)
    config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{namespace}/saved-rm": {"linux": {"path": str(install_rm)}},
                f"files/{namespace}/saved-install": {
                    "linux": {"path": str(install_install)}
                },
                f"files/{namespace}/saved-share": {
                    "linux": {"path": str(install_share)}
                },
            }
        },
    )
    view_root = root / "view" / "manual"
    view_root.mkdir(parents=True)
    rm_view = view_root / "rm"
    install_view = view_root / "install"
    share_view = view_root / "share"
    rm_view.symlink_to(os.path.relpath(saved_rm, rm_view.parent))
    install_view.symlink_to(os.path.relpath(saved_install, install_view.parent))
    share_view.symlink_to(saved_share)
    assert operations.resolve_view_save_path(str(install_view), str(root)) == str(
        saved_install
    )

    before_dry_run = (root / "dfm.yaml").read_bytes()
    _run(monkeypatch, "install", install_view, "--dry-run")
    assert not os.path.lexists(install_install)
    assert (root / "dfm.yaml").read_bytes() == before_dry_run
    _run(monkeypatch, "install", install_view, "--force")
    assert install_install.is_symlink()

    _run(
        monkeypatch,
        "share",
        share_view,
        install_share,
        "--non-interactive",
        "--dry-run",
    )
    assert not os.path.lexists(install_share)
    assert (root / "dfm.yaml").read_bytes() == before_dry_run
    _run(monkeypatch, "share", share_view, install_share, "--non-interactive")
    assert install_share.is_symlink()

    _run(monkeypatch, "rm", rm_view, "--all", "--force", "--dry-run")
    assert saved_rm.read_text() == "saved-rm"
    assert install_rm.is_symlink()
    assert (root / "dfm.yaml").read_bytes() == before_dry_run
    _run(monkeypatch, "rm", rm_view, "--all", "--force")
    assert install_rm.read_text() == "saved-rm"
    assert (
        f"files/{namespace}/saved-rm" not in config.load_config(str(root))["dotfiles"]
    )


def test_unmanaged_view_path_remains_rejected(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    config.save_config(str(root), {"dotfiles": {}})
    unmanaged = root / "view" / "linux" / "home" / ".unmanaged"

    with pytest.raises(SystemExit):
        _run(monkeypatch, "install", unmanaged, "--dry-run")
