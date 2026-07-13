"""Tests for CLI dispatch and persistence behavior."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dotfilesmanager import cli, operations


@pytest.fixture(autouse=True)
def no_real_transactions(monkeypatch):
    """Dispatch tests exercise the adapter, not its on-disk transaction layer."""

    class Lock:
        def __init__(self, root):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Transaction:
        def __init__(self, *args):
            pass

        def begin(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(cli.transaction, "ProcessLock", Lock)
    monkeypatch.setattr(cli.transaction, "Transaction", Transaction)


def _args(command, **values):
    args = {"add": False, "rm": False, "install": False, "share": False, "view": False}
    args[command] = True
    args.update(values)
    return args


def test_main_dispatches_add_renders_and_saves(monkeypatch):
    result = operations.OperationResult({"dotfiles": {}}, ["added"])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(
            return_value=_args("add", **{"<install_path>": "~/item", "--system": True})
        ),
    )
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value={"dotfiles": {}}))
    normalize = Mock(return_value="/home/item")
    monkeypatch.setattr(cli.operations, "normalize_path", normalize)
    monkeypatch.setattr(cli.operations, "validate_add", Mock(return_value=None))
    add = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "add", add)
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    cli.main()

    add.assert_called_once_with("/home/item", True, {"dotfiles": {}}, "/repo")
    save.assert_called_once_with("/repo", result.config)


def test_main_exits_on_validation_failure_without_saving(monkeypatch, capsys):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(return_value=_args("add", **{"<install_path>": "bad", "--system": False})),
    )
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value={"dotfiles": {}}))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/bad"))
    monkeypatch.setattr(
        cli.operations, "validate_add", Mock(return_value="invalid path")
    )
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == -1
    assert capsys.readouterr().out == "invalid path\n"
    save.assert_not_called()


@pytest.mark.parametrize(
    ("command", "values", "expected"),
    [
        ("rm", {"<path>": "path"}, ("remove", ("/path",))),
        ("install", {"<save_path>": None}, ("install", (None,))),
        ("install", {"<save_path>": "save"}, ("install", ("/save",))),
        (
            "share",
            {"<save_path>": "save", "<install_path>": "install"},
            ("share", ("/save", "/install")),
        ),
    ],
)
def test_main_dispatches_remaining_commands_and_saves(
    monkeypatch, command, values, expected
):
    result = operations.OperationResult({"dotfiles": {}}, [])
    dotfiles_config = {"dotfiles": {}}
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args(command, **values)))
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(
        cli.operations,
        "normalize_path",
        Mock(side_effect=lambda path: None if path is None else f"/{path}"),
    )
    monkeypatch.setattr(cli.operations, "validate_remove", Mock(return_value=None))
    monkeypatch.setattr(
        cli.operations, "validate_saved_object", Mock(return_value=None)
    )
    monkeypatch.setattr(
        cli.operations, "validate_install_target", Mock(return_value=None)
    )
    monkeypatch.setattr(
        cli.operations, "validate_install_sources", Mock(return_value=None)
    )
    monkeypatch.setattr(
        cli.operations, "validate_remove_destination", Mock(return_value=None)
    )
    remove = Mock(return_value=result)
    install = Mock(return_value=result)
    share = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "remove", remove)
    monkeypatch.setattr(cli.operations, "install", install)
    monkeypatch.setattr(cli.operations, "share", share)
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    cli.main()

    operation, paths = expected
    if operation == "remove":
        cli.operations.validate_remove.assert_called_once_with("/path", "/repo")
        remove.assert_called_once_with(*paths, dotfiles_config, "/repo", False)
        install.assert_not_called()
        share.assert_not_called()
    elif operation == "install":
        install.assert_called_once_with(
            *paths, dotfiles_config, "/repo", cli._confirm_replace
        )
        remove.assert_not_called()
        share.assert_not_called()
    else:
        share.assert_called_once_with(
            *paths, dotfiles_config, "/repo", cli._confirm_replace
        )
        remove.assert_not_called()
        install.assert_not_called()
    if command == "share":
        # A no-op/rejected share must not rewrite YAML.
        save.assert_not_called()
    else:
        save.assert_called_once_with("/repo", result.config)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", False), ("n", False)],
)
def test_confirm_replace_only_accepts_y(monkeypatch, answer, expected):
    prompt = Mock(return_value=answer)
    monkeypatch.setattr("builtins.input", prompt)

    assert cli._confirm_replace("/existing") is expected
    prompt.assert_called_once_with("文件 /existing 已存在，是否替换？(y/N)")


def test_main_blocks_non_administrator_on_windows(monkeypatch, capsys):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        cli,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 0))
        ),
    )
    parse = Mock()
    monkeypatch.setattr(cli, "docopt", parse)

    cli.main()

    assert "Administrator priviledges" in capsys.readouterr().out
    parse.assert_not_called()


def test_view_dispatches_without_saving_configuration(monkeypatch):
    result = operations.OperationResult({"dotfiles": {"changed": {}}}, ["viewed"])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args("view")))
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    dotfiles_config = {"dotfiles": {}}
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "plan_view", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "validate_view_root", Mock(return_value=None))
    view = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "view", view)
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    cli.main()

    view.assert_called_once_with(dotfiles_config, "/repo", False)
    save.assert_not_called()
