"""Tests for CLI dispatch and persistence behavior."""

from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from dotfilesmanager import cli, operations


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
        "--all": False,
        "<install_path>": None,
        "<save_path>": None,
        "<path>": None,
    }
    args[command] = True
    args.update(values)
    return args


def test_main_dispatches_add_saves_then_rebuilds_view_and_renders(monkeypatch, capsys):
    result = operations.OperationResult({"dotfiles": {"saved": {}}}, ["added"])
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
    calls = []
    save = Mock(side_effect=lambda *_: calls.append("save"))
    monkeypatch.setattr(cli.config, "save_config", save)
    mutation_root = Mock(return_value=None)
    monkeypatch.setattr(cli.operations, "validate_view_mutation_root", mutation_root)
    view = Mock(
        side_effect=lambda *_args, **_kwargs: (
            calls.append("view")
            or operations.OperationResult(result.config, ["viewed"])
        )
    )
    monkeypatch.setattr(cli.operations, "view", view)

    cli.main()

    add.assert_called_once_with("/home/item", True, {"dotfiles": {}}, "/repo", {})
    save.assert_called_once_with("/repo", result.config)
    mutation_root.assert_called_once_with("/repo")
    view.assert_called_once_with(result.config, "/repo", force=True)
    assert calls == ["save", "view"]
    assert capsys.readouterr().out == "added\nviewed\n"


def test_auto_view_failure_keeps_saved_config_and_reports_repair(monkeypatch):
    result = operations.OperationResult({"dotfiles": {"saved": {}}}, ["added"])
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
    monkeypatch.setattr(
        cli.operations, "normalize_path", Mock(return_value="/home/item")
    )
    monkeypatch.setattr(cli.operations, "validate_add", Mock(return_value=None))
    monkeypatch.setattr(cli.operations, "add", Mock(return_value=result))
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)
    monkeypatch.setattr(
        cli.operations, "validate_view_mutation_root", Mock(return_value=None)
    )
    rebuild_error = OSError("view failed")
    view = Mock(side_effect=rebuild_error)
    monkeypatch.setattr(cli.operations, "view", view)

    with pytest.raises(RuntimeError, match="configuration was saved") as error:
        cli.main()

    save.assert_called_once_with("/repo", result.config)
    view.assert_called_once_with(result.config, "/repo", force=True)
    assert error.value.__cause__ is rebuild_error
    assert "dfm view to repair" in str(error.value)


def test_auto_view_root_validation_failure_keeps_saved_config(monkeypatch):
    result = operations.OperationResult({"dotfiles": {"saved": {}}}, [])
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
    monkeypatch.setattr(
        cli.operations, "normalize_path", Mock(return_value="/home/item")
    )
    monkeypatch.setattr(cli.operations, "validate_add", Mock(return_value=None))
    monkeypatch.setattr(cli.operations, "add", Mock(return_value=result))
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)
    mutation_root = Mock(return_value="unsafe view root")
    monkeypatch.setattr(cli.operations, "validate_view_mutation_root", mutation_root)
    view = Mock()
    monkeypatch.setattr(cli.operations, "view", view)

    with pytest.raises(RuntimeError, match="configuration was saved") as error:
        cli.main()

    save.assert_called_once_with("/repo", result.config)
    mutation_root.assert_called_once_with("/repo")
    view.assert_not_called()
    assert isinstance(error.value.__cause__, ValueError)


def test_auto_view_privilege_error_uses_setup_guidance(monkeypatch, capsys):
    result = operations.OperationResult({"dotfiles": {"saved": {}}}, [])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "windows")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(
            return_value=_args("add", **{"<install_path>": "~/item", "--system": True})
        ),
    )
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value={"dotfiles": {}}))
    monkeypatch.setattr(
        cli.operations, "normalize_path", Mock(return_value="/home/item")
    )
    monkeypatch.setattr(cli.operations, "validate_add", Mock(return_value=None))
    monkeypatch.setattr(cli.operations, "add", Mock(return_value=result))
    monkeypatch.setattr(cli.config, "save_config", Mock())
    monkeypatch.setattr(
        cli.operations, "validate_view_mutation_root", Mock(return_value=None)
    )
    monkeypatch.setattr(
        cli.operations, "view", Mock(side_effect=cli.windows.SymlinkPrivilegeError())
    )

    with pytest.raises(SystemExit):
        cli.main()

    assert "Run dfm setup" in capsys.readouterr().out


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


def test_target_wizard_adapter_choices_custom_retry_confirmation_and_dry_run(
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
    assert path_list.message == "Target path for macOS"
    assert path_list.default == "~/.config/app"
    assert [(choice.label, choice.value) for choice in path_list.choices] == [
        ("~/.config/app", "~/.config/app"),
        ("~/Library/Application Support/app", "~/Library/Application Support/app"),
        ("Custom path", cli._CUSTOM),
    ]
    validator = prompts.call_args_list[3].args[0][0]._validate
    assert prompts.call_args_list[3].args[0][0].message == "Custom path for Windows"
    assert validator({}, "~/custom") is True
    assert validator({}, "   ") is True
    with pytest.raises(cli.ValidationError):
        validator({}, "not-home")

    with pytest.raises(cli.ValidationError) as error:
        validator({}, "~/dotfiles/nope")
    assert error.value.reason == "target path cannot be in ~/dotfiles"
    confirm = prompts.call_args_list[4].args[0][0]
    assert confirm.default is False

    prompts = Mock(
        side_effect=[
            {"systems": ["darwin", "windows"]},
            {"path": "~/item"},
            {"path": cli._CUSTOM},
            {"custom_path": "   "},
            {"path": "~/item"},
            {"confirm": True},
        ]
    )
    monkeypatch.setattr(cli, "_prompt_targets", prompts)
    assert cli._target_wizard("~/item", {}) == {
        "darwin": "~/item",
        "windows": "~/item",
    }
    retried_list = prompts.call_args_list[4].args[0][0]
    assert retried_list.default == "~/item"
    assert retried_list.message == "Target path for Windows"

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


def test_docopt_parses_rm_all():
    args = cli.docopt(cli.USAGE, argv=["rm", "~/item", "--all"])
    assert args["--all"] is True


def test_remove_selector_lists_registered_systems_and_defaults_current(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    prompt = Mock(return_value={"systems": ["custom", "darwin"]})
    monkeypatch.setattr(cli, "_prompt_targets", prompt)

    selected = cli._select_remove_systems(
        _args("rm"), {"linux": {}, "darwin": {}, "custom": {}}
    )

    assert selected == {"darwin", "custom"}
    checkbox = prompt.call_args.args[0][0]
    assert checkbox.message == "Select systems to remove"
    assert checkbox.default == ["linux"]
    assert [(choice.label, choice.value) for choice in checkbox.choices] == [
        ("Linux", "linux"),
        ("macOS", "darwin"),
        ("custom", "custom"),
    ]


def test_remove_selector_cancellation_and_empty_selection_are_distinct(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    prompt = Mock(side_effect=[None, {"systems": []}])
    monkeypatch.setattr(cli, "_prompt_targets", prompt)

    assert cli._select_remove_systems(_args("rm"), {"linux": {}}) is None
    assert cli._select_remove_systems(_args("rm"), {"linux": {}}) == set()


def test_remove_selector_bypasses_prompt_for_non_tty_and_all(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    prompt = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr(cli, "_prompt_targets", prompt)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    assert cli._select_remove_systems(_args("rm"), {"darwin": {}}) == {"linux"}
    assert cli._select_remove_systems(
        _args("rm", **{"--all": True}), {"linux": {}, "darwin": {}}
    ) == {"linux", "darwin"}
    prompt.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_foreign_selection_skips_current_preflight(monkeypatch, dry_run):
    result = operations.OperationResult({"dotfiles": {}}, [])
    dotfiles_config = {
        "dotfiles": {"saved": {"linux": {"path": "/install"}, "custom": {}}}
    }
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(return_value=_args("rm", **{"<path>": "path", "--dry-run": dry_run})),
    )
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "validate_config", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/path"))
    monkeypatch.setattr(
        cli.operations, "resolve_view_save_path", Mock(return_value="/repo/saved")
    )
    monkeypatch.setattr(cli.operations, "validate_remove", Mock(return_value=None))
    destination = Mock(return_value=None)
    monkeypatch.setattr(cli.operations, "validate_remove_destination", destination)
    paths = Mock(return_value=None)
    monkeypatch.setattr(cli.operations, "validate_mutation_paths", paths)
    monkeypatch.setattr(cli, "_select_remove_systems", Mock(return_value={"custom"}))
    remove = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "remove", remove)
    monkeypatch.setattr(cli.config, "save_config", Mock())
    monkeypatch.setattr(
        cli.operations,
        "view",
        Mock(return_value=operations.OperationResult(result.config)),
    )

    cli.main()

    destination.assert_not_called()
    paths.assert_called_once_with([], "/repo")
    if dry_run:
        remove.assert_not_called()
    else:
        assert remove.call_args.kwargs["selected_systems"] == {"custom"}


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_all_with_only_foreign_registration_validates_saved_path(
    monkeypatch, dry_run
):
    result = operations.OperationResult({"dotfiles": {}}, [])
    dotfiles_config = {"dotfiles": {"saved": {"darwin": {}}}}
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(
        cli,
        "docopt",
        Mock(
            return_value=_args(
                "rm", **{"<path>": "path", "--all": True, "--dry-run": dry_run}
            )
        ),
    )
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "validate_config", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/path"))
    monkeypatch.setattr(
        cli.operations, "resolve_view_save_path", Mock(return_value="/repo/saved")
    )
    monkeypatch.setattr(cli.operations, "validate_remove", Mock(return_value=None))
    paths = Mock(return_value=None)
    monkeypatch.setattr(cli.operations, "validate_mutation_paths", paths)
    monkeypatch.setattr(cli, "_select_remove_systems", Mock(return_value={"darwin"}))
    remove = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "remove", remove)
    monkeypatch.setattr(cli.config, "save_config", Mock())
    monkeypatch.setattr(
        cli.operations,
        "view",
        Mock(return_value=operations.OperationResult(result.config)),
    )

    cli.main()

    paths.assert_called_once_with(["/repo/saved"], "/repo")
    if dry_run:
        remove.assert_not_called()
    else:
        remove.assert_called_once()


@pytest.mark.parametrize(
    ("command", "values", "expected"),
    [
        ("rm", {"<path>": "path", "--all": True}, ("remove", ("/path",))),
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
    result = operations.OperationResult({"dotfiles": {"changed": {}}}, ["changed"])
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
    view = Mock(return_value=operations.OperationResult(result.config, ["viewed"]))
    monkeypatch.setattr(cli.operations, "view", view)

    cli.main()

    operation, paths = expected
    if operation == "remove":
        cli.operations.validate_remove.assert_called_once_with(
            "/path", "/repo", "/path"
        )
        remove.assert_called_once_with(
            *paths,
            dotfiles_config,
            "/repo",
            False,
            True,
            "/path",
            selected_systems=set(),
        )
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
    if command in ("rm", "share"):
        save.assert_called_once_with("/repo", result.config)
        view.assert_called_once_with(result.config, "/repo", force=True)
    else:
        save.assert_not_called()
        view.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", False), ("n", False)],
)
def test_confirm_replace_only_accepts_y(monkeypatch, answer, expected):
    prompt = Mock(return_value=answer)
    monkeypatch.setattr("builtins.input", prompt)

    assert cli._confirm_replace("/existing") is expected
    prompt.assert_called_once_with("文件 /existing 已存在，是否替换？(y/N)")


def test_main_dispatches_on_windows_without_administrator_gate(monkeypatch):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "windows")
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args("doctor")))
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    doctor = Mock()
    monkeypatch.setattr(cli, "_doctor", doctor)

    cli.main()

    doctor.assert_called_once_with("/repo")


def test_only_symlink_privilege_error_gets_setup_guidance(monkeypatch, capsys):
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args("doctor")))
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(
        cli, "_doctor", Mock(side_effect=cli.windows.SymlinkPrivilegeError())
    )

    with pytest.raises(SystemExit):
        cli.main()

    output = capsys.readouterr().out
    assert "Run dfm setup, then inspect and repair" in output
    assert output.index("dfm setup") < output.index("before retrying")


def test_unrelated_symlink_error_remains_native(monkeypatch, capsys):
    monkeypatch.setattr(cli, "docopt", Mock(return_value=_args("doctor")))
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli, "_doctor", Mock(side_effect=OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        cli.main()

    assert "Run dfm setup" not in capsys.readouterr().out


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

    view.assert_called_once_with(dotfiles_config, "/repo", force=True)
    save.assert_not_called()
