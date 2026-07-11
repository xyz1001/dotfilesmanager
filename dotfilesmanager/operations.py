"""Filesystem operations for managed dotfiles.

This module deliberately does not read input, print output, or choose process
exit codes. Callers provide confirmation callbacks and render operation results.
"""

import hashlib
import ntpath
import os
import platform
import posixpath
import shutil
from dataclasses import dataclass, field


@dataclass
class OperationResult:
    """Outcome data that a CLI (or future adapter) can render."""

    config: dict
    messages: list = field(default_factory=list)


def os_name():
    return platform.system().lower()


def expanduser(path):
    if path is None:
        return None
    if os_name() != "windows":
        return os.path.expanduser(path)

    home = str(os.path.expanduser("~"))
    if path.startswith("~"):
        path = path.replace("~", home, 1)
    return path


def normalize_path(path):
    if path is None:
        return None
    return os.path.abspath(os.path.normpath(expanduser(path)))


def shrinkuser(path):
    if path is None:
        return None
    home = str(os.path.expanduser("~"))
    if path.startswith(home):
        path = path.replace(home, "~", 1)
    return path


def get_save_path(install_path, system, dotfiles_root):
    install_path = shrinkuser(install_path)
    save_dir = hashlib.md5(os.path.dirname(install_path).encode("utf8")).hexdigest()
    filename = os.path.basename(install_path)
    system_sep = os_name() if system else ""
    return os.path.join(dotfiles_root, save_dir, system_sep, filename)


def _is_within(path, directory):
    path_module = ntpath if os_name() == "windows" else os.path
    path = path_module.normcase(path_module.abspath(path_module.normpath(path)))
    directory = path_module.normcase(
        path_module.abspath(path_module.normpath(directory))
    )
    try:
        return path_module.commonpath((path, directory)) == directory
    except ValueError:
        return False


def validate_add(install_path, system, dotfiles_root):
    if not os.path.isfile(install_path) and not os.path.isdir(install_path):
        return f"{install_path} is not valid file or directory"
    if _is_within(install_path, dotfiles_root):
        return f"{install_path} cannot be in dotfiles"
    if not _is_within(install_path, os.path.expanduser("~")):
        return f"{install_path} must be in home"
    if os.path.exists(get_save_path(install_path, system, dotfiles_root)):
        return f"{install_path} has been kept in dotfiles"
    return None


def _remove_save_path(path, dotfiles_root):
    if _is_within(path, dotfiles_root):
        return os.path.abspath(os.path.normpath(path))
    target_path = os.readlink(path) if os.path.islink(path) else path
    if not os.path.isabs(target_path):
        target_path = os.path.join(os.path.dirname(path), target_path)
    return os.path.abspath(os.path.normpath(target_path))


def validate_remove(path, dotfiles_root):
    target_path = _remove_save_path(path, dotfiles_root)
    if not _is_within(target_path, dotfiles_root):
        return f"{path} is not in dotfiles"
    return None


def set_path(config, rel_save_path, install_path):
    current_os = os_name()
    config["dotfiles"].setdefault(rel_save_path, {}).setdefault(current_os, {})[
        "path"
    ] = shrinkuser(install_path)
    return config


def get_path(config, rel_save_path):
    item = config["dotfiles"].get(rel_save_path, {}).get(os_name())
    return expanduser(item["path"]) if item is not None else None


def _make_link(target, link, confirm_replace):
    if not os.path.isdir(os.path.dirname(link)):
        os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link):
        if not confirm_replace(link):
            return False
        if os.path.islink(link) or os.path.isfile(link):
            os.remove(link)
        else:
            shutil.rmtree(link)
    os.symlink(target, link)
    return True


def add(install_path, system, config, dotfiles_root):
    abs_save_path = get_save_path(install_path, system, dotfiles_root)
    os.makedirs(os.path.dirname(abs_save_path), exist_ok=True)
    shutil.move(install_path, abs_save_path)
    os.symlink(abs_save_path, install_path)
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
        os.sep, posixpath.sep
    )
    return OperationResult(
        set_path(config, rel_save_path, install_path),
        [f"Add {install_path} to {rel_save_path}"],
    )


def remove(path, config, dotfiles_root):
    abs_save_path = _remove_save_path(path, dotfiles_root)
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
        os.sep, posixpath.sep
    )
    install_path = get_path(config, rel_save_path)
    if install_path is None:
        return OperationResult(config)

    if os.path.islink(install_path):
        os.unlink(install_path)
    del config["dotfiles"][rel_save_path][os_name()]
    if config["dotfiles"][rel_save_path]:
        if os.path.isfile(abs_save_path):
            shutil.copy(abs_save_path, install_path)
        else:
            shutil.copytree(abs_save_path, install_path)
    else:
        shutil.move(abs_save_path, install_path)
        del config["dotfiles"][rel_save_path]
    abs_save_dir = os.path.dirname(abs_save_path)
    if len(os.listdir(abs_save_dir)) == 0:
        os.rmdir(abs_save_dir)
    return OperationResult(config, [f"Remove {rel_save_path}"])


def install(abs_save_path, config, dotfiles_root, confirm_replace):
    rel_save_path = None
    if abs_save_path is not None:
        rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
            os.sep, posixpath.sep
        )
        if get_path(config, rel_save_path) is None:
            return OperationResult(config, [f"{rel_save_path} is not kept in dotfiles"])

    messages = []
    for item_rel_save_path in config["dotfiles"]:
        if rel_save_path is not None and item_rel_save_path != rel_save_path:
            continue
        item_install_path = get_path(config, item_rel_save_path)
        if item_install_path is None:
            continue
        item_abs_save_path = os.path.join(dotfiles_root, item_rel_save_path).replace(
            posixpath.sep, os.sep
        )
        if _make_link(item_abs_save_path, item_install_path, confirm_replace):
            messages.append(f"Install {item_rel_save_path} -> {item_install_path}")
    return OperationResult(config, messages)


def share(abs_save_path, install_path, config, dotfiles_root, confirm_replace):
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
        os.sep, posixpath.sep
    )
    if rel_save_path not in config["dotfiles"]:
        return OperationResult(config, [f"{rel_save_path} is not kept in dotfiles"])
    config = set_path(config, rel_save_path, install_path)
    messages = []
    if _make_link(abs_save_path, install_path, confirm_replace):
        messages.append(f"share {rel_save_path} -> {install_path}")
    return OperationResult(config, messages)
