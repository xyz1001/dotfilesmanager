"""Filesystem operations for managed dotfiles.

This module deliberately does not read input, print output, or choose process
exit codes. Callers provide confirmation callbacks and render operation results.
"""

import copy
import hashlib
import ntpath
import os
import platform
import posixpath
import shutil
from dataclasses import dataclass, field
from stat import S_ISDIR


@dataclass
class OperationResult:
    """Outcome data that a CLI (or future adapter) can render."""

    config: dict
    messages: list = field(default_factory=list)


VIEW_DIRECTORY = "view"
_VIEW_EXCLUDED = (
    VIEW_DIRECTORY,
    ".dfm-transaction.yaml",
    ".dfm-backups",
    "dfm.yaml",
    ".dfm.lock",
    ".git",
)


@dataclass(frozen=True)
class ViewEntry:
    """A validated generated link in the readable view."""

    path: str
    target: str
    is_directory: bool


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
    if os_name() == "windows":
        path_module = ntpath
        home = path_module.normcase(path_module.normpath(expanduser("~")))
        install_path = str(install_path)
        if install_path.startswith("~"):
            install_path = install_path.replace("~", home, 1)
        install_path = path_module.normcase(path_module.normpath(install_path))
        parent = path_module.dirname(install_path)
        try:
            relative_parent = path_module.relpath(parent, home)
        except ValueError:
            # Paths on another drive cannot be represented relative to home.
            hash_path = parent
        else:
            if relative_parent == ".." or relative_parent.startswith(
                ".." + path_module.sep
            ):
                hash_path = parent
            elif relative_parent == ".":
                hash_path = "~"
            else:
                hash_path = path_module.join("~", relative_parent)
        filename = path_module.basename(install_path)
    else:
        install_path = shrinkuser(install_path)
        hash_path = os.path.dirname(install_path)
        filename = os.path.basename(install_path)
    save_dir = hashlib.md5(hash_path.encode("utf8")).hexdigest()
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


def validate_config(config, dotfiles_root):
    """Return validation errors before configuration-derived paths are touched."""
    errors = []
    if not isinstance(config, dict) or not isinstance(config.get("dotfiles"), dict):
        return ["dfm.yaml must contain a dotfiles mapping"]
    for rel_path, systems in config["dotfiles"].items():
        if not isinstance(rel_path, str) or not rel_path or os.path.isabs(rel_path):
            errors.append("invalid saved path in dfm.yaml")
            continue
        if _is_view_key(rel_path):
            errors.append("view is reserved and cannot be a saved path")
            continue
        saved = os.path.abspath(
            os.path.join(dotfiles_root, rel_path.replace("/", os.sep))
        )
        if not _is_within(saved, dotfiles_root):
            errors.append("saved path escapes dotfiles root")
        elif _is_view_filesystem_path(
            os.path.relpath(saved, dotfiles_root)
        ) or _is_within(
            os.path.realpath(saved),
            os.path.realpath(os.path.join(dotfiles_root, VIEW_DIRECTORY)),
        ):
            errors.append("view is reserved and cannot be a saved path")
        if not isinstance(systems, dict):
            errors.append("invalid system mapping in dfm.yaml")
            continue
        for system, item in systems.items():
            if (
                not isinstance(system, str)
                or not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
            ):
                errors.append("invalid install path in dfm.yaml")
                continue
            # Foreign-platform records are data only: do not reject a valid
            # macOS/Windows path merely because it is not meaningful locally.
            if system == os_name():
                install = normalize_path(item["path"])
                if not _is_within(install, os.path.expanduser("~")) or _is_within(
                    install, dotfiles_root
                ):
                    errors.append("configured install path is outside home")
    return errors


def validate_save_path(path, dotfiles_root):
    if path is None or not _is_within(path, dotfiles_root):
        return f"{path} is not in dotfiles"
    relative = os.path.relpath(path, dotfiles_root)
    if _is_view_filesystem_path(relative) or _is_within(
        os.path.realpath(path),
        os.path.realpath(os.path.join(dotfiles_root, VIEW_DIRECTORY)),
    ):
        return f"{path} is in the reserved view namespace"
    return None


def _is_view_key(path):
    """YAML keys are POSIX paths, including when DFM runs on Windows."""
    parts = path.split("/")
    return bool(parts) and parts[0] == VIEW_DIRECTORY


def _is_view_filesystem_path(path):
    """Filesystem paths use only the active platform's path separator."""
    parts = os.path.normpath(path).split(os.sep)
    return bool(parts) and parts[0] == VIEW_DIRECTORY


def _is_safe_system_component(system):
    return (
        isinstance(system, str)
        and system not in ("", ".", "..")
        and "/" not in system
        and "\\" not in system
    )


def _is_excluded_view_source(path, root):
    return any(
        path == os.path.join(root, name) or _is_within(path, os.path.join(root, name))
        for name in _VIEW_EXCLUDED
    )


def plan_view(config, dotfiles_root):
    """Validate and return current-platform links for the generated view."""
    root = os.path.abspath(dotfiles_root)
    real_root = os.path.realpath(root)
    home = normalize_path(os.path.expanduser("~"))
    system = os_name()
    if not _is_safe_system_component(system):
        raise ValueError("current system name is not a safe path component")
    entries = []
    seen = []
    for rel_save_path, systems in config["dotfiles"].items():
        item = systems.get(system)
        if item is None:
            continue
        install = normalize_path(item["path"])
        if install == home:
            raise ValueError("configured install path cannot be home itself")
        if not _is_within(install, home) or _is_within(install, root):
            raise ValueError("configured install path is outside home")
        relative_install = os.path.relpath(install, home)
        view_path = os.path.join(root, VIEW_DIRECTORY, system, "home", relative_install)
        for other in seen:
            if _is_within(view_path, other) or _is_within(other, view_path):
                raise ValueError("view paths duplicate or overlap")
        saved = os.path.abspath(os.path.join(root, rel_save_path.replace("/", os.sep)))
        error = validate_saved_object(saved, root)
        if error:
            raise ValueError(error)
        real_saved = os.path.realpath(saved)
        if (
            not _is_within(real_saved, real_root)
            or _is_excluded_view_source(saved, root)
            or _is_excluded_view_source(real_saved, real_root)
        ):
            raise ValueError("saved object is not a safe canonical object")
        entries.append(ViewEntry(view_path, saved, os.path.isdir(saved)))
        seen.append(view_path)
    return entries


def validate_view_root(dotfiles_root, force=False):
    """A view root can only be absent or an actual directory."""
    view_root = os.path.join(dotfiles_root, VIEW_DIRECTORY)
    if not os.path.lexists(view_root):
        return None
    if os.path.islink(view_root) or not S_ISDIR(os.lstat(view_root).st_mode):
        return "view must be a real directory or not exist"
    if not force:
        return "view already exists; use --force to rebuild it"
    return None


def view(config, dotfiles_root, force=False):
    """Rebuild the generated readable view from a prevalidated configuration."""
    entries = plan_view(config, dotfiles_root)
    error = validate_view_root(dotfiles_root, force)
    if error:
        raise ValueError(error)
    view_root = os.path.join(dotfiles_root, VIEW_DIRECTORY)
    if os.path.lexists(view_root):
        shutil.rmtree(view_root)
    os.makedirs(view_root)
    for entry in entries:
        os.makedirs(os.path.dirname(entry.path), exist_ok=True)
        os.symlink(
            os.path.relpath(entry.target, os.path.dirname(entry.path)),
            entry.path,
            target_is_directory=entry.is_directory,
        )
    return OperationResult(config, [f"View {len(entries)} item(s)"])


def validate_install_target(path, dotfiles_root):
    if not _is_within(path, os.path.expanduser("~")) or _is_within(path, dotfiles_root):
        return f"{path} must be in home and outside dotfiles"
    return None


def validate_saved_object(path, dotfiles_root):
    error = validate_save_path(path, dotfiles_root)
    if error:
        return error
    if not os.path.isfile(path) and not os.path.isdir(path):
        return f"{path} is not a supported saved file or directory"
    return None


def validate_install_sources(config, dotfiles_root, abs_save_path=None):
    """Ensure every current-platform object to install exists before prompts."""
    selected = None
    if abs_save_path is not None:
        selected = os.path.relpath(abs_save_path, dotfiles_root).replace(os.sep, "/")
    for rel_path in config["dotfiles"]:
        if selected is not None and rel_path != selected:
            continue
        if get_path(config, rel_path) is None:
            continue
        saved = os.path.join(dotfiles_root, rel_path.replace("/", os.sep))
        error = validate_saved_object(saved, dotfiles_root)
        if error:
            return error
    return None


def validate_remove_destination(config, rel_save_path, dotfiles_root=None, force=False):
    """Do not let rm overwrite an unrelated file at its install destination."""
    install = get_path(config, rel_save_path)
    if (
        not force
        and install is not None
        and os.path.lexists(install)
        and not os.path.islink(install)
    ):
        return f"{install} is not a managed link; refusing to overwrite it"
    if (
        not force
        and install is not None
        and os.path.islink(install)
        and dotfiles_root is not None
    ):
        target = os.readlink(install)
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(install), target)
        expected = os.path.join(dotfiles_root, rel_save_path.replace("/", os.sep))
        if os.path.abspath(os.path.normpath(target)) != os.path.abspath(
            os.path.normpath(expected)
        ):
            return f"{install} is not a managed link; refusing to overwrite it"
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


def remove(path, config, dotfiles_root, force=False):
    abs_save_path = _remove_save_path(path, dotfiles_root)
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
        os.sep, posixpath.sep
    )
    install_path = get_path(config, rel_save_path)
    if install_path is None:
        return OperationResult(config)

    error = validate_remove_destination(config, rel_save_path, dotfiles_root, force)
    if error:
        raise ValueError(error)

    if os.path.lexists(install_path):
        if not os.path.islink(install_path) and not force:
            raise ValueError(
                f"{install_path} is not a managed link; refusing to overwrite it"
            )
        if os.path.islink(install_path) or os.path.isfile(install_path):
            os.unlink(install_path)
        else:
            shutil.rmtree(install_path)
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
    error = validate_install_sources(config, dotfiles_root, abs_save_path)
    if error:
        return OperationResult(config, [error])

    # Ask every destructive question before changing anything.  This avoids a
    # partially-installed batch when a later replacement is declined.
    candidates = []
    for item_rel_save_path in config["dotfiles"]:
        if rel_save_path is not None and item_rel_save_path != rel_save_path:
            continue
        item_install_path = get_path(config, item_rel_save_path)
        if item_install_path is None:
            continue
        item_abs_save_path = os.path.join(dotfiles_root, item_rel_save_path).replace(
            posixpath.sep, os.sep
        )
        candidates.append((item_rel_save_path, item_abs_save_path, item_install_path))
    accepted = []
    for item_rel_save_path, item_abs_save_path, item_install_path in candidates:
        if not os.path.lexists(item_install_path) or confirm_replace(item_install_path):
            accepted.append((item_rel_save_path, item_abs_save_path, item_install_path))
    messages = []
    for item_rel_save_path, item_abs_save_path, item_install_path in accepted:
        # The preflight above already obtained consent; do not prompt again.
        if _make_link(item_abs_save_path, item_install_path, lambda _: True):
            messages.append(f"Install {item_rel_save_path} -> {item_install_path}")
    return OperationResult(config, messages)


def share(abs_save_path, install_path, config, dotfiles_root, confirm_replace):
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root).replace(
        os.sep, posixpath.sep
    )
    if rel_save_path not in config["dotfiles"]:
        return OperationResult(config, [f"{rel_save_path} is not kept in dotfiles"])
    error = validate_saved_object(abs_save_path, dotfiles_root)
    if error:
        return OperationResult(config, [error])
    error = validate_install_target(install_path, dotfiles_root)
    if error:
        return OperationResult(config, [error])
    messages = []
    if _make_link(abs_save_path, install_path, confirm_replace):
        # Do not alter the mapping when the replacement was declined.
        config = set_path(copy.deepcopy(config), rel_save_path, install_path)
        messages.append(f"share {rel_save_path} -> {install_path}")
    return OperationResult(config, messages)
