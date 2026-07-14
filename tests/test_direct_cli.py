"""Direct command integration coverage."""

import os
import sys

import pytest

from dotfilesmanager import cli, config, operations


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


def test_direct_rm_all_restores_current_and_removes_foreign_registration(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "saved"
    install = home / ".item"
    saved.write_text("value")
    install.symlink_to(saved)
    config.save_config(
        str(root),
        {
            "dotfiles": {
                "saved": {
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


def test_rm_last_foreign_selection_rejects_saved_symlink_parent_before_mutation(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    namespace = "a" * 32
    external = tmp_path / "external"
    external.mkdir()
    saved = root / namespace / "saved"
    (external / "saved").write_text("value")
    (root / namespace).symlink_to(external, target_is_directory=True)
    config.save_config(
        str(root), {"dotfiles": {f"{namespace}/saved": {"darwin": {"path": "~/x"}}}}
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
    saved = root / "saved"
    saved.write_text("value")
    systems = {"linux": {"path": str(home / ".item")}}
    if selection == {"linux"}:
        systems = {"darwin": {"path": "~/foreign"}}
    config.save_config(str(root), {"dotfiles": {"saved": systems}})
    before = (root / "dfm.yaml").read_bytes()
    monkeypatch.setattr(cli, "_select_remove_systems", lambda *_: selection)

    _run(monkeypatch, "rm", saved, "--force")

    assert saved.read_text() == "value"
    assert (root / "dfm.yaml").read_bytes() == before


def test_rm_foreign_selection_saves_changed_config(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "saved"
    install = home / ".item"
    saved.write_text("value")
    install.symlink_to(saved)
    config.save_config(
        str(root),
        {
            "dotfiles": {
                "saved": {
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
    assert config.load_config(str(root))["dotfiles"]["saved"] == {
        "linux": {"path": str(install)}
    }


def test_rm_last_foreign_selection_deletes_saved_object(tmp_path, monkeypatch):
    _, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    saved = root / "saved"
    saved.write_text("value")
    config.save_config(
        str(root), {"dotfiles": {"saved": {"darwin": {"path": "~/foreign"}}}}
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
    saved = root / os.path.relpath(
        operations.get_save_path(str(install), False, str(root)), root
    )
    saved.parent.mkdir()
    saved.write_text("value")
    rel = os.path.relpath(saved, root).replace(os.sep, "/")
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
    saved = root / os.path.relpath(
        operations.get_save_path(str(home / ".shared"), False, str(root)), root
    )
    saved.parent.mkdir()
    saved.write_text("value")
    install = home / ".shared"
    rel = os.path.relpath(saved, root).replace(os.sep, "/")
    config.save_config(str(root), {"dotfiles": {rel: {}}})
    _run(monkeypatch, "share", saved, install, "--non-interactive")
    entries = operations.plan_view(config.load_config(str(root)), str(root))
    assert len(entries) == 1
    assert os.path.islink(entries[0].path)
    with pytest.raises(SystemExit):
        _run(monkeypatch, "view")
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
    rel = "0" * 32 + "/broken"
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
    saved = root / os.path.relpath(
        operations.get_save_path(str(install), False, str(root)), root
    )
    saved.parent.mkdir()
    saved.write_text("saved")
    rel = os.path.relpath(saved, root).replace(os.sep, "/")
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
    saved = root / "saved"
    install = home / ".installed"
    saved.write_text("saved")
    target = os.path.relpath(saved, install.parent) if relative else str(saved)
    install.symlink_to(target)
    config.save_config(
        str(root), {"dotfiles": {"saved": {"linux": {"path": str(install)}}}}
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
    saved_rm = root / namespace / "saved-rm"
    saved_install = root / namespace / "saved-install"
    saved_share = root / namespace / "saved-share"
    saved_rm.parent.mkdir()
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
                f"{namespace}/saved-rm": {"linux": {"path": str(install_rm)}},
                f"{namespace}/saved-install": {"linux": {"path": str(install_install)}},
                f"{namespace}/saved-share": {"linux": {"path": str(install_share)}},
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
    assert f"{namespace}/saved-rm" not in config.load_config(str(root))["dotfiles"]


def test_unmanaged_view_path_remains_rejected(tmp_path, monkeypatch):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    config.save_config(str(root), {"dotfiles": {}})
    unmanaged = root / "view" / "linux" / "home" / ".unmanaged"

    with pytest.raises(SystemExit):
        _run(monkeypatch, "install", unmanaged, "--dry-run")
