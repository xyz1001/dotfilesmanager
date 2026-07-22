"""Command-line adapter for dotfilesmanager."""

import copy
import ntpath
import os
import re
import stat
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, cast

import click
import questionary

from . import config, encryption, operations, windows
from ._types import Config


def _confirm_replace(link):
    return input(f"文件 {link} 已存在，是否替换？(y/N)").lower() == "y"


def _fail(message):
    print(message)
    raise SystemExit(-1)


def _load_config(root):
    try:
        return config.load_config(root)
    except ValueError as error:
        _fail(f"Invalid configuration: {error}")


def _render(result):
    for message in result.messages:
        print(message)


_SYSTEM_LABELS = {
    "linux": "Linux",
    "darwin": "macOS",
    "windows": "Windows",
    "android": "Android (Termux)",
}
_CUSTOM = "__custom__"


def _prompt_targets(questions):
    """Ask named Questionary questions and return an Inquirer-like mapping.

    Questionary's module-level ``prompt`` accepts dictionaries, whereas the
    factories used here return ``Question`` instances.  Keep that difference
    at this boundary so the rest of the CLI can continue to deal in named
    answers.
    """
    try:
        answers = {}
        for question in questions:
            answer = question.ask()
            if answer is None:
                return None
            answers[question.name] = answer
        return answers
    except (EOFError, KeyboardInterrupt):
        return None


def _named_question(factory, name, **kwargs):
    """Create a Questionary question and attach the CLI answer name."""
    question = factory(**kwargs)
    question.name = name
    return question


def _target_wizard(install_path, configured, dry_run=False):
    """Collect foreign mappings, returning None when the wizard is cancelled."""
    current = operations.os_name()
    available = [
        system
        for system in operations.SUPPORTED_SYSTEMS
        if system != current and system not in configured
    ]
    answers = _prompt_targets(
        [
            _named_question(
                questionary.checkbox,
                "systems",
                message="Select target systems",
                choices=[
                    questionary.Choice(
                        _SYSTEM_LABELS.get(current, current),
                        value=current,
                        disabled=True,
                        checked=True,
                    )
                ]
                + [
                    questionary.Choice(_SYSTEM_LABELS[system], value=system)
                    for system in available
                ],
            )
        ]
    )
    if not answers or "systems" not in answers:
        return None
    selected_systems = [system for system in answers["systems"] if system in available]
    if not selected_systems:
        return {}
    selected = {}
    for system in available:
        if system not in selected_systems:
            continue
        candidates = operations.target_candidates(install_path, system)
        system_label = _SYSTEM_LABELS.get(system, system)
        while True:
            answers = _prompt_targets(
                [
                    _named_question(
                        questionary.select,
                        "path",
                        message=f"Target path for {system_label}",
                        choices=[
                            *[
                                questionary.Choice(label, value=value)
                                for label, value in candidates
                            ],
                            questionary.Choice("Custom path", value=_CUSTOM),
                        ],
                        default=candidates[0][1],
                    )
                ]
            )
            if not answers or "path" not in answers:
                return None
            path = answers["path"]
            if path != _CUSTOM:
                break

            def validate(value, target_system=system):
                if not value or not value.strip():
                    return True
                error = operations.validate_foreign_target(target_system, value)
                if error:
                    return error
                return True

            answers = _prompt_targets(
                [
                    _named_question(
                        questionary.text,
                        "custom_path",
                        message=f"Custom path for {system_label}",
                        validate=validate,
                    )
                ]
            )
            if not answers or "custom_path" not in answers:
                return None
            path = answers["custom_path"]
            if path and path.strip():
                break
        selected[system] = path
    if not selected:
        return {}
    print(
        "Target plan: " + ", ".join(f"{key}={value}" for key, value in selected.items())
    )
    if dry_run:
        return selected
    answers = _prompt_targets(
        [
            _named_question(
                questionary.confirm,
                "confirm",
                message="Apply target plan?",
                default=False,
            )
        ]
    )
    if not answers or not answers.get("confirm", False):
        return None
    return selected


def _select_remove_systems(args, configured):
    """Choose registered systems to remove, without prompting outside a TTY."""
    current = operations.os_name()
    systems = list(configured)
    if args.get("--all", False):
        return set(systems)
    if not sys.stdin.isatty():
        return {current}
    answers = _prompt_targets(
        [
            _named_question(
                questionary.checkbox,
                "systems",
                message="Select systems to remove",
                choices=[
                    questionary.Choice(
                        _SYSTEM_LABELS.get(system, system),
                        value=system,
                        checked=system == current,
                    )
                    for system in systems
                ],
            )
        ]
    )
    if not answers or "systems" not in answers:
        return None
    return set(answers["systems"]).intersection(systems)


def _select_targets(
    args, command, install_path, dotfiles_config, root, dry_run, saved_path=None
):
    """Validate/collect targets before a direct mutation."""
    if saved_path is None:
        # Compatibility for callers of this private helper predating the
        # explicit saved_path argument.  Command preparation never relies on it.
        saved_path = args.get("_saved")
    supplied = args.get("--target", []) or []
    # The default preserves direct callers which predate these normalized keys.
    non_interactive = args.get("--non-interactive", True)
    if command == "add" and args.get("--system", False):
        return {}
    rel = (
        operations.save_path_to_key(
            operations.get_save_path(install_path, args.get("--system", False), root),
            root,
        )
        if command == "add"
        else operations.save_path_to_key(saved_path, root)
    )
    if command == "share" and operations.is_platform_specific_save_path(rel):
        if supplied:
            _fail("platform-specific saved objects cannot have external targets")
        return {}
    if supplied and not non_interactive:
        _fail("--target requires --non-interactive")
    if command == "add" and args.get("--system", False) and supplied:
        _fail("--system cannot use --target")
    if non_interactive:
        try:
            targets = operations.parse_target_mappings(supplied)
        except ValueError as error:
            _fail(str(error))
    else:
        if not sys.stdin.isatty():
            _fail("add/share require --non-interactive when stdin is not a TTY")
        raw_key = operations.raw_save_key(dotfiles_config, rel)
        configured = dotfiles_config["dotfiles"].get(raw_key, {}) if raw_key else {}
        targets = _target_wizard(install_path, configured, dry_run)
        if targets is None:
            _fail("target selection cancelled")
    try:
        operations.merge_targets(dotfiles_config, rel, targets)
    except ValueError as error:
        _fail(str(error))
    return targets


def _preconfirm_install(abs_save_path, dotfiles_config, root, force):
    """Collect every install replacement decision before mutation."""
    selected = None
    if abs_save_path is not None:
        selected = operations.save_path_to_key(abs_save_path, root)
    approved = {}
    conflicts = []
    for rel_path in dotfiles_config["dotfiles"]:
        if selected is not None and operations.canonical_save_key(rel_path) != selected:
            continue
        install = operations.get_path(dotfiles_config, rel_path)
        if install is None:
            continue
        saved = operations.key_to_save_path(rel_path, root)
        state = operations._install_link_state(saved, install)
        if state == "correct":
            continue
        if state in ("missing", "dangling", "sync") or force:
            approved[rel_path] = state
        else:
            conflicts.append((rel_path, install, state))
    if force or not conflicts:
        return approved
    answers = _prompt_targets(
        [
            _named_question(
                questionary.checkbox,
                "paths",
                message="Select destination paths to replace",
                choices=[
                    questionary.Choice(install, value=rel_path)
                    for rel_path, install, _ in conflicts
                ],
            )
        ]
    )
    if answers is None or "paths" not in answers:
        return None
    selected_paths = set(answers["paths"])
    for rel_path, _, state in conflicts:
        if rel_path in selected_paths:
            approved[rel_path] = state
    return approved


def _direct_paths(
    command, args, dotfiles_config, root, resolved_save_path=None, selected_systems=None
):
    """Return direct paths each operation may replace, move, or unlink."""
    if command == "add":
        install = operations.normalize_path(args["<install_path>"])
        return [
            install,
            operations.get_save_path(install, args.get("--system", False), root),
        ]
    if command == "rm":
        # A foreign-only removal changes configuration only unless it removes
        # the final registered platform, in which case it also deletes saved.
        if (
            not args.get("--all", False)
            and selected_systems is not None
            and operations.os_name() not in selected_systems
        ):
            saved = resolved_save_path
            if saved is None:
                path = operations.normalize_path(args["<path>"])
                saved = operations._remove_save_path(path, root)
            rel_path = operations.save_path_to_key(saved, root)
            raw_key = operations.raw_save_key(dotfiles_config, rel_path)
            registered = set(dotfiles_config.get("dotfiles", {}).get(raw_key, {}))
            selected = set(selected_systems).intersection(registered)
            if not selected or selected != registered:
                return []
            return [saved]
        saved = resolved_save_path
        if saved is None:
            path = operations.normalize_path(args["<path>"])
            saved = operations._remove_save_path(path, root)
        paths = [saved]
        rel_path = operations.save_path_to_key(saved, root)
        # rm only mutates its saved object and, when registered, the current
        # platform's install path. Foreign mappings are configuration data.
        raw_key = operations.raw_save_key(dotfiles_config, rel_path)
        item = (
            dotfiles_config.get("dotfiles", {})
            .get(raw_key, {})
            .get(operations.os_name())
        )
        if isinstance(item, dict) and item.get("path"):
            paths.append(operations.normalize_path(item["path"]))
        return paths
    if command == "share":
        return [operations.normalize_path(args["<install_path>"])]
    if command == "view":
        return [os.path.join(root, operations.VIEW_DIRECTORY)]
    paths = []
    selected = resolved_save_path or operations.normalize_path(args.get("<save_path>"))
    for rel_path in dotfiles_config["dotfiles"]:
        if selected is not None and operations.save_path_to_key(
            selected, root
        ) != operations.canonical_save_key(rel_path):
            continue
        install = operations.get_path(dotfiles_config, rel_path)
        if install:
            paths.append(install)
    return paths


@dataclass
class _PreparedCommand:
    command: str
    args: Dict[str, Any]
    dotfiles_config: Dict[str, Any]
    original_config: Dict[str, Any]
    install: Optional[str] = None
    path: Optional[str] = None
    saved: Optional[str] = None
    rm_save_path: Optional[str] = None
    selected_remove_systems: Optional[Set[str]] = None
    targets: Optional[Dict[str, str]] = None
    install_approved: Optional[Dict[str, str]] = None
    share_state: Optional[str] = None


def _prepare_direct_command(command, args, root, dry_run):
    """Load and validate a direct command, without performing its operation."""
    dotfiles_config = _load_config(root)
    errors = operations.validate_config(dotfiles_config, root)
    if errors:
        _fail(errors[0])
    dotfiles_config = cast(Config, dotfiles_config)
    original_config = copy.deepcopy(dotfiles_config)
    # Target selection merges mappings.  Keep that derived state off the loaded
    # object so preparation remains read-only (especially for dry-runs).
    dotfiles_config = copy.deepcopy(dotfiles_config)
    prepared = _PreparedCommand(command, args, dotfiles_config, original_config)

    if command == "view":
        try:
            operations.plan_view(dotfiles_config, root)
        except ValueError as error:
            _fail(str(error))
        error = operations.validate_view_mutation_root(
            root
        ) or operations.validate_view_root(root, force=True)
        if error:
            _fail(error)
        return prepared

    if command == "add":
        prepared.install = operations.normalize_path(args["<install_path>"])
        error = operations.validate_add(
            prepared.install, args.get("--system", False), root
        )
        if error:
            _fail(error)
    elif command == "rm":
        prepared.path = operations.normalize_path(args["<path>"])
        prepared.rm_save_path = operations.resolve_view_save_path(args["<path>"], root)
        if prepared.rm_save_path == prepared.path:
            prepared.rm_save_path = operations._remove_save_path(prepared.path, root)
        error = operations.validate_remove(prepared.path, root, prepared.rm_save_path)
        if error:
            _fail(error)
        rel_path = operations.save_path_to_key(prepared.rm_save_path, root)
        raw_key = operations.raw_save_key(dotfiles_config, rel_path)
        prepared.selected_remove_systems = _select_remove_systems(
            args, dotfiles_config["dotfiles"].get(raw_key, {})
        )
        if prepared.selected_remove_systems is None:
            return None
        if operations.os_name() in prepared.selected_remove_systems:
            error = operations.validate_remove_destination(
                dotfiles_config, rel_path, root, args.get("--force", False)
            )
            if error:
                _fail(error)
    elif command == "share" or (
        command == "install" and args.get("<save_path>") is not None
    ):
        prepared.saved = operations.resolve_view_save_path(args["<save_path>"], root)
        error = operations.validate_saved_object(prepared.saved, root)
        if error:
            _fail(error)

    if command == "share":
        prepared.install = operations.normalize_path(args["<install_path>"])
        error = operations.validate_install_target(prepared.install, root)
        if error:
            _fail(error)
        error = operations.validate_share_state(
            prepared.saved, prepared.install, dotfiles_config, root
        )
        if error:
            _fail(error)
    if command in ("add", "share"):
        prepared.targets = _select_targets(
            args,
            command,
            prepared.install,
            dotfiles_config,
            root,
            dry_run,
            prepared.saved,
        )
    if command == "install":
        error = operations.validate_install_sources(
            dotfiles_config, root, prepared.saved
        )
        if error:
            _fail(error)
        if not dry_run:
            prepared.install_approved = _preconfirm_install(
                prepared.saved,
                dotfiles_config,
                root,
                args.get("--force", False),
            )
            if prepared.install_approved is None:
                return None
    if command == "share" and not dry_run:
        prepared.share_state = operations._link_state(prepared.saved, prepared.install)
        if prepared.share_state == "conflict" and not args.get("--force", False):
            if args.get("--non-interactive", True):
                _fail("existing install path requires --force in non-interactive mode")
            if not _confirm_replace(prepared.install):
                return None
    if command != "view":
        error = operations.validate_mutation_paths(
            _direct_paths(
                command,
                args,
                dotfiles_config,
                root,
                prepared.rm_save_path or prepared.saved,
                prepared.selected_remove_systems,
            ),
            root,
        )
        if error:
            _fail(error)
    return prepared


def _doctor_problems(root, dotfiles_config):
    problems = []
    for rel_path in dotfiles_config["dotfiles"]:
        saved = operations.key_to_save_path(rel_path, root)
        if not os.path.lexists(saved):
            problems.append(f"missing saved path: {rel_path}")
        install = operations.get_path(dotfiles_config, rel_path)
        if not install:
            continue
        if not os.path.lexists(install):
            problems.append(f"missing install link: {install}")
        elif not os.path.islink(install):
            problems.append(f"install path is not a link: {install}")
        else:
            target = os.readlink(install)
            if not _link_target_matches(target, install, saved):
                problems.append(f"wrong install link: {install}")
            elif not os.path.exists(install):
                problems.append(f"dangling install link: {install}")
    problems.extend(_unreferenced_saved_objects(root, dotfiles_config))
    return problems


def _link_target_matches(target, install, saved):
    """Compare a link target using Windows' native link-path spelling."""
    if os.name == "nt":
        path_module = ntpath
        if target.startswith("\\\\?\\UNC\\"):
            target = "\\\\" + target[8:]
        elif target.startswith(("\\\\?\\", "\\??\\")):
            target = target[4:]
    else:
        path_module = os.path
    if not path_module.isabs(target):
        target = path_module.join(path_module.dirname(install), target)
    return path_module.normcase(path_module.abspath(path_module.normpath(target))) == (
        path_module.normcase(path_module.abspath(path_module.normpath(saved)))
    )


def _safe_install_parent(path):
    """Return whether every existing install parent is a real directory."""
    parent = os.path.abspath(os.path.dirname(path))
    while parent:
        if not os.path.isdir(parent) or operations._is_link_or_reparse(parent):
            return False
        if parent == os.path.dirname(parent):
            break
        parent = os.path.dirname(parent)
    return True


def _safe_saved_path(root, saved):
    """Check every saved-object component before using it as a link target."""
    root = os.path.abspath(root)
    files_root = os.path.join(root, "files")
    saved = os.path.abspath(saved)
    if (
        operations._is_link_or_reparse(root)
        or not os.path.isdir(root)
        or operations._is_link_or_reparse(files_root)
        or not os.path.isdir(files_root)
        or not operations._is_within(saved, files_root)
    ):
        return None
    relative = os.path.relpath(saved, files_root)
    components = relative.split(os.sep)
    if len(components) < 2 or not re.fullmatch(r"[0-9a-f]{32}", components[0]):
        return None
    current = files_root
    for component in components:
        current = os.path.join(current, component)
        if not os.path.lexists(current) or operations._is_link_or_reparse(current):
            return None
        if current != saved and not os.path.isdir(current):
            return None
    try:
        mode = os.stat(saved).st_mode
    except OSError:
        return None
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        return None
    return mode


def _fix_missing_install_links(root, dotfiles_config):
    fixed = []
    failures = []
    for rel_path in dotfiles_config["dotfiles"]:
        saved = operations.key_to_save_path(rel_path, root)
        install = operations.get_path(dotfiles_config, rel_path)
        if not install or not os.path.lexists(saved):
            continue
        saved_mode = _safe_saved_path(root, saved)
        if saved_mode is None:
            continue
        if os.path.lexists(install) or not _safe_install_parent(install):
            continue
        # Recheck immediately before creation; an existing destination is never
        # replaced, even if it appeared after the first check.
        if os.path.lexists(install):
            continue
        try:
            windows.create_symlink(
                saved, install, target_is_directory=stat.S_ISDIR(saved_mode)
            )
            fixed.append(f"missing install link: {install}")
        except Exception as error:
            failures.append(f"could not fix missing install link {install}: {error}")
    return fixed, failures


def _safe_cleanup_unreferenced(root, dotfiles_config):
    """Remove only unreferenced, ordinary children of real hash namespaces."""
    managed = {
        os.path.abspath(operations.key_to_save_path(path, root))
        for path in dotfiles_config["dotfiles"]
    }
    files_root = os.path.join(root, "files")
    if operations._is_link_or_reparse(files_root) or not os.path.isdir(files_root):
        return []
    removed = []

    def protected(path):
        return any(
            path == saved
            or operations._is_within(path, saved)
            or operations._is_within(saved, path)
            for saved in managed
        )

    def clean(path, namespace):
        if not operations._is_within(path, namespace):
            return
        if os.path.islink(path):
            if not protected(path):
                try:
                    os.unlink(path)
                    removed.append(path)
                except OSError:
                    pass
            return
        # A Windows reparse point which is not a symlink is not safe to inspect.
        if operations._is_link_or_reparse(path):
            return
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return
        if stat.S_ISDIR(mode):
            try:
                entries = list(os.scandir(path))
            except OSError:
                return
            for entry in entries:
                clean(os.path.abspath(entry.path), namespace)
            if path != namespace and not protected(path):
                try:
                    os.rmdir(path)
                    removed.append(path)
                except OSError:
                    pass
        elif stat.S_ISREG(mode) and not protected(path):
            try:
                os.unlink(path)
                removed.append(path)
            except OSError:
                pass

    for namespace in os.listdir(files_root):
        if not re.fullmatch(r"[0-9a-f]{32}", namespace):
            continue
        namespace_path = os.path.abspath(os.path.join(files_root, namespace))
        if not os.path.isdir(namespace_path) or operations._is_link_or_reparse(
            namespace_path
        ):
            continue
        clean(namespace_path, namespace_path)
    return removed


def _doctor(root, fix=False):
    problems = []
    fixed = []
    if not os.path.isdir(root):
        _fail(f"dotfiles root does not exist: {root}")
    try:
        dotfiles_config = _load_config(root)
        problems.extend(operations.validate_config(dotfiles_config, root))
        if not problems:
            dotfiles_config = cast(Config, dotfiles_config)
            if fix:
                fixed, fix_failures = _fix_missing_install_links(root, dotfiles_config)
                fixed.extend(
                    f"unreferenced saved object: {path} removed"
                    for path in _safe_cleanup_unreferenced(root, dotfiles_config)
                )
                problems.extend(fix_failures)
            # Always rescan after a fix; this also preserves unfixable diagnoses.
            problems.extend(_doctor_problems(root, dotfiles_config))
    except Exception as error:
        problems.append(f"Invalid configuration: {error}")
    for message in fixed:
        print(f"Fixed: {message}")
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(-1)
    print("No configuration problems found")


def _unreferenced_saved_objects(root, dotfiles_config):
    """Inspect only hash namespaces produced by get_save_path, never repo files."""
    managed = {
        operations.key_to_save_path(path, root) for path in dotfiles_config["dotfiles"]
    }
    problems = []
    files_root = os.path.join(root, "files")
    if operations._is_link_or_reparse(files_root) or not os.path.isdir(files_root):
        return problems
    for namespace in os.listdir(files_root):
        if not re.fullmatch(r"[0-9a-f]{32}", namespace):
            continue
        namespace_path = os.path.join(files_root, namespace)
        if not os.path.isdir(namespace_path) or operations._is_link_or_reparse(
            namespace_path
        ):
            continue
        for current, directories, files in os.walk(namespace_path, topdown=True):
            reparse_directories = []
            safe_directories = []
            for name in directories:
                if operations._is_link_or_reparse(os.path.join(current, name)):
                    reparse_directories.append(name)
                else:
                    safe_directories.append(name)
            directories[:] = safe_directories
            for name in reparse_directories + directories + files:
                path = os.path.abspath(os.path.join(current, name))
                if any(
                    path == saved
                    or operations._is_within(path, saved)
                    or operations._is_within(saved, path)
                    for saved in managed
                ):
                    continue
                problems.append(
                    f"unreferenced saved object: {os.path.relpath(path, root)}"
                )
    return problems


def main():
    try:
        click_app.main(standalone_mode=False)
    except click.ClickException as error:
        error.show()
        raise SystemExit(error.exit_code) from None
    except click.exceptions.Exit as error:
        if error.exit_code:
            raise SystemExit(error.exit_code) from None
        return
    except windows.SymlinkPrivilegeError:
        _fail(
            "Windows could not create a symbolic link because the required "
            "privilege is not held. Run dfm setup, then inspect and repair any "
            "partially applied state before retrying."
        )


_CLICK_OPTION_COMMANDS = {
    "system": {"add"},
    "encrypt": {"add"},
    "non_interactive": {"add", "share"},
    "target": {"add", "share"},
    "dry_run": {"add", "rm", "install", "share", "view"},
    "force": {"add", "rm", "install", "share"},
    "all": {"rm"},
    "fix": {"doctor"},
}


def _build_command_args(command, root_options, command_options):
    """Build the normalized argument mapping consumed by the existing runner."""
    values = dict(root_options)
    for name, value in command_options.items():
        if name == "target":
            values[name] = [*values.get(name, ()), *value]
        elif value:
            values[name] = value

    for name, commands in _CLICK_OPTION_COMMANDS.items():
        value = values.get(name, () if name == "target" else False)
        if value and command not in commands:
            option = "--" + name.replace("_", "-")
            raise click.UsageError(f"{option} is not valid for {command}")

    args = {
        name: False
        for name in ("add", "rm", "install", "share", "view", "doctor", "setup")
    }
    args[command] = True
    args.update(
        {
            "--system": values.get("system", False),
            "--encrypt": values.get("encrypt", False),
            "--non-interactive": values.get("non_interactive", False),
            "--target": list(values.get("target", ())),
            "--dry-run": values.get("dry_run", False),
            "--force": values.get("force", False),
            "--all": values.get("all", False),
            "--root": values.get("root"),
            "--fix": values.get("fix", False),
            "<install_path>": command_options.get("install_path"),
            "<save_path>": command_options.get("save_path"),
            "<path>": command_options.get("path"),
        }
    )
    return args


def _run_click_command(ctx, command, **command_options):
    args = _build_command_args(command, ctx.obj["root_options"], command_options)
    return _run_parsed_command(args)


def _root_click_options(function):
    for option in (
        click.option(
            "-r",
            "--root",
            metavar="DIR",
            type=click.Path(),
            help="Directory used to store managed dotfiles.",
        ),
        click.option("--all", "all", is_flag=True, hidden=True),
        click.option("--force", is_flag=True, hidden=True),
        click.option("--dry-run", "dry_run", is_flag=True, hidden=True),
        click.option("--target", multiple=True, hidden=True),
        click.option("--non-interactive", "non_interactive", is_flag=True, hidden=True),
        click.option("--encrypt", is_flag=True, hidden=True),
        click.option("--system", is_flag=True, hidden=True),
        click.option("--fix", is_flag=True, hidden=True),
    ):
        function = option(function)
    return function


@click.group(
    name="dfm",
    invoke_without_command=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@_root_click_options
@click.pass_context
def click_app(ctx, **options):
    """dfm: manage dotfiles and safely reuse configuration across systems.

    Common workflow:

    \b
    Use ``add`` to track a file, then ``install`` to create its link;
    use ``share`` to link a saved object elsewhere, and ``view`` to inspect the view.
    Use ``doctor`` to check the configuration, ``rm`` to remove managed objects, and
    ``setup`` to prepare Windows for symbolic links.

    Key usage examples:

    \b
    dfm add ~/.zshrc
    dfm install
    dfm add --system ~/.config/app/settings.toml
    dfm share <SAVE_PATH> <INSTALL_PATH>

    Run ``dfm <COMMAND> --help`` for details about a command's arguments.
    """
    ctx.ensure_object(dict)["root_options"] = options


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--force", is_flag=True, help="Force replacement when the target conflicts."
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, help="Show the plan without changing files."
)
@click.option(
    "--target",
    multiple=True,
    metavar="SYSTEM=PATH",
    help="Set an install target for a system; may be repeated.",
)
@click.option(
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Skip the interactive wizard; useful for scripts.",
)
@click.option(
    "--encrypt", is_flag=True, help="Encrypt the saved content when supported."
)
@click.option(
    "--system",
    is_flag=True,
    help="Save for the current system without cross-system targets.",
)
@click.argument("install_path", metavar="INSTALL_PATH")
@click.pass_context
def add(ctx, install_path, **options):
    """Track an install path and save it as a dotfile object.

    INSTALL_PATH is an existing file or directory to track. By default, dfm opens
    the interactive target selector; use --non-interactive and --target in scripts.
    """
    return _run_click_command(ctx, "add", install_path=install_path, **options)


@click_app.command(name="rm", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--all",
    "all",
    is_flag=True,
    help="Remove all registered systems, not just the current system.",
)
@click.option(
    "--force", is_flag=True, help="Force handling of conflicting install paths."
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, help="Show the plan without changing files."
)
@click.argument("path", metavar="PATH")
@click.pass_context
def remove(ctx, path, **options):
    """Remove the saved object for PATH and its install link on the current system.

    PATH may be an install path or a saved path. By default only the current system
    is handled; --all handles every registered system. Use --dry-run to preview.
    """
    return _run_click_command(ctx, "rm", path=path, **options)


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--force", is_flag=True, help="Force replacement of existing install links."
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, help="Show the plan without changing files."
)
@click.argument("save_path", metavar="SAVE_PATH", required=False)
@click.pass_context
def install(ctx, save_path, **options):
    """Create install links from saved objects.

    SAVE_PATH is optional; omit it to install every configured object, or provide
    one to install only that object.
    """
    return _run_click_command(ctx, "install", save_path=save_path, **options)


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--force", is_flag=True, help="Force replacement when the target conflicts."
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, help="Show the plan without changing files."
)
@click.option(
    "--target",
    multiple=True,
    metavar="SYSTEM=PATH",
    help="Set an install target for a system; may be repeated.",
)
@click.option(
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Skip the interactive wizard; useful for scripts.",
)
@click.argument("save_path", metavar="SAVE_PATH")
@click.argument("install_path", metavar="INSTALL_PATH")
@click.pass_context
def share(ctx, save_path, install_path, **options):
    """Link a saved object to a new install location.

    SAVE_PATH is the saved object and INSTALL_PATH is the target location. For
    non-interactive use, provide --non-interactive and one or more --target values.
    """
    return _run_click_command(
        ctx, "share", save_path=save_path, install_path=install_path, **options
    )


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--dry-run", "dry_run", is_flag=True)
@click.pass_context
def view(ctx, **options):
    """Rebuild and inspect the dotfiles view from the configuration."""
    return _run_click_command(ctx, "view", **options)


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--fix", is_flag=True, help="Repair safe, automatically fixable issues.")
@click.pass_context
def doctor(ctx, **options):
    """Check the configuration, saved objects, and install links for consistency."""
    return _run_click_command(ctx, "doctor", **options)


@click_app.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def setup(ctx):
    """Enable the Windows developer setting required for symbolic links."""
    return _run_click_command(ctx, "setup")


@click_app.group(name="encrypt")
def encrypt_group():
    """Configure and operate value-level encryption."""


@encrypt_group.command(name="init")
@click.argument("recipient")
@click.pass_context
def encrypt_init(ctx, recipient):
    """Create the data key and configure the Git filter."""
    root = config.resolve_dotfiles_root(ctx.obj["root_options"].get("root"))
    encryption.init(root, recipient)


@encrypt_group.command(name="filter")
@click.argument("kind", type=click.Choice(["clean", "smudge"]))
@click.argument("filename")
def encrypt_filter(kind, filename):
    """Run the Git clean or smudge filter."""
    encryption.filter_command(kind, filename)




def _run_parsed_command(args):
    """Run a complete command from the normalized argument mapping."""
    if args.get("setup"):
        result = windows.setup_developer_mode()
        print(result.message)
        if not result.success:
            raise SystemExit(-1)
        return
    root = config.resolve_dotfiles_root(args.get("--root"))
    if args.get("doctor"):
        if args.get("--fix", False):
            _doctor(root, fix=True)
        else:
            _doctor(root)
        return
    command = next(
        name for name in ("add", "rm", "install", "share", "view") if args.get(name)
    )
    if command in ("add", "share"):
        non_interactive = args.get("--non-interactive", True)
        if args.get("--target", []) and not non_interactive:
            _fail("--target requires --non-interactive")
        if (
            command == "add"
            and args.get("--system", False)
            and args.get("--target", [])
        ):
            _fail("--system cannot use --target")
        if (
            not non_interactive
            and not (command == "add" and args.get("--system", False))
            and not sys.stdin.isatty()
        ):
            _fail("add/share require --non-interactive when stdin is not a TTY")
    dry_run = args.get("--dry-run", False)
    prepared = _prepare_direct_command(command, args, root, dry_run)
    if prepared is None:
        return
    if dry_run:
        print(f"Dry-run: {command}; no changes made")
        return

    def run_direct():
        dotfiles_config = prepared.dotfiles_config
        original_config = prepared.original_config
        install = prepared.install
        path = prepared.path
        saved = prepared.saved
        rm_save_path = prepared.rm_save_path
        selected_remove_systems = prepared.selected_remove_systems
        targets = prepared.targets or {}
        install_approved = prepared.install_approved
        share_state = prepared.share_state

        def confirm(_):
            return True

        if command == "add":
            result = operations.add(
                install,
                args.get("--system", False),
                dotfiles_config,
                root,
                targets,
                encrypt=args.get("--encrypt", False),
            )
        elif command == "rm":
            result = operations.remove(
                path,
                dotfiles_config,
                root,
                args.get("--force", False),
                args.get("--all", False),
                rm_save_path,
                selected_systems=selected_remove_systems,
            )
        elif command == "install":
            result = operations.install(
                saved,
                dotfiles_config,
                root,
                confirm,
                install_approved,
            )
        elif command == "share":
            result = operations.share(
                saved,
                operations.normalize_path(args["<install_path>"]),
                dotfiles_config,
                root,
                confirm,
                targets,
                share_state,
            )
        else:
            result = operations.view(dotfiles_config, root, force=True)
        if command in ("add", "rm", "share") and result.config != original_config:
            config.save_config(root, result.config)
            try:
                error = operations.validate_view_mutation_root(root)
                if error:
                    raise ValueError(error)
                view_result = operations.view(result.config, root, force=True)
            except windows.SymlinkPrivilegeError:
                raise
            except Exception as error:
                raise RuntimeError(
                    "configuration was saved but view rebuild failed; "
                    "run dfm view to repair"
                ) from error
            result.messages.extend(view_result.messages)
        return result

    result = run_direct()
    if result is None:
        return
    _render(result)
