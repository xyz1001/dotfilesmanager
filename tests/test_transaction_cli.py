"""End-to-end transaction coverage for each filesystem mutator."""

import os

import pytest

from dotfilesmanager import cli, config, operations, transaction


def _args(command, **values):
    result = {
        "add": False,
        "rm": False,
        "install": False,
        "share": False,
        "view": False,
        "doctor": False,
        "--system": False,
        "--dry-run": False,
        "--force": True,
        "--backup": False,
        "--repair": False,
        "<install_path>": None,
        "<save_path>": None,
        "<path>": None,
    }
    result[command] = True
    result.update(values)
    return result


def _run(monkeypatch, root, arguments):
    monkeypatch.setattr(cli, "docopt", lambda usage: arguments)
    monkeypatch.setattr(cli.config, "default_dotfiles_root", lambda: str(root))
    cli.main()


def test_add_then_rm_are_committed_without_pending_journal(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    item = home / ".item"
    home.mkdir()
    item.write_text("value")
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("add", **{"<install_path>": str(item)}))
    assert item.is_symlink()
    assert not (root / ".dfm-transaction.yaml").exists()
    _run(monkeypatch, root, _args("rm", **{"<path>": str(item)}))
    assert item.read_text() == "value"
    assert config.load_config(str(root)) == {"dotfiles": {}}


def test_share_and_install_commit_as_transactions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    target = home / "shared"
    system = operations.os_name()
    config.save_config(
        str(root), {"dotfiles": {"saved": {"darwin": {"path": "~/old"}}}}
    )
    monkeypatch.setenv("HOME", str(home))

    _run(
        monkeypatch,
        root,
        _args("share", **{"<save_path>": str(saved), "<install_path>": str(target)}),
    )
    assert target.is_symlink()
    target.unlink()
    _run(
        monkeypatch,
        root,
        _args("install", **{"<save_path>": str(saved)}),
    )
    assert target.is_symlink()
    assert (
        config.load_config(str(root))["dotfiles"]["saved"][system]["path"] == "~/shared"
    )


def test_dry_run_does_not_create_root_or_transaction_files(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    root = home / "dotfiles"
    item = home / "item"
    home.mkdir()
    item.write_text("value")
    monkeypatch.setenv("HOME", str(home))

    _run(
        monkeypatch,
        root,
        _args("add", **{"<install_path>": str(item), "--dry-run": True}),
    )
    assert not root.exists()
    assert item.read_text() == "value"
    assert "Dry-run: add" in capsys.readouterr().out


def test_doctor_reports_missing_saved_object_without_repairing_it(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    config.save_config(
        str(root),
        {"dotfiles": {"missing": {operations.os_name(): {"path": "~/x"}}}},
    )
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(SystemExit):
        _run(monkeypatch, root, _args("doctor"))
    assert "missing saved path" in capsys.readouterr().out
    assert not (home / "x").exists()


def test_share_preflight_rejects_outside_home_before_journal(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    root.mkdir()
    (root / "saved").write_text("value")
    config.save_config(str(root), {"dotfiles": {"saved": {}}})
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(SystemExit, match="-1"):
        _run(
            monkeypatch,
            root,
            _args(
                "share",
                **{
                    "<save_path>": str(root / "saved"),
                    "<install_path>": str(tmp_path / "outside"),
                },
            ),
        )
    assert not (root / transaction.JOURNAL).exists()


def test_install_preflight_rejects_missing_batch_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    config.save_config(
        str(root),
        {"dotfiles": {"missing": {operations.os_name(): {"path": "~/target"}}}},
    )
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(SystemExit):
        _run(monkeypatch, root, _args("install"))
    assert not (root / transaction.JOURNAL).exists()


def test_doctor_repairs_only_missing_link(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    install = home / "install"
    config.save_config(
        str(root),
        {"dotfiles": {"saved": {operations.os_name(): {"path": "~/install"}}}},
    )
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("doctor", **{"--repair": True}))
    assert install.is_symlink()
    assert os.path.abspath(os.readlink(install)) == str(saved)


def test_doctor_reports_wrong_dangling_and_unreferenced_objects(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    root.mkdir()
    saved = root / "saved"
    saved.write_text("value")
    wrong = home / "wrong"
    wrong.symlink_to(home / "other")
    dangling = home / "dangling"
    dangling.symlink_to(root / "missing")
    namespace = root / ("a" * 32)
    namespace.mkdir()
    (namespace / "orphan").write_text("orphan")
    (root / "README.md").write_text("repository file")
    (root / ".git").mkdir()
    config.save_config(
        str(root),
        {
            "dotfiles": {
                "saved": {operations.os_name(): {"path": "~/wrong"}},
                "missing": {operations.os_name(): {"path": "~/dangling"}},
            }
        },
    )
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(SystemExit):
        _run(monkeypatch, root, _args("doctor"))
    output = capsys.readouterr().out
    assert "wrong install link" in output
    assert "missing saved path: missing" in output
    assert "dangling install link" in output
    assert f"unreferenced saved object: {namespace.name}/orphan" in output
    assert "README.md" not in output


def test_doctor_repairs_wrong_link_when_no_other_problem(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    root.mkdir()
    saved = root / "saved"
    saved.write_text("value")
    install = home / "install"
    install.symlink_to(home / "wrong")
    config.save_config(
        str(root),
        {"dotfiles": {"saved": {operations.os_name(): {"path": "~/install"}}}},
    )
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("doctor", **{"--repair": True}))
    assert os.path.abspath(os.readlink(install)) == str(saved)


def test_force_rm_preserves_conflicting_destination_in_transaction(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("managed")
    install = home / "install"
    install.write_text("conflict")
    config.save_config(
        str(root),
        {"dotfiles": {"saved": {operations.os_name(): {"path": "~/install"}}}},
    )
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(SystemExit):
        _run(
            monkeypatch,
            root,
            _args("rm", **{"<path>": str(saved), "--force": False}),
        )
    assert install.read_text() == "conflict"
    _run(
        monkeypatch,
        root,
        _args("rm", **{"<path>": str(saved), "--force": True, "--backup": True}),
    )
    assert install.read_text() == "managed"
    assert any((root / transaction.BACKUPS).iterdir())


def test_view_dry_run_writes_nothing_and_force_rebuilds(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    config.save_config(
        str(root), {"dotfiles": {"saved": {operations.os_name(): {"path": "~/item"}}}}
    )
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("view", **{"--dry-run": True}))
    assert not (root / "view").exists()
    _run(monkeypatch, root, _args("view"))
    link = root / "view" / operations.os_name() / "home" / "item"
    assert link.resolve() == saved
    with pytest.raises(SystemExit):
        _run(monkeypatch, root, _args("view", **{"--force": False}))
    _run(monkeypatch, root, _args("view", **{"--force": True}))
    assert link.resolve() == saved
    assert not (root / transaction.JOURNAL).exists()


def test_view_force_dry_run_preserves_existing_tree_and_yaml(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    old_view = root / "view"
    old_view.mkdir()
    (old_view / "keep").write_text("old")
    config.save_config(
        str(root), {"dotfiles": {"saved": {operations.os_name(): {"path": "~/item"}}}}
    )
    config_bytes = (root / "dfm.yaml").read_bytes()
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("view", **{"--dry-run": True, "--force": True}))

    assert (old_view / "keep").read_text() == "old"
    assert (root / "dfm.yaml").read_bytes() == config_bytes
    assert not (root / transaction.JOURNAL).exists()


def test_view_failure_rolls_back_force_rebuild(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    old_view = root / "view"
    old_view.mkdir()
    (old_view / "keep").write_text("old")
    config.save_config(
        str(root), {"dotfiles": {"saved": {operations.os_name(): {"path": "~/item"}}}}
    )
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setattr(
        operations.os,
        "symlink",
        lambda *_, **__: (_ for _ in ()).throw(OSError("boom")),
    )
    with pytest.raises(OSError, match="boom"):
        _run(monkeypatch, root, _args("view", **{"--force": True}))

    assert (old_view / "keep").read_text() == "old"
    assert not (root / transaction.JOURNAL).exists()


def test_view_first_build_failure_rolls_back_absent_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    saved = root / "saved"
    saved.parent.mkdir()
    saved.write_text("value")
    config.save_config(
        str(root), {"dotfiles": {"saved": {operations.os_name(): {"path": "~/item"}}}}
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        operations.os,
        "symlink",
        lambda *_, **__: (_ for _ in ()).throw(OSError("boom")),
    )

    with pytest.raises(OSError, match="boom"):
        _run(monkeypatch, root, _args("view"))

    assert not (root / "view").exists()
    assert not (root / transaction.JOURNAL).exists()


def test_doctor_ignores_generated_view_namespace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dotfiles"
    home.mkdir()
    root.mkdir()
    (root / "view" / "linux").mkdir(parents=True)
    (root / "view" / "linux" / "untracked").write_text("generated")
    config.save_config(str(root), {"dotfiles": {}})
    monkeypatch.setenv("HOME", str(home))

    _run(monkeypatch, root, _args("doctor"))
