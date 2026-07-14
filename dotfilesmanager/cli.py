"""Command-line adapter for dotfilesmanager."""

import copy
import os
import re
import sys

import inquirer
from docopt import docopt
from inquirer.errors import ValidationError

from . import config, operations, windows

USAGE = """
dotfile管理工具(dotfiles manager)，dotfile指保存配置信息的文件或包含配置文件的文件夹

Usage:
    dfm add <install_path> [--system] [--non-interactive] [--target=<mapping>...] [--dry-run] [--force]
    dfm rm <path> [--all] [--dry-run] [--force]
    dfm install [<save_path>] [--dry-run] [--force]
    dfm share <save_path> <install_path> [--non-interactive] [--target=<mapping>...] [--dry-run] [--force]
    dfm view [--dry-run] [--force]
    dfm doctor
    dfm setup

Options:
    -h --help  显示帮助
    --system   该dotfile和操作系统相关
    --non-interactive  Never read stdin; required for --target and non-TTY use.
    --target=<mapping>  Foreign target mapping, repeated as SYSTEM=~/path.
    --dry-run  Validate and show what would be changed without writing.
    --force    Do not ask before replacing an existing path.
    --all      Remove registrations for every platform.
"""


def _confirm_replace(link):
    return input(f"文件 {link} 已存在，是否替换？(y/N)").lower() == "y"


def _fail(message):
    print(message)
    raise SystemExit(-1)


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
    """Single prompt boundary, kept separate for non-TTY-safe tests."""
    try:
        return inquirer.prompt(questions)
    except (EOFError, KeyboardInterrupt):
        return None


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
            inquirer.Checkbox(
                "systems",
                message="Select target systems",
                choices=[(_SYSTEM_LABELS.get(current, current), current)]
                + [(_SYSTEM_LABELS[system], system) for system in available],
                default=[current],
                locked=[current],
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
                    inquirer.List(
                        "path",
                        message=f"Target path for {system_label}",
                        choices=[*candidates, ("Custom path", _CUSTOM)],
                        default=candidates[0][1],
                    )
                ]
            )
            if not answers or "path" not in answers:
                return None
            path = answers["path"]
            if path != _CUSTOM:
                break

            def validate(_, value, target_system=system):
                if not value or not value.strip():
                    return True
                error = operations.validate_foreign_target(target_system, value)
                if error:
                    raise ValidationError(value, reason=error)
                return True

            answers = _prompt_targets(
                [
                    inquirer.Text(
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
        [inquirer.Confirm("confirm", message="Apply target plan?", default=False)]
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
            inquirer.Checkbox(
                "systems",
                message="Select systems to remove",
                choices=[
                    (_SYSTEM_LABELS.get(system, system), system) for system in systems
                ],
                default=[current] if current in configured else [],
            )
        ]
    )
    if not answers or "systems" not in answers:
        return None
    return set(answers["systems"]).intersection(systems)


def _select_targets(args, command, install_path, dotfiles_config, root, dry_run):
    """Validate/collect targets before a direct mutation."""
    supplied = args.get("--target", []) or []
    # The default preserves direct callers which predate these docopt keys.
    non_interactive = args.get("--non-interactive", True)
    if command == "add" and args.get("--system", False):
        return {}
    rel = (
        os.path.relpath(
            operations.get_save_path(install_path, args.get("--system", False), root),
            root,
        ).replace(os.sep, "/")
        if command == "add"
        else os.path.relpath(args["_saved"], root).replace(os.sep, "/")
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
        configured = dotfiles_config["dotfiles"].get(rel, {})
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
        selected = os.path.relpath(abs_save_path, root).replace(os.sep, "/")
    approved = {}
    for rel_path in dotfiles_config["dotfiles"]:
        if selected is not None and rel_path != selected:
            continue
        install = operations.get_path(dotfiles_config, rel_path)
        if install is None:
            continue
        saved = os.path.join(root, rel_path.replace("/", os.sep))
        state = operations._link_state(saved, install)
        if state == "correct":
            continue
        if state == "missing" or force or _confirm_replace(install):
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
            rel_path = os.path.relpath(saved, root).replace(os.sep, "/")
            registered = set(dotfiles_config.get("dotfiles", {}).get(rel_path, {}))
            selected = set(selected_systems).intersection(registered)
            if not selected or selected != registered:
                return []
            return [saved]
        saved = resolved_save_path
        if saved is None:
            path = operations.normalize_path(args["<path>"])
            saved = operations._remove_save_path(path, root)
        paths = [saved]
        rel_path = os.path.relpath(saved, root).replace(os.sep, "/")
        # rm only mutates its saved object and, when registered, the current
        # platform's install path. Foreign mappings are configuration data.
        item = (
            dotfiles_config.get("dotfiles", {})
            .get(rel_path, {})
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
        if (
            selected is not None
            and os.path.relpath(selected, root).replace(os.sep, "/") != rel_path
        ):
            continue
        install = operations.get_path(dotfiles_config, rel_path)
        if install:
            paths.append(install)
    return paths


def _doctor(root):
    problems = []
    if not os.path.isdir(root):
        _fail(f"dotfiles root does not exist: {root}")
    try:
        dotfiles_config = config.load_config(root)
        problems.extend(operations.validate_config(dotfiles_config, root))
        if not problems:
            for rel_path in dotfiles_config["dotfiles"]:
                saved = os.path.join(root, rel_path.replace("/", os.sep))
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
                    if not os.path.isabs(target):
                        target = os.path.join(os.path.dirname(install), target)
                    if os.path.abspath(os.path.normpath(target)) != os.path.abspath(
                        os.path.normpath(saved)
                    ):
                        problems.append(f"wrong install link: {install}")
                    elif not os.path.exists(install):
                        problems.append(f"dangling install link: {install}")
            problems.extend(_unreferenced_saved_objects(root, dotfiles_config))
    except Exception as error:
        problems.append(f"Invalid configuration: {error}")
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(-1)
    print("No configuration problems found")


def _unreferenced_saved_objects(root, dotfiles_config):
    """Inspect only hash namespaces produced by get_save_path, never repo files."""
    managed = {
        os.path.abspath(os.path.join(root, path.replace("/", os.sep)))
        for path in dotfiles_config["dotfiles"]
    }
    problems = []
    for namespace in os.listdir(root):
        if not re.fullmatch(r"[0-9a-f]{32}", namespace):
            continue
        namespace_path = os.path.join(root, namespace)
        if not os.path.isdir(namespace_path) or os.path.islink(namespace_path):
            continue
        for current, directories, files in os.walk(namespace_path):
            for name in directories + files:
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
        _main()
    except windows.SymlinkPrivilegeError:
        _fail(
            "Windows could not create a symbolic link because the required "
            "privilege is not held. Run dfm setup, then inspect and repair any "
            "partially applied state before retrying."
        )


def _main():
    args = docopt(USAGE)
    if args.get("setup"):
        result = windows.setup_developer_mode()
        print(result.message)
        if not result.success:
            raise SystemExit(-1)
        return
    root = config.default_dotfiles_root()
    if args.get("doctor"):
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
    if dry_run:
        # Validation below is deliberately read-only to preserve zero writes.
        dotfiles_config = config.load_config(root)
        errors = operations.validate_config(dotfiles_config, root)
        if errors:
            _fail(errors[0])
        if command == "view":
            try:
                operations.plan_view(dotfiles_config, root)
            except ValueError as error:
                _fail(str(error))
            error = operations.validate_view_mutation_root(
                root
            ) or operations.validate_view_root(root, args.get("--force", False))
            if error:
                _fail(error)
            print(f"Dry-run: {command}; no changes made")
            return
        rm_save_path = None
        selected_remove_systems = None
        saved = None
        if command == "add":
            install = operations.normalize_path(args["<install_path>"])
            error = operations.validate_add(install, args.get("--system", False), root)
            if error:
                _fail(error)
        elif command == "rm":
            path = operations.normalize_path(args["<path>"])
            rm_save_path = operations.resolve_view_save_path(args["<path>"], root)
            if rm_save_path == path:
                rm_save_path = operations._remove_save_path(path, root)
            error = operations.validate_remove(path, root, rm_save_path)
            if error:
                _fail(error)
            rel_path = os.path.relpath(rm_save_path, root).replace(os.sep, "/")
            selected_remove_systems = _select_remove_systems(
                args, dotfiles_config["dotfiles"].get(rel_path, {})
            )
            if selected_remove_systems is None:
                return
            if operations.os_name() in selected_remove_systems:
                error = operations.validate_remove_destination(
                    dotfiles_config, rel_path, root, args.get("--force", False)
                )
                if error:
                    _fail(error)
        elif command == "share" or (
            command == "install" and args.get("<save_path>") is not None
        ):
            saved = operations.resolve_view_save_path(args["<save_path>"], root)
            error = operations.validate_saved_object(saved, root)
            if error:
                _fail(error)
        if command == "share":
            args["_saved"] = saved
            error = operations.validate_install_target(
                operations.normalize_path(args["<install_path>"]), root
            )
            if error:
                _fail(error)
            error = operations.validate_share_state(
                saved,
                operations.normalize_path(args["<install_path>"]),
                dotfiles_config,
                root,
            )
            if error:
                _fail(error)
        if command in ("add", "share"):
            target_install = (
                install
                if command == "add"
                else operations.normalize_path(args["<install_path>"])
            )
            _select_targets(args, command, target_install, dotfiles_config, root, True)
        if command == "install":
            error = operations.validate_install_sources(
                dotfiles_config,
                root,
                saved,
            )
            if error:
                _fail(error)
        if command != "view":
            error = operations.validate_mutation_paths(
                _direct_paths(
                    command,
                    args,
                    dotfiles_config,
                    root,
                    rm_save_path or saved,
                    selected_remove_systems,
                ),
                root,
            )
            if error:
                _fail(error)
        print(f"Dry-run: {command}; no changes made")
        return

    def run_direct():
        dotfiles_config = config.load_config(root)
        errors = operations.validate_config(dotfiles_config, root)
        if errors:
            _fail(errors[0])
        if command == "view":
            try:
                operations.plan_view(dotfiles_config, root)
            except ValueError as error:
                _fail(str(error))
            error = operations.validate_view_mutation_root(
                root
            ) or operations.validate_view_root(root, args.get("--force", False))
            if error:
                _fail(error)
        original_config = copy.deepcopy(dotfiles_config)
        share_state = None
        rm_save_path = None
        selected_remove_systems = None
        saved = None
        if command == "add":
            install = operations.normalize_path(args["<install_path>"])
            error = operations.validate_add(install, args.get("--system", False), root)
            if error:
                _fail(error)
        elif command == "rm":
            path = operations.normalize_path(args["<path>"])
            rm_save_path = operations.resolve_view_save_path(args["<path>"], root)
            if rm_save_path == path:
                rm_save_path = operations._remove_save_path(path, root)
            error = operations.validate_remove(path, root, rm_save_path)
            if error:
                _fail(error)
            rel_path = os.path.relpath(rm_save_path, root).replace(os.sep, "/")
            selected_remove_systems = _select_remove_systems(
                args, dotfiles_config["dotfiles"].get(rel_path, {})
            )
            if selected_remove_systems is None:
                return None
            if operations.os_name() in selected_remove_systems:
                error = operations.validate_remove_destination(
                    dotfiles_config, rel_path, root, args.get("--force", False)
                )
                if error:
                    _fail(error)
        elif command == "share" or (
            command == "install" and args.get("<save_path>") is not None
        ):
            saved = operations.resolve_view_save_path(args["<save_path>"], root)
            error = operations.validate_saved_object(saved, root)
            if error:
                _fail(error)
        if command == "share":
            args["_saved"] = saved
            error = operations.validate_install_target(
                operations.normalize_path(args["<install_path>"]), root
            )
            if error:
                _fail(error)
            error = operations.validate_share_state(
                saved,
                operations.normalize_path(args["<install_path>"]),
                dotfiles_config,
                root,
            )
            if error:
                _fail(error)
        targets = {}
        install_approved = None
        if command in ("add", "share"):
            target_install = (
                install
                if command == "add"
                else operations.normalize_path(args["<install_path>"])
            )
            targets = _select_targets(
                args, command, target_install, dotfiles_config, root, False
            )
        if command == "install":
            error = operations.validate_install_sources(
                dotfiles_config,
                root,
                saved,
            )
            if error:
                _fail(error)
            install_approved = _preconfirm_install(
                saved,
                dotfiles_config,
                root,
                args.get("--force", False),
            )
        # Share must not rewrite YAML when the user declines replacement.
        if command == "share":
            share_install = operations.normalize_path(args["<install_path>"])
            share_state = operations._link_state(saved, share_install)
            if share_state == "conflict" and not args.get("--force", False):
                if args.get("--non-interactive", True):
                    _fail(
                        "existing install path requires --force in non-interactive mode"
                    )
                if not _confirm_replace(share_install):
                    return None

        if command != "view":
            error = operations.validate_mutation_paths(
                _direct_paths(
                    command,
                    args,
                    dotfiles_config,
                    root,
                    rm_save_path or saved,
                    selected_remove_systems,
                ),
                root,
            )
            if error:
                _fail(error)

        def confirm(_):
            return True

        if command == "add":
            result = operations.add(
                install, args.get("--system", False), dotfiles_config, root, targets
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
            result = operations.view(dotfiles_config, root, args.get("--force", False))
        if command != "view" and (
            (command != "share" or result.config != original_config)
            and (command != "rm" or result.config != original_config)
        ):
            config.save_config(root, result.config)
        return result

    result = run_direct()
    if result is None:
        return
    _render(result)
