"""Command-line adapter for dotfilesmanager."""

import copy
import ctypes
import os
import re

from docopt import docopt

from . import config, operations, transaction

USAGE = """
dotfile管理工具(dotfiles manager)，dotfile指保存配置信息的文件或包含配置文件的文件夹

Usage:
    dfm add <install_path> [--system] [--dry-run] [--force] [--backup]
    dfm rm <path> [--dry-run] [--force] [--backup]
    dfm install [<save_path>] [--dry-run] [--force] [--backup]
    dfm share <save_path> <install_path> [--dry-run] [--force] [--backup]
    dfm doctor [--repair]

Options:
    -h --help  显示帮助
    --system   该dotfile和操作系统相关
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
    command = next(name for name in ("add", "rm", "install", "share") if args.get(name))
    dry_run = args.get("--dry-run", False)
    if dry_run:
        # Validation is deliberately done below; no lock is taken because even
        # creating its lock file would violate the zero-write contract.
        dotfiles_config = config.load_config(root)
        errors = operations.validate_config(dotfiles_config, root)
        if errors:
            _fail(errors[0])
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
            error = operations.validate_install_target(
                operations.normalize_path(args["<install_path>"]), root
            )
            if error:
                _fail(error)
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
        original_config = copy.deepcopy(dotfiles_config)
        share_preconfirmed = False
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
            error = operations.validate_install_target(
                operations.normalize_path(args["<install_path>"]), root
            )
            if error:
                _fail(error)
        if command == "install":
            error = operations.validate_install_sources(
                dotfiles_config,
                root,
                operations.normalize_path(args.get("<save_path>")),
            )
            if error:
                _fail(error)
        # Share must not even rewrite YAML when the user declines replacement.
        # Prompt before opening a transaction so a declined operation is a true
        # no-op (apart from the read/validation above).
        if command == "share":
            share_install = operations.normalize_path(args["<install_path>"])
            if os.path.lexists(share_install) and not args.get("--force", False):
                if not _confirm_replace(share_install):
                    return
                share_preconfirmed = True

        tx = transaction.Transaction(
            root,
            _mutation_paths(command, args, dotfiles_config, root),
            args.get("--backup", False),
        )
        tx.begin()
        confirm = (
            (lambda _: True)
            if args.get("--force", False) or share_preconfirmed
            else _confirm_replace
        )
        try:
            if command == "add":
                result = operations.add(
                    install, args.get("--system", False), dotfiles_config, root
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
                )
            else:
                result = operations.share(
                    operations.normalize_path(args["<save_path>"]),
                    operations.normalize_path(args["<install_path>"]),
                    dotfiles_config,
                    root,
                    confirm,
                )
            if command != "share" or result.config != original_config:
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
