"""Tests for CLI dispatch and persistence behavior."""

from types import SimpleNamespace
from unittest.mock import ANY, Mock

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
    args = {
        "add": False,
        "rm": False,
        "install": False,
        "share": False,
        "view": False,
        "doctor": False,
        "--system": False,
        "--non-interactive": False,
        "--target": [],
        "--dry-run": False,
        "--force": False,
        "--backup": False,
        "--repair": False,
        "<install_path>": None,
        "<save_path>": None,
        "<path>": None,
    }
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

    add.assert_called_once_with("/home/item", True, {"dotfiles": {}}, "/repo", {})
    save.assert_called_once_with("/repo", result.config)


def test_main_exits_on_validation_failure_without_saving(monkeypatch, capsys):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(
            return_value=_args(
                "add", **{"<install_path>": "bad", "--non-interactive": True}
            )
        ),
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
    ("command", "values"),
    [
        ("add", {"<install_path>": "x"}),
        ("share", {"<save_path>": "saved", "<install_path>": "target"}),
    ],
)
def test_non_tty_target_commands_require_noninteractive_without_reading_stdin(
    monkeypatch, command, values
):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args(command, **values)))
    load = Mock()
    monkeypatch.setattr(cli.config, "load_config", load)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    prompt = Mock(side_effect=AssertionError("stdin must not be read"))
    monkeypatch.setattr("builtins.input", prompt)
    target_prompt = Mock(side_effect=AssertionError("wizard must not run"))
    monkeypatch.setattr(cli, "_prompt_targets", target_prompt)

    with pytest.raises(SystemExit):
        cli.main()

    load.assert_not_called()
    prompt.assert_not_called()
    target_prompt.assert_not_called()


def test_platform_specific_share_rejects_target_without_running_wizard(monkeypatch):
    rel = "a" * 32 + "/linux/item"
    args = _args(
        "share",
        **{
            "--non-interactive": True,
            "--target": ["darwin=~/x"],
            "_saved": "/repo/" + rel,
        },
    )
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    wizard = Mock()
    monkeypatch.setattr(cli, "_target_wizard", wizard)

    with pytest.raises(SystemExit):
        cli._select_targets(
            args, "share", "/home/item", {"dotfiles": {rel: {}}}, "/repo", False
        )

    wizard.assert_not_called()


def test_target_wizard_adapter_choices_custom_skip_confirmation_and_dry_run(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    prompts = Mock(
        side_effect=[
            {"systems": ["windows", "darwin"]},
            {"path": "~/.config/app"},
            {"path": cli._CUSTOM},
            {"custom_path": "~/custom"},
            {"confirm": True},
        ]
    )
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/.config/app", {}) == {
        "darwin": "~/.config/app",
        "windows": "~/custom",
    }
    checkbox = prompts.call_args_list[0].args[0][0]
    assert checkbox.default == ["linux"]
    assert checkbox.locked == ["linux"]
    assert [(choice.label, choice.value) for choice in checkbox.choices] == [
        ("Linux", "linux"),
        ("macOS", "darwin"),
        ("Windows", "windows"),
        ("Android (Termux)", "android"),
    ]
    path_list = prompts.call_args_list[1].args[0][0]
    assert path_list.default == cli._SKIP
    validator = prompts.call_args_list[3].args[0][0]._validate
    assert validator({}, "~/custom") is True
    with pytest.raises(cli.ValidationError):
        validator({}, "not-home")

    with pytest.raises(cli.ValidationError) as error:
        validator({}, "~/dotfiles/nope")
    assert error.value.reason == "target path cannot be in ~/dotfiles"
    confirm = prompts.call_args_list[4].args[0][0]
    assert confirm.default is False

    prompts = Mock(side_effect=[{"systems": ["darwin"]}, {"path": cli._SKIP}])
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) == {}

    monkeypatch.setattr(cli, "_prompt_targets", Mock(return_value=None))
    assert cli._target_wizard("~/item", {}) is None

    prompts = Mock(side_effect=[{"systems": ["darwin"]}, {"path": "~/item"}])
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}, dry_run=True) == {"darwin": "~/item"}
    assert prompts.call_count == 2
    assert "Target plan: darwin=~/item" in capsys.readouterr().out


@pytest.mark.parametrize("current", operations.SUPPORTED_SYSTEMS)
def test_target_wizard_filters_systems_and_does_not_prompt_when_none(
    monkeypatch, current
):
    monkeypatch.setattr(cli.operations, "os_name", lambda: current)
    prompts = Mock(return_value={"systems": []})
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) == {}
    choices = prompts.call_args.args[0][0].choices
    assert [choice.value for choice in choices] == [
        current,
        *[system for system in operations.SUPPORTED_SYSTEMS if system != current],
    ]
    assert prompts.call_args.args[0][0].default == [current]
    assert prompts.call_args.args[0][0].locked == [current]
    configured = {
        system: {"path": "~/x"}
        for system in operations.SUPPORTED_SYSTEMS
        if system != current
    }
    prompts.reset_mock()
    assert cli._target_wizard("~/item", configured) == {}
    prompts.assert_called_once()
    current_only = prompts.call_args.args[0][0]
    assert [(choice.label, choice.value) for choice in current_only.choices] == [
        (cli._SYSTEM_LABELS[current], current)
    ]
    assert current_only.default == [current]
    assert current_only.locked == [current]


def test_target_wizard_filters_partially_configured_platforms(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    prompts = Mock(return_value={"systems": []})
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {"darwin": {"path": "~/old"}}) == {}
    choices = prompts.call_args.args[0][0].choices
    assert [(choice.label, choice.value) for choice in choices] == [
        ("Linux", "linux"),
        ("Windows", "windows"),
        ("Android (Termux)", "android"),
    ]


def test_target_wizard_ignores_locked_current_selection(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "android")
    prompts = Mock(return_value={"systems": ["android"]})
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) == {}
    prompts.assert_called_once()


def test_target_wizard_uses_unknown_current_key_as_display_label(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "plan9")
    prompts = Mock(return_value={"systems": ["plan9"]})
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) == {}
    choice = prompts.call_args.args[0][0].choices[0]
    assert (choice.label, choice.value) == ("plan9", "plan9")


@pytest.mark.parametrize(
    "answers",
    [
        [{"systems": ["darwin"]}, {}],
        [{"systems": ["darwin"]}, {"path": cli._CUSTOM}, {}],
        [{"systems": ["darwin"]}, {"path": "~/item"}, {}],
    ],
)
def test_target_wizard_treats_missing_list_text_or_confirm_answer_as_cancelled(
    monkeypatch, answers
):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    prompts = Mock(side_effect=answers)
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) is None


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_prompt_adapter_converts_terminal_cancellation_to_none(monkeypatch, error):
    monkeypatch.setattr(cli.inquirer, "prompt", Mock(side_effect=error))
    assert cli._prompt_targets([]) is None


def test_docopt_parses_repeated_targets():
    args = cli.docopt(
        cli.USAGE,
        argv=[
            "add",
            "~/item",
            "--non-interactive",
            "--target=darwin=~/a",
            "--target=windows=~/b",
        ],
    )
    assert args["--target"] == ["darwin=~/a", "windows=~/b"]


@pytest.mark.parametrize(
    ("command", "values", "expected"),
    [
        ("rm", {"<path>": "path"}, ("remove", ("/path",))),
        ("install", {"<save_path>": None}, ("install", (None,))),
        ("install", {"<save_path>": "save"}, ("install", ("/save",))),
        (
            "share",
            {
                "<save_path>": "save",
                "<install_path>": "install",
                "--non-interactive": True,
            },
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
        assert install.call_args.args[:2] == (*paths, dotfiles_config)
        assert install.call_args.args[2] == "/repo"
        remove.assert_not_called()
        share.assert_not_called()
    else:
        share.assert_called_once_with(
            *paths, dotfiles_config, "/repo", ANY, {}, "missing"
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
