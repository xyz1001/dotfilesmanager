"""Tests for CLI dispatch and persistence behavior."""

from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
from click.testing import CliRunner

from dotfilesmanager import cli, operations

HASH = "a" * 32
SAVED_KEY = f"files/{HASH}/saved"


def _args(command, **values):
    args = {
        "add": False,
        "rm": False,
        "install": False,
        "share": False,
        "view": False,
        "doctor": False,
        "--system": False,
        "--encrypt": False,
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


def test_load_config_error_is_reported_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.config, "load_config", Mock(side_effect=ValueError("bad yaml"))
    )

    with pytest.raises(SystemExit):
        cli._load_config("/repo")

    assert capsys.readouterr().out == "Invalid configuration: bad yaml\n"


def test_command_reports_invalid_utf8_config_without_traceback(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "dotfiles"
    root.mkdir()
    (root / "dfm.yaml").write_bytes(b"\xff")
    monkeypatch.setattr(
        cli.config, "default_dotfiles_root", Mock(return_value=str(root))
    )

    with pytest.raises(SystemExit):
        cli._run_parsed_command(_args("view", **{"--dry-run": True}))

    output = capsys.readouterr().out
    assert "Invalid configuration: invalid dfm.yaml encoding; expected UTF-8" in output
    assert "Traceback" not in output


def test_main_dispatches_add_saves_then_rebuilds_view_and_renders(monkeypatch, capsys):
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, ["added"])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
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

    cli._run_parsed_command(
        _args(
            "add",
            **{"<install_path>": "~/item", "--system": True, "--encrypt": True},
        )
    )

    add.assert_called_once_with(
        "/home/item", True, {"dotfiles": {}}, "/repo", {}, encrypt=True
    )
    save.assert_called_once_with("/repo", result.config)
    mutation_root.assert_called_once_with("/repo")
    view.assert_called_once_with(result.config, "/repo", force=True)
    assert calls == ["save", "view"]
    assert capsys.readouterr().out == "added\nviewed\n"


def test_auto_view_failure_keeps_saved_config_and_reports_repair(monkeypatch):
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, ["added"])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
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
        cli._run_parsed_command(
            _args("add", **{"<install_path>": "~/item", "--system": True})
        )

    save.assert_called_once_with("/repo", result.config)
    view.assert_called_once_with(result.config, "/repo", force=True)
    assert error.value.__cause__ is rebuild_error
    assert "dfm view to repair" in str(error.value)


def test_auto_view_root_validation_failure_keeps_saved_config(monkeypatch):
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, [])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
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
        cli._run_parsed_command(
            _args("add", **{"<install_path>": "~/item", "--system": True})
        )

    save.assert_called_once_with("/repo", result.config)
    mutation_root.assert_called_once_with("/repo")
    view.assert_not_called()
    assert isinstance(error.value.__cause__, ValueError)


def test_auto_view_privilege_error_uses_setup_guidance(monkeypatch, capsys):
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, [])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "windows")
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
    monkeypatch.setattr(cli.sys, "argv", ["dfm", "add", "~/item", "--system"])

    with pytest.raises(SystemExit):
        cli.main()

    assert "Run dfm setup" in capsys.readouterr().out


def test_main_exits_on_validation_failure_without_saving(monkeypatch, capsys):
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value={"dotfiles": {}}))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/bad"))
    monkeypatch.setattr(
        cli.operations, "validate_add", Mock(return_value="invalid path")
    )
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    with pytest.raises(SystemExit) as error:
        cli._run_parsed_command(
            _args("add", **{"<install_path>": "bad", "--non-interactive": True})
        )

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
    load = Mock()
    monkeypatch.setattr(cli.config, "load_config", load)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    prompt = Mock(side_effect=AssertionError("stdin must not be read"))
    monkeypatch.setattr("builtins.input", prompt)
    target_prompt = Mock(side_effect=AssertionError("wizard must not run"))
    monkeypatch.setattr(cli, "_prompt_targets", target_prompt)

    with pytest.raises(SystemExit):
        cli._run_parsed_command(_args(command, **values))

    load.assert_not_called()
    prompt.assert_not_called()
    target_prompt.assert_not_called()


def test_platform_specific_share_rejects_target_without_running_wizard(monkeypatch):
    rel = "a" * 32 + "/linux/item"
    key = "files/" + rel
    args = _args(
        "share",
        **{
            "--non-interactive": True,
            "--target": ["darwin=~/x"],
            "_saved": "/repo/files/" + rel,
        },
    )
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    wizard = Mock()
    monkeypatch.setattr(cli, "_target_wizard", wizard)

    with pytest.raises(SystemExit):
        cli._select_targets(
            args, "share", "/home/item", {"dotfiles": {key: {}}}, "/repo", False
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


def _install_prompt_fixture(tmp_path, names):
    root = tmp_path / "repo"
    saved_dir = root / "files" / HASH
    saved_dir.mkdir(parents=True)
    config = {"dotfiles": {}}
    paths = {}
    for name in names:
        key = f"files/{HASH}/{name}"
        saved = saved_dir / name
        saved.write_text(name)
        install = tmp_path / "home" / name
        install.parent.mkdir(exist_ok=True)
        config["dotfiles"][key] = {cli.operations.os_name(): {"path": str(install)}}
        paths[key] = (saved, install)
    return root, config, paths


def test_install_prompt_uses_one_checkbox_for_conflicting_destinations(
    tmp_path, monkeypatch
):
    root, config, paths = _install_prompt_fixture(tmp_path, ["conflict"])
    wrong_target = tmp_path / "wrong-target"
    wrong_target.write_text("existing")
    paths["files/" + HASH + "/conflict"][1].symlink_to(wrong_target)
    prompt = Mock(return_value={"paths": ["files/" + HASH + "/conflict"]})
    monkeypatch.setattr(cli, "_prompt_targets", prompt)

    approved = cli._preconfirm_install(None, config, str(root), False)

    assert approved == {"files/" + HASH + "/conflict": "conflict"}
    prompt.assert_called_once()
    checkbox = prompt.call_args.args[0][0]
    assert checkbox.message == "Select destination paths to replace"
    assert [(choice.label, choice.value) for choice in checkbox.choices] == [
        (str(paths["files/" + HASH + "/conflict"][1]), "files/" + HASH + "/conflict")
    ]


def test_install_prompt_auto_approves_missing_dangling_and_sync_without_checkbox(
    tmp_path, monkeypatch
):
    root, config, paths = _install_prompt_fixture(
        tmp_path, ["missing", "dangling", "sync"]
    )
    dangling = paths["files/" + HASH + "/dangling"][1]
    dangling.symlink_to(tmp_path / "not-there")
    paths["files/" + HASH + "/sync"][1].write_text("existing")
    prompt = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr(cli, "_prompt_targets", prompt)

    approved = cli._preconfirm_install(None, config, str(root), False)

    assert approved == {
        "files/" + HASH + "/missing": "missing",
        "files/" + HASH + "/dangling": "dangling",
        "files/" + HASH + "/sync": "sync",
    }
    prompt.assert_not_called()


def test_install_force_approves_conflicts_without_checkbox(tmp_path, monkeypatch):
    root, config, paths = _install_prompt_fixture(tmp_path, ["conflict"])
    wrong_target = tmp_path / "wrong-target"
    wrong_target.write_text("existing")
    paths["files/" + HASH + "/conflict"][1].symlink_to(wrong_target)
    prompt = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr(cli, "_prompt_targets", prompt)

    approved = cli._preconfirm_install(None, config, str(root), True)

    assert approved == {"files/" + HASH + "/conflict": "conflict"}
    prompt.assert_not_called()


def test_install_checkbox_cancellation_returns_without_approvals(tmp_path, monkeypatch):
    root, config, paths = _install_prompt_fixture(tmp_path, ["conflict"])
    wrong_target = tmp_path / "wrong-target"
    wrong_target.write_text("existing")
    paths["files/" + HASH + "/conflict"][1].symlink_to(wrong_target)
    monkeypatch.setattr(cli, "_prompt_targets", Mock(return_value=None))

    assert cli._preconfirm_install(None, config, str(root), False) is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_foreign_selection_skips_current_preflight(monkeypatch, dry_run):
    result = operations.OperationResult({"dotfiles": {}}, [])
    dotfiles_config = {
        "dotfiles": {SAVED_KEY: {"linux": {"path": "/install"}, "custom": {}}}
    }
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "validate_config", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/path"))
    monkeypatch.setattr(
        cli.operations,
        "resolve_view_save_path",
        Mock(return_value=f"/repo/{SAVED_KEY}"),
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

    cli._run_parsed_command(_args("rm", **{"<path>": "path", "--dry-run": dry_run}))

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
    dotfiles_config = {"dotfiles": {SAVED_KEY: {"darwin": {}}}}
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "validate_config", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "normalize_path", Mock(return_value="/path"))
    monkeypatch.setattr(
        cli.operations,
        "resolve_view_save_path",
        Mock(return_value=f"/repo/{SAVED_KEY}"),
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

    cli._run_parsed_command(
        _args("rm", **{"<path>": "path", "--all": True, "--dry-run": dry_run})
    )

    paths.assert_called_once_with([f"/repo/{SAVED_KEY}"], "/repo")
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
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, ["changed"])
    dotfiles_config = {"dotfiles": {}}
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
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

    cli._run_parsed_command(_args(command, **values))

    operation, paths = expected
    if operation == "remove":
        import os

        cli.operations.validate_remove.assert_called_once_with(
            "/path", "/repo", os.path.abspath("/path")
        )
        remove.assert_called_once_with(
            *paths,
            dotfiles_config,
            "/repo",
            False,
            True,
            os.path.abspath("/path"),
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
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    doctor = Mock()
    monkeypatch.setattr(cli, "_doctor", doctor)

    assert cli._run_parsed_command(_args("doctor")) is None

    doctor.assert_called_once_with("/repo")


def test_main_successfully_returns_without_system_exit(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["dfm", "doctor"])
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    doctor = Mock()
    monkeypatch.setattr(cli, "_doctor", doctor)

    assert cli.main() is None
    doctor.assert_called_once_with("/repo")


def test_main_click_parser_error_is_stderr_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["dfm", "not-a-command"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "Error:" in captured.err
    assert captured.out == ""


def test_only_symlink_privilege_error_gets_setup_guidance(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["dfm", "doctor"])
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
    monkeypatch.setattr(cli.sys, "argv", ["dfm", "doctor"])
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    monkeypatch.setattr(cli, "_doctor", Mock(side_effect=OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        cli.main()

    assert "Run dfm setup" not in capsys.readouterr().out


def test_view_dispatches_without_saving_configuration(monkeypatch):
    result = operations.OperationResult({"dotfiles": {SAVED_KEY: {}}}, ["viewed"])
    monkeypatch.setattr(cli.operations, "os_name", lambda: "linux")
    monkeypatch.setattr(cli.config, "default_dotfiles_root", Mock(return_value="/repo"))
    dotfiles_config = {"dotfiles": {}}
    monkeypatch.setattr(cli.config, "load_config", Mock(return_value=dotfiles_config))
    monkeypatch.setattr(cli.operations, "plan_view", Mock(return_value=[]))
    monkeypatch.setattr(cli.operations, "validate_view_root", Mock(return_value=None))
    view = Mock(return_value=result)
    monkeypatch.setattr(cli.operations, "view", view)
    save = Mock()
    monkeypatch.setattr(cli.config, "save_config", save)

    cli._run_parsed_command(_args("view"))

    view.assert_called_once_with(dotfiles_config, "/repo", force=True)
    save.assert_not_called()


@pytest.mark.parametrize(
    ("command", "arguments", "positionals"),
    [
        ("add", ["item"], {"<install_path>": "item"}),
        ("rm", ["item"], {"<path>": "item"}),
        ("install", [], {"<save_path>": None}),
        (
            "share",
            ["saved", "item"],
            {"<save_path>": "saved", "<install_path>": "item"},
        ),
        ("view", [], {}),
        ("doctor", [], {}),
        ("setup", [], {}),
    ],
)
def test_click_app_builds_complete_normalized_mapping(
    monkeypatch, command, arguments, positionals
):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)

    result = CliRunner().invoke(cli.click_app, [command, *arguments])

    assert result.exit_code == 0
    args = seen[0]
    assert args[command] is True
    assert all(
        args[name] is False
        for name in ("add", "rm", "install", "share", "view", "doctor", "setup")
        if name != command
    )
    assert args["--target"] == []
    assert all(
        args[name] is False
        for name in (
            "--system",
            "--encrypt",
            "--non-interactive",
            "--dry-run",
            "--force",
            "--all",
        )
    )
    for name, value in {
        "<install_path>": None,
        "<save_path>": None,
        "<path>": None,
        **positionals,
    }.items():
        assert args[name] == value


def test_click_target_forms_are_ordered_and_root_options_merge(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)

    result = CliRunner().invoke(
        cli.click_app,
        [
            "--target=first=~/one",
            "--non-interactive",
            "share",
            "saved",
            "item",
            "--target",
            "second=~/two",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert seen[0]["--target"] == ["first=~/one", "second=~/two"]
    assert seen[0]["--non-interactive"] is True
    assert seen[0]["--dry-run"] is True


def test_click_root_option_is_passed_to_runner(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)

    result = CliRunner().invoke(cli.click_app, ["-r", "custom", "view"])

    assert result.exit_code == 0
    assert seen[0]["--root"] == "custom"


def test_doctor_fix_rebuilds_only_a_missing_install_link(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    saved = root / "files" / HASH / "item"
    install = tmp_path / "home" / "install" / "item"
    saved.parent.mkdir(parents=True)
    install.parent.mkdir(parents=True)
    saved.write_text("content")
    cli.config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/item": {operations.os_name(): {"path": str(install)}}
            }
        },
    )

    cli._doctor(str(root), fix=True)

    assert install.is_symlink()
    assert install.resolve() == saved


def test_doctor_accepts_windows_extended_link_target(tmp_path, monkeypatch):
    root = tmp_path / "root"
    saved = root / "files" / HASH / "item"
    install = tmp_path / "home" / "install" / "item"
    saved.parent.mkdir(parents=True)
    install.parent.mkdir(parents=True)
    saved.write_text("content")
    cli.config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/item": {operations.os_name(): {"path": str(install)}}
            }
        },
    )
    install.symlink_to(saved)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli.os, "readlink", lambda _: "\\\\?\\" + str(saved))

    assert cli._doctor_problems(str(root), cli.config.load_config(str(root))) == []


def test_doctor_fix_rejects_a_reparse_saved_ancestor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    saved = root / "files" / HASH / "item"
    install = tmp_path / "home" / "install" / "item"
    saved.parent.mkdir(parents=True)
    install.parent.mkdir(parents=True)
    saved.write_text("content")
    cli.config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/item": {operations.os_name(): {"path": str(install)}}
            }
        },
    )
    original = cli.operations._is_link_or_reparse
    monkeypatch.setattr(
        cli.operations,
        "_is_link_or_reparse",
        lambda path: path == str(saved.parent) or original(path),
    )

    with pytest.raises(SystemExit):
        cli._doctor(str(root), fix=True)

    assert not install.exists()


def test_doctor_fix_rejects_a_symlinked_files_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    external_files = tmp_path / "external-files"
    saved = external_files / HASH / "item"
    install = tmp_path / "home" / "install" / "item"
    saved.parent.mkdir(parents=True)
    install.parent.mkdir(parents=True)
    saved.write_text("content")
    root.mkdir()
    (root / "files").symlink_to(external_files, target_is_directory=True)
    cli.config.save_config(
        str(root),
        {
            "dotfiles": {
                f"files/{HASH}/item": {operations.os_name(): {"path": str(install)}}
            }
        },
    )

    with pytest.raises(SystemExit):
        cli._doctor(str(root), fix=True)

    assert not install.exists()
    assert saved.read_text() == "content"


def test_cli_root_takes_priority_over_dfm_root(monkeypatch, tmp_path):
    selected = []
    monkeypatch.setenv("DFM_ROOT", str(tmp_path / "environment"))
    monkeypatch.setattr(cli, "_doctor", selected.append)

    cli._run_parsed_command(
        _args("doctor", **{"--root": str(tmp_path / "command-line")})
    )

    assert selected == [str(tmp_path / "command-line")]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--force", "view"],
        ["--all", "add", "item"],
        ["--system", "install"],
        ["--target=x=y", "view"],
        ["--encrypt", "share", "saved", "item"],
        ["--non-interactive", "install"],
        ["--dry-run", "doctor"],
    ],
)
def test_click_rejects_inapplicable_root_options(arguments):
    result = CliRunner(mix_stderr=False).invoke(cli.click_app, arguments)

    assert result.exit_code == 2
    assert "Error:" in result.stderr


@pytest.mark.parametrize(
    "arguments", [["--help"], ["-h"], ["view", "--help"], ["add", "-h"]]
)
def test_click_root_and_command_help(arguments):
    result = CliRunner().invoke(cli.click_app, arguments)

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_click_root_help_contract_describes_workflow_and_commands():
    result = CliRunner().invoke(cli.click_app, ["--help"])

    assert result.exit_code == 0
    assert "manage dotfiles" in result.output
    assert "Common workflow:" in result.output
    output_lines = result.output.splitlines()
    for example in (
        "dfm add ~/.zshrc",
        "dfm install",
        "dfm add --system ~/.config/app/settings.toml",
        "dfm share <SAVE_PATH> <INSTALL_PATH>",
    ):
        assert output_lines.count(f"  {example}") == 1
    for command in ("add", "install", "share", "rm", "view", "doctor", "setup"):
        assert command in result.output
    assert "-r, --root DIR" in result.output


@pytest.mark.parametrize(
    ("command", "required_text", "options"),
    [
        (
            "add",
            "INSTALL_PATH",
            (
                "--system",
                "--encrypt",
                "--non-interactive",
                "--target",
                "--dry-run",
                "--force",
            ),
        ),
        ("install", "SAVE_PATH", ("--dry-run", "--force")),
        (
            "share",
            "SAVE_PATH INSTALL_PATH",
            ("--non-interactive", "--target", "--dry-run", "--force"),
        ),
        ("rm", "PATH", ("--all", "--dry-run", "--force")),
    ],
)
def test_click_command_help_contract(command, required_text, options):
    result = CliRunner().invoke(cli.click_app, [command, "--help"])

    assert result.exit_code == 0
    assert required_text in result.output
    for option in options:
        assert option in result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["unknown"],
        ["rm"],
        ["add"],
        ["share", "saved"],
        ["view", "--force"],
        ["view", "extra"],
    ],
)
def test_click_parser_errors_use_stderr_and_exit_two(arguments):
    result = CliRunner(mix_stderr=False).invoke(cli.click_app, arguments)

    assert result.exit_code == 2
    assert "Error:" in result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_click_shell_completion_sources_are_parser_only(monkeypatch, shell):
    runner = Mock(side_effect=AssertionError("completion must not run a command"))
    load_config = Mock(side_effect=AssertionError("completion must not load config"))
    monkeypatch.setattr(cli, "_run_parsed_command", runner)
    monkeypatch.setattr(cli.config, "load_config", load_config)

    result = CliRunner().invoke(
        cli.click_app,
        [],
        prog_name="dfm",
        env={"_DFM_COMPLETE": f"{shell}_source"},
    )

    assert result.exit_code == 0
    assert result.output
    runner.assert_not_called()
    load_config.assert_not_called()


@pytest.mark.parametrize(
    ("option", "command", "arguments", "expected"),
    [
        ("system", "add", ["item"], {"--system": True}),
        ("encrypt", "add", ["item"], {"--encrypt": True}),
        ("non-interactive", "add", ["item"], {"--non-interactive": True}),
        ("non-interactive", "share", ["saved", "item"], {"--non-interactive": True}),
        ("dry-run", "add", ["item"], {"--dry-run": True}),
        ("dry-run", "rm", ["item"], {"--dry-run": True}),
        ("dry-run", "install", [], {"--dry-run": True}),
        ("dry-run", "share", ["saved", "item"], {"--dry-run": True}),
        ("dry-run", "view", [], {"--dry-run": True}),
        ("force", "add", ["item"], {"--force": True}),
        ("force", "rm", ["item"], {"--force": True}),
        ("force", "install", ["saved"], {"--force": True}),
        ("force", "share", ["saved", "item"], {"--force": True}),
        ("all", "rm", ["item"], {"--all": True}),
    ],
)
@pytest.mark.parametrize("placement", ["local_before", "local_after", "root"])
def test_click_supported_options_map_from_both_positions(
    monkeypatch, option, command, arguments, expected, placement
):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)
    option_token = "--" + option
    if placement == "local_before":
        argv = [command, option_token, *arguments]
    elif placement == "local_after":
        argv = [command, *arguments, option_token]
    else:
        argv = [option_token, command, *arguments]

    result = CliRunner().invoke(cli.click_app, argv)

    assert result.exit_code == 0
    for key, value in expected.items():
        assert seen[0][key] is value


@pytest.mark.parametrize("command", ["add", "share"])
def test_click_target_forms_and_ordered_repetition_for_each_target_command(
    monkeypatch, command
):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)
    arguments = ["item"] if command == "add" else ["saved", "item"]
    result = CliRunner().invoke(
        cli.click_app,
        [
            "--target=first=~/one",
            "--non-interactive",
            command,
            *arguments,
            "--target",
            "second=~/two",
            "--target=third=~/three",
        ],
    )

    assert result.exit_code == 0
    assert seen[0]["--target"] == [
        "first=~/one",
        "second=~/two",
        "third=~/three",
    ]


@pytest.mark.parametrize(
    ("command", "positionals", "argv"),
    [
        (
            "add",
            {"<install_path>": "item"},
            ["--target=root=~/root", "add", "item", "--target=local-after=~/after"],
        ),
        (
            "add",
            {"<install_path>": "item"},
            ["add", "--target", "local-before=~/before", "item"],
        ),
        (
            "share",
            {"<save_path>": "saved", "<install_path>": "item"},
            [
                "--target=root=~/root",
                "share",
                "saved",
                "--target",
                "local-before=~/before",
                "item",
            ],
        ),
        (
            "share",
            {"<save_path>": "saved", "<install_path>": "item"},
            ["share", "saved", "item", "--target=local-after=~/after"],
        ),
    ],
)
def test_click_target_position_contracts_preserve_mapping_order(
    monkeypatch, command, positionals, argv
):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)

    result = CliRunner().invoke(cli.click_app, argv)

    assert result.exit_code == 0
    args = seen[0]
    assert {key: args[key] for key in positionals} == positionals
    if argv[0].startswith("--target="):
        assert args["--target"][0] == "root=~/root"
    assert args["--target"][-1].startswith("local-")


def test_click_install_nonempty_optional_save_path_is_mapped(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_run_parsed_command", seen.append)

    result = CliRunner().invoke(cli.click_app, ["install", "saved-object"])

    assert result.exit_code == 0
    assert seen[0]["install"] is True
    assert seen[0]["<save_path>"] == "saved-object"
