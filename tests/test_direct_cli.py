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


def test_direct_share_install_and_noninteractive_target_persistence(
    tmp_path, monkeypatch
):
    home, root = _environment(tmp_path, monkeypatch)
    root.mkdir()
    install = home / ".shared"
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
    _run(monkeypatch, "view")
    assert any((root / "view").rglob(".shared"))
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
