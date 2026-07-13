"""Command-line adapter for dotfilesmanager."""

import copy
import ctypes
import os
import re
import sys

import inquirer
from docopt import docopt
from inquirer.errors import ValidationError

from . import config, operations, transaction

USAGE = """
dotfile管理工具(dotfiles manager)，dotfile指保存配置信息的文件或包含配置文件的文件夹

Usage:
    dfm add <install_path> [--system] [--non-interactive] [--target=<mapping>...] [--dry-run] [--force] [--backup]
    dfm rm <path> [--dry-run] [--force] [--backup]
    dfm install [<save_path>] [--dry-run] [--force] [--backup]
    dfm share <save_path> <install_path> [--non-interactive] [--target=<mapping>...] [--dry-run] [--force] [--backup]
    dfm view [--dry-run] [--force] [--backup]
    dfm doctor [--repair]

Options:
    -h --help  显示帮助
    --system   该dotfile和操作系统相关
    --non-interactive  Never read stdin; required for --target and non-TTY use.
    --target=<mapping>  Foreign target mapping, repeated as SYSTEM=~/path.
    --dry-run  Validate and show what would be changed without writing.
    --force    Do not ask before replacing an existing path.
    --backup   Keep the transaction's pre-change backups after commit.
    --repair   Recover an interrupted transaction.
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
_SKIP = "__skip__"


def _prompt_targets(questions):
    """Single prompt boundary, kept separate for non-TTY-safe tests."""
    try:
        return inquirer.prompt(questions)
    except (EOFError, KeyboardInterrupt):
        return None


def _target_wizard(install_path, configured, dry_run=False):
    """Collect foreign mappings through the terminal prompt adapter."""
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
        answers = _prompt_targets(
            [
                inquirer.List(
                    "path",
                    message=f"Target path for {system}",
                    choices=[*candidates, ("Custom path", _CUSTOM), ("Skip", _SKIP)],
                    default=_SKIP,
                )
            ]
        )
        if not answers or "path" not in answers:
            return None
        path = answers["path"]
        if path == _SKIP:
            continue
        if path == _CUSTOM:

            def validate(_, value, target_system=system):
                error = operations.validate_foreign_target(target_system, value)
                if error:
                    raise ValidationError(value, reason=error)
                return True

            answers = _prompt_targets(
                [
                    inquirer.Text(
                        "custom_path",
                        message=f"Custom path for {system}",
                        validate=validate,
                    )
                ]
            )
            if not answers or "custom_path" not in answers:
                return None
            path = answers["custom_path"]
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


def _select_targets(args, command, install_path, dotfiles_config, root, dry_run):
    """Validate/collect targets before a transaction is opened."""
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
    """Collect every install replacement decision before opening a transaction."""
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
        if state == "missing" or force or _confirm_replace(install):
            approved[rel_path] = state
    return approved


def _mutation_paths(command, args, dotfiles_config, root):
    """Return direct paths each operation may replace, move, or unlink."""
    if command == "add":
        install = operations.normalize_path(args["<install_path>"])
        return [
            install,
            operations.get_save_path(install, args.get("--system", False), root),
        ]
    if command == "rm":
        path = operations.normalize_path(args["<path>"])
        saved = operations._remove_save_path(path, root)
        paths = [path, saved]
        rel_path = os.path.relpath(saved, root).replace(os.sep, "/")
        # Snapshot every configured install path for this saved object.  The
        # current operation normally changes one, but this makes recovery safe
        # if a malformed/shared mapping or a future implementation touches more.
        for system, item in (
            dotfiles_config.get("dotfiles", {}).get(rel_path, {}).items()
        ):
            if system != operations.os_name():
                continue
            if isinstance(item, dict) and item.get("path"):
                paths.append(operations.normalize_path(item["path"]))
        return paths
    if command == "share":
        return [operations.normalize_path(args["<install_path>"])]
    if command == "view":
        return [os.path.join(root, operations.VIEW_DIRECTORY)]
    paths = []
    selected = operations.normalize_path(args.get("<save_path>"))
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


def _doctor(root, repair):
    with transaction.ProcessLock(root):
        problems = []
        repair_links = []
        try:
            pending = transaction.inspect(root)
        except transaction.JournalError as error:
            problems.append(f"Invalid pending transaction: {error}")
            pending = None
        if pending:
            if repair:
                try:
                    transaction.recover(root)
                    print("Recovered pending transaction")
                except (OSError, transaction.JournalError) as error:
                    problems.append(f"Could not recover pending transaction: {error}")
            else:
                problems.append("Pending transaction found; run dfm doctor --repair")
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
                        if os.path.isfile(saved) or os.path.isdir(saved):
                            repair_links.append((saved, install))
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
                            if os.path.isfile(saved) or os.path.isdir(saved):
                                repair_links.append((saved, install))
                        elif not os.path.exists(install):
                            problems.append(f"dangling install link: {install}")
                problems.extend(_unreferenced_saved_objects(root, dotfiles_config))
        except Exception as error:
            problems.append(f"Invalid configuration: {error}")
        repairable = [
            problem
            for problem in problems
            if problem.startswith("missing install link:")
            or problem.startswith("wrong install link:")
        ]
        nonrepairable = [problem for problem in problems if problem not in repairable]
        if repair and repair_links and not nonrepairable:
            tx = transaction.Transaction(root, [link for _, link in repair_links])
            tx.begin()
            try:
                for saved, link in repair_links:
                    os.makedirs(os.path.dirname(link), exist_ok=True)
                    if os.path.lexists(link):
                        os.unlink(link)
                    os.symlink(saved, link)
                tx.commit()
                print(f"Recreated {len(repair_links)} install link(s)")
                return
            except Exception:
                tx.rollback()
                raise
        if problems:
            for problem in problems:
                print(problem)
            raise SystemExit(-1)
        print("No transaction or configuration problems found")


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
    if operations.os_name() == "windows":
        if ctypes.windll.shell32.IsUserAnAdmin() == 0:
            print("dfm must run with Administrator priviledges under Windows")
            return

    args = docopt(USAGE)
    root = config.default_dotfiles_root()
    if args.get("doctor"):
        _doctor(root, args.get("--repair", False))
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
        # Validation is deliberately done below; no lock is taken because even
        # creating its lock file would violate the zero-write contract.
        dotfiles_config = config.load_config(root)
        errors = operations.validate_config(dotfiles_config, root)
        if errors:
            _fail(errors[0])
        if command == "view":
            try:
                operations.plan_view(dotfiles_config, root)
            except ValueError as error:
                _fail(str(error))
            error = operations.validate_view_root(root, args.get("--force", False))
            if error:
                _fail(error)
            print(f"Dry-run: {command}; no changes made")
            return
        if command == "add":
            install = operations.normalize_path(args["<install_path>"])
            error = operations.validate_add(install, args.get("--system", False), root)
            if error:
                _fail(error)
        elif command == "rm":
            path = operations.normalize_path(args["<path>"])
            error = operations.validate_remove(path, root)
            if error:
                _fail(error)
            saved = operations._remove_save_path(path, root)
            rel_path = os.path.relpath(saved, root).replace(os.sep, "/")
            error = operations.validate_remove_destination(
                dotfiles_config, rel_path, root, args.get("--force", False)
            )
            if error:
                _fail(error)
        elif command == "share" or (
            command == "install" and args.get("<save_path>") is not None
        ):
            saved = operations.normalize_path(args["<save_path>"])
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
                operations.normalize_path(args.get("<save_path>")),
            )
            if error:
                _fail(error)
        print(f"Dry-run: {command}; no changes made")
        return

    with transaction.ProcessLock(root):
        # Always recover before reading config or allowing a mutation.
        transaction.recover(root)
        dotfiles_config = config.load_config(root)
        errors = operations.validate_config(dotfiles_config, root)
        if errors:
            _fail(errors[0])
        if command == "view":
            try:
                operations.plan_view(dotfiles_config, root)
            except ValueError as error:
                _fail(str(error))
            error = operations.validate_view_root(root, args.get("--force", False))
            if error:
                _fail(error)
        original_config = copy.deepcopy(dotfiles_config)
        share_state = None
        if command == "add":
            install = operations.normalize_path(args["<install_path>"])
            error = operations.validate_add(install, args.get("--system", False), root)
            if error:
                _fail(error)
        elif command == "rm":
            path = operations.normalize_path(args["<path>"])
            error = operations.validate_remove(path, root)
            if error:
                _fail(error)
            saved = operations._remove_save_path(path, root)
            rel_path = os.path.relpath(saved, root).replace(os.sep, "/")
            error = operations.validate_remove_destination(
                dotfiles_config, rel_path, root, args.get("--force", False)
            )
            if error:
                _fail(error)
        elif command == "share" or (
            command == "install" and args.get("<save_path>") is not None
        ):
            saved = operations.normalize_path(args["<save_path>"])
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
                operations.normalize_path(args.get("<save_path>")),
            )
            if error:
                _fail(error)
            install_approved = _preconfirm_install(
                operations.normalize_path(args.get("<save_path>")),
                dotfiles_config,
                root,
                args.get("--force", False),
            )
        # Share must not even rewrite YAML when the user declines replacement.
        # Prompt before opening a transaction so a declined operation is a true
        # no-op (apart from the read/validation above).
        if command == "share":
            share_install = operations.normalize_path(args["<install_path>"])
            share_state = operations._link_state(saved, share_install)
            if share_state == "conflict" and not args.get("--force", False):
                if args.get("--non-interactive", True):
                    _fail(
                        "existing install path requires --force in non-interactive mode"
                    )
                if not _confirm_replace(share_install):
                    return

        tx = transaction.Transaction(
            root,
            _mutation_paths(command, args, dotfiles_config, root),
            args.get("--backup", False),
        )
        tx.begin()

        # All confirmation happened above, before tx.begin().
        def confirm(_):
            return True

        try:
            if command == "add":
                result = operations.add(
                    install, args.get("--system", False), dotfiles_config, root, targets
                )
            elif command == "rm":
                result = operations.remove(
                    path, dotfiles_config, root, args.get("--force", False)
                )
            elif command == "install":
                result = operations.install(
                    operations.normalize_path(args.get("<save_path>")),
                    dotfiles_config,
                    root,
                    confirm,
                    install_approved,
                )
            elif command == "share":
                result = operations.share(
                    operations.normalize_path(args["<save_path>"]),
                    operations.normalize_path(args["<install_path>"]),
                    dotfiles_config,
                    root,
                    confirm,
                    targets,
                    share_state,
                )
            else:
                result = operations.view(
                    dotfiles_config, root, args.get("--force", False)
                )
            if command != "view" and (
                command != "share" or result.config != original_config
            ):
                config.save_config(root, result.config)
            tx.commit()
        except Exception:
            # If restoration itself fails the journal remains for doctor/next run.
            try:
                tx.rollback()
            except Exception:
                pass
            raise
    # A message means both filesystem and YAML changes crossed the commit point.
    _render(result)
