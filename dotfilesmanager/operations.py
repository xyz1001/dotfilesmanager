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
import sys
import uuid
from dataclasses import dataclass, field
from stat import S_ISDIR

from platformdirs.macos import MacOS
from platformdirs.unix import Unix
from platformdirs.windows import Windows

from . import windows


@dataclass
class OperationResult:
    """Outcome data that a CLI (or future adapter) can render."""

    config: dict
    messages: list = field(default_factory=list)


VIEW_DIRECTORY = "view"
SUPPORTED_SYSTEMS = ("linux", "darwin", "windows", "android")
_VIEW_EXCLUDED = (
    VIEW_DIRECTORY,
    "dfm.yaml",
    ".git",
)


@dataclass(frozen=True)
class ViewEntry:
    """A validated generated link in the readable view."""

    path: str
    target: str
    is_directory: bool


def os_name():
    """Return the YAML platform key, recognizing Android before Linux."""
    get_api_level = getattr(sys, "getandroidapilevel", None)
    if get_api_level is not None:
        try:
            api_level = get_api_level()
        except Exception:
            pass
        else:
            if isinstance(api_level, int):
                return "android"
    if os.environ.get("ANDROID_ROOT") and os.environ.get("ANDROID_DATA"):
        return "android"
    system = platform.system().lower()
    return "android" if system == "android" else system


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
    return os.path.join(dotfiles_root, "files", save_dir, system_sep, filename)


def save_path_to_key(abs_save_path, dotfiles_root):
    """Convert a physical saved-object path to its dfm.yaml key."""
    return os.path.relpath(abs_save_path, dotfiles_root).replace(os.sep, posixpath.sep)


def canonical_save_key(save_key):
    """Return the canonical slash form of one saved-object key, or ``None``."""
    if (
        not isinstance(save_key, str)
        or not save_key
        or not save_key.startswith("files/")
        or "\\" in save_key
    ):
        return None
    parts = save_key.split("/")
    if (
        len(parts) not in (3, 4)
        or len(parts[1]) != 32
        or any(character not in "0123456789abcdef" for character in parts[1])
        or (len(parts) == 4 and parts[2] not in SUPPORTED_SYSTEMS)
        or any(part in ("", ".", "..") for part in parts)
    ):
        return None
    return save_key


def key_to_save_path(save_key, dotfiles_root):
    """Convert a dfm.yaml key to its physical saved-object path."""
    canonical = canonical_save_key(save_key)
    if canonical is None:
        raise ValueError("invalid saved path in dfm.yaml")
    return os.path.abspath(os.path.join(dotfiles_root, *canonical.split("/")))


def _is_save_key(save_key):
    """Return whether a YAML key names one canonical saved object."""
    return canonical_save_key(save_key) is not None


def raw_save_key(config, save_key):
    """Find the unique raw YAML key for a canonical or raw saved-object key."""
    return (
        save_key
        if canonical_save_key(save_key) and save_key in config.get("dotfiles", {})
        else None
    )


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


def _same_path(left, right):
    path_module = ntpath if os_name() == "windows" else os.path
    return path_module.normcase(path_module.abspath(left)) == path_module.normcase(
        path_module.abspath(right)
    )


def validate_mutation_paths(paths, dotfiles_root):
    """Reject protected targets and existing symlink parents before mutation."""
    root = os.path.abspath(dotfiles_root)
    if os.path.lexists(root) and _is_link_or_reparse(root):
        return f"{root} is a symbolic-link or reparse-point root"
    protected = (
        root,
        os.path.join(root, "dfm.yaml"),
        os.path.join(root, VIEW_DIRECTORY),
    )
    for path in paths:
        if not path:
            continue
        path = os.path.abspath(path)
        if _same_path(path, root) or any(
            _same_path(path, item) or _is_within(path, item) for item in protected[1:]
        ):
            return f"{path} targets protected dotfiles state"
        parent = os.path.dirname(path)
        while parent and parent != os.path.dirname(parent):
            if os.path.lexists(parent) and _is_link_or_reparse(parent):
                return f"{path} has a symbolic-link parent"
            if parent == root:
                break
            parent = os.path.dirname(parent)
    return None


def _is_link_or_reparse(path):
    if os.path.islink(path):
        return True
    if os_name() != "windows" or not os.path.lexists(path):
        return False
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)


def validate_view_mutation_root(dotfiles_root):
    """Ensure rebuilding view cannot traverse a substituted root or parent."""
    root = os.path.abspath(dotfiles_root)
    if os.path.lexists(root) and _is_link_or_reparse(root):
        return f"{root} is a symbolic-link or reparse-point root"
    parent = os.path.dirname(root)
    while parent and parent != os.path.dirname(parent):
        if os.path.lexists(parent) and _is_link_or_reparse(parent):
            return f"{root} has a symbolic-link or reparse-point parent"
        parent = os.path.dirname(parent)
    return None


def validate_add(install_path, system, dotfiles_root):
    if not os.path.isfile(install_path) and not os.path.isdir(install_path):
        return f"{install_path} is not valid file or directory"
    if _is_within(install_path, dotfiles_root):
        return f"{install_path} cannot be in dotfiles"
    if not _is_within(install_path, os.path.expanduser("~")):
        return f"{install_path} must be in home"
    if "\\" in os.path.basename(install_path):
        return f"{install_path} has an invalid saved filename"
    if system and os_name() not in SUPPORTED_SYSTEMS:
        return f"{os_name()} is not a supported system"
    save_path = get_save_path(install_path, system, dotfiles_root)
    if canonical_save_key(save_path_to_key(save_path, dotfiles_root)) is None:
        return f"{install_path} has an invalid saved path"
    if os.path.exists(save_path):
        return f"{install_path} has been kept in dotfiles"
    return None


def _remove_save_path(path, dotfiles_root):
    if _is_within(path, dotfiles_root):
        return os.path.abspath(os.path.normpath(path))
    target_path = os.readlink(path) if os.path.islink(path) else path
    if not os.path.isabs(target_path):
        target_path = os.path.join(os.path.dirname(path), target_path)
    return os.path.abspath(os.path.normpath(target_path))


def validate_remove(path, dotfiles_root, resolved_save_path=None):
    target_path = resolved_save_path or _remove_save_path(path, dotfiles_root)
    if not _is_within(target_path, os.path.join(dotfiles_root, "files")):
        return f"{path} is not in dotfiles"
    if not _is_save_key(save_path_to_key(target_path, dotfiles_root)):
        return f"{path} is not a canonical saved path"
    return None


def validate_config(config, dotfiles_root):
    """Return validation errors before configuration-derived paths are touched."""
    errors = []
    if not isinstance(config, dict) or not isinstance(config.get("dotfiles"), dict):
        return ["dfm.yaml must contain a dotfiles mapping"]
    for rel_path, systems in config["dotfiles"].items():
        if not _is_save_key(rel_path):
            errors.append("invalid saved path in dfm.yaml")
            continue
        if (
            sum(
                canonical_save_key(other) == canonical_save_key(rel_path)
                for other in config["dotfiles"]
            )
            > 1
        ):
            errors.append("ambiguous saved paths in dfm.yaml")
            continue
        if _is_view_key(rel_path):
            errors.append("view is reserved and cannot be a saved path")
            continue
        saved = key_to_save_path(rel_path, dotfiles_root)
        if _same_path(saved, dotfiles_root) or _same_path(
            saved, os.path.join(dotfiles_root, "files")
        ):
            errors.append("saved path cannot be dotfiles root")
        elif not _is_within(saved, os.path.join(dotfiles_root, "files")):
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
    if path is None or not _is_within(path, os.path.join(dotfiles_root, "files")):
        return f"{path} is not in dotfiles"
    relative = save_path_to_key(path, dotfiles_root)
    if not _is_save_key(relative):
        return f"{path} is not a canonical saved path"
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


def _encode_view_component(value):
    """Encode arbitrary config text as one portable, reversible path component."""
    return "v" + value.encode("utf-8", "surrogatepass").hex()


def _is_readable_view_component(value):
    """Recognize one component that is portable on the host's filesystem."""
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update({"com" + str(number) for number in range(1, 10)})
    reserved.update({"lpt" + str(number) for number in range(1, 10)})
    if not isinstance(value, str) or value in ("", ".", ".."):
        return False
    if any(character in "/\\" or ord(character) < 32 for character in value):
        return False
    if os.name != "nt":
        return True
    return (
        not value.endswith((".", " "))
        and value.split(".", 1)[0].casefold() not in reserved
        and not any(character in '<>:"|?*' for character in value)
    )


def _view_component(value, force_escape=False):
    if not force_escape and _is_readable_view_component(value):
        return value
    return _encode_view_component(value)


def _view_path_within(path, directory):
    """Use host filesystem rules, irrespective of the target platform."""
    try:
        return os.path.commonpath(
            (os.path.abspath(path), os.path.abspath(directory))
        ) == (os.path.abspath(directory))
    except ValueError:
        return False


def _view_projection_components(path, view_root):
    """Return host-normalized relative components for projection comparisons."""
    components = tuple(os.path.relpath(path, view_root).split(os.sep))
    return (
        tuple(component.casefold() for component in components)
        if os.name == "nt"
        else components
    )


def _view_target_path(system, path):
    """Classify a target path using its platform's lexical path conventions."""
    windows_target = system.casefold() == "windows"
    module = ntpath if windows_target else posixpath
    path = str(path)
    if windows_target:
        text = path.replace("/", "\\")
        if text.startswith("~\\"):
            namespace, tail = "home", text[2:]
        elif text.startswith("\\\\"):
            namespace, tail = "unc", text[2:]
        else:
            drive, tail = module.splitdrive(text)
            if drive and tail.startswith("\\"):
                namespace = "drive:" + drive
                tail = tail[1:]
            elif text.startswith("\\"):
                namespace, tail = "absolute", text[1:]
            else:
                namespace, tail = "legacy", text
        parts = tail.split("\\")
    else:
        if path.startswith("~/"):
            namespace, tail = "home", path[2:]
        elif path.startswith("/"):
            namespace, tail = "absolute", path[1:]
        else:
            namespace, tail = "legacy", path
        parts = tail.split("/")
    logical = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if logical and logical[-1] != "..":
                logical.pop()
            elif namespace == "legacy":
                logical.append(part)
            continue
        logical.append(part)
    return namespace, tuple(logical)


def _view_entry_path(view_root, system, namespace, parts, force_escape=False):
    path = os.path.join(
        view_root,
        _view_component(system, force_escape),
        _view_component(namespace, force_escape),
    )
    for part in parts or (".",):
        path = os.path.join(path, _view_component(part, force_escape))
    if not _view_path_within(path, view_root):
        raise ValueError("view path escapes generated view")
    return path


def _is_excluded_view_source(path, root):
    return any(
        path == os.path.join(root, name) or _is_within(path, os.path.join(root, name))
        for name in _VIEW_EXCLUDED
    )


def plan_view(config, dotfiles_root):
    """Validate and return links for every configured platform's generated view."""
    root = os.path.abspath(dotfiles_root)
    real_root = os.path.realpath(root)
    current_system = os_name()
    candidates = []
    entries = []
    logical_paths = []
    projected_paths = []
    for rel_save_path, systems in config["dotfiles"].items():
        saved = key_to_save_path(rel_save_path, root)
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
        for system, item in systems.items():
            if system == current_system:
                home = normalize_path(os.path.expanduser("~"))
                install = normalize_path(item["path"])
                if install == home:
                    raise ValueError("configured install path cannot be home itself")
                if not _is_within(install, home) or _is_within(install, root):
                    raise ValueError("configured install path is outside home")
                namespace = "home"
                parts = tuple(os.path.relpath(install, home).split(os.sep))
            else:
                namespace, parts = _view_target_path(system, item["path"])
            logical_system = (
                system.casefold() if system.casefold() == "windows" else system
            )
            logical_parts = (
                tuple(part.casefold() for part in parts)
                if system.casefold() == "windows"
                else parts
            )
            logical_namespace = (
                namespace.casefold() if system.casefold() == "windows" else namespace
            )
            logical_path = (logical_system, logical_namespace, logical_parts)
            for other_system, other_namespace, other_parts in logical_paths:
                if (
                    logical_system == other_system
                    and logical_namespace == other_namespace
                    and (
                        logical_parts[: len(other_parts)] == other_parts
                        or other_parts[: len(logical_parts)] == logical_parts
                    )
                ):
                    raise ValueError("view paths duplicate or overlap")
            candidates.append((system, namespace, parts, saved, os.path.isdir(saved)))
            logical_paths.append(logical_path)
    view_root = os.path.join(root, VIEW_DIRECTORY)
    initial_paths = [
        _view_entry_path(view_root, system, namespace, parts)
        for system, namespace, parts, _saved, _is_directory in candidates
    ]
    projection_groups = {}
    for index, view_path in enumerate(initial_paths):
        projection_key = _view_projection_components(view_path, view_root)
        projection_groups.setdefault(projection_key, []).append(index)
    escaped = {
        index
        for indexes in projection_groups.values()
        if len(indexes) > 1
        for index in indexes
    }
    for index, (system, namespace, parts, saved, is_directory) in enumerate(candidates):
        view_path = _view_entry_path(
            view_root, system, namespace, parts, force_escape=index in escaped
        )
        projected_path = _view_projection_components(view_path, view_root)
        for other in projected_paths:
            if (
                projected_path[: len(other)] == other
                or other[: len(projected_path)] == projected_path
            ):
                raise ValueError("view projection paths duplicate or overlap")
        entries.append(ViewEntry(view_path, saved, is_directory))
        projected_paths.append(projected_path)
    return entries


def resolve_view_save_path(path, dotfiles_root):
    """Resolve a direct view symlink to its canonical hash-namespaced target."""
    path = normalize_path(path)
    if path is None:
        return None
    try:
        root = os.path.abspath(dotfiles_root)
        view_root = os.path.join(root, VIEW_DIRECTORY)
        if (
            _same_path(path, view_root)
            or not _is_within(path, view_root)
            or not os.path.islink(path)
        ):
            return path
        parent = os.path.dirname(path)
        while _is_within(parent, view_root):
            if os.path.lexists(parent) and _is_link_or_reparse(parent):
                return path
            if _same_path(parent, view_root):
                break
            parent = os.path.dirname(parent)

        target = os.readlink(path)
        if not os.path.isabs(target):
            target = os.path.join(os.path.dirname(path), target)
        target = os.path.abspath(os.path.normpath(target))
        if not _is_within(target, root):
            return path
        parts = os.path.relpath(target, root).split(os.sep)
        if (
            len(parts) < 3
            or parts[0] != "files"
            or len(parts[1]) != 32
            or any(character not in "0123456789abcdef" for character in parts[1])
            or not (os.path.isfile(target) or os.path.isdir(target))
        ):
            return path
        real_root = os.path.realpath(root)
        real_target = os.path.realpath(target)
        if not _is_within(real_target, real_root) or _is_excluded_view_source(
            real_target, real_root
        ):
            return path
        return target
    except OSError:
        return path


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
    staging_root = os.path.join(dotfiles_root, ".view-staging-" + uuid.uuid4().hex)
    backup_root = os.path.join(dotfiles_root, ".view-backup-" + uuid.uuid4().hex)
    committed = False
    moved_old_view = False
    try:
        os.makedirs(staging_root)
        for entry in entries:
            relative_path = os.path.relpath(entry.path, view_root)
            staging_path = os.path.join(staging_root, relative_path)
            if not _view_path_within(staging_path, staging_root):
                raise ValueError("view staging path escapes generated view")
            os.makedirs(os.path.dirname(staging_path), exist_ok=True)
            windows.create_symlink(
                os.path.relpath(entry.target, os.path.dirname(staging_path)),
                staging_path,
                target_is_directory=entry.is_directory,
            )
        if os.path.lexists(view_root):
            os.replace(view_root, backup_root)
            moved_old_view = True
        try:
            os.replace(staging_root, view_root)
        except OSError:
            if moved_old_view:
                try:
                    os.replace(backup_root, view_root)
                except OSError as restore_error:
                    raise OSError(
                        "view replacement failed and old view could not be restored"
                    ) from restore_error
            raise
        committed = True
    except Exception:
        if not committed and os.path.lexists(staging_root):
            shutil.rmtree(staging_root)
        raise
    if os.path.lexists(backup_root):
        try:
            shutil.rmtree(backup_root)
        except OSError:
            pass
    return OperationResult(config, [f"View {len(entries)} item(s)"])


def validate_install_target(path, dotfiles_root):
    if not _is_within(path, os.path.expanduser("~")) or _is_within(path, dotfiles_root):
        return f"{path} must be in home and outside dotfiles"
    return None


def parse_target_mappings(values, current_system=None):
    """Parse repeated SYSTEM=~/path options without consulting the local OS."""
    current_system = current_system or os_name()
    targets = {}
    for value in values or ():
        if not isinstance(value, str) or "=" not in value:
            raise ValueError("target must be SYSTEM=PATH")
        system, path = value.split("=", 1)
        if system not in SUPPORTED_SYSTEMS:
            raise ValueError(f"unsupported target system: {system}")
        if system == current_system:
            raise ValueError("current platform cannot be a target")
        if system in targets:
            raise ValueError(f"duplicate target system: {system}")
        error = validate_foreign_target(system, path)
        if error:
            raise ValueError(error)
        targets[system] = path
    return targets


def validate_foreign_target(system, path):
    """Lexically validate a portable, home-relative foreign install path."""
    if system not in SUPPORTED_SYSTEMS:
        return f"unsupported target system: {system}"
    if not isinstance(path, str) or not path or "\x00" in path:
        return "target path must be a non-empty ~/ path"
    if "\\" in path:
        return "target path must use / separators"
    module = ntpath if system == "windows" else posixpath
    if not path.startswith("~/") or path == "~":
        return "target path must be below ~"
    tail = path[2:]
    if (
        module.isabs(tail)
        or module.splitdrive(tail)[0]
        or any(part in ("", ".", "..") for part in tail.split("/"))
    ):
        return "target path must be a safe path below ~"
    if system == "windows":
        in_dotfiles = path.casefold() == "~/dotfiles" or path.casefold().startswith(
            "~/dotfiles/"
        )
    else:
        in_dotfiles = path == "~/dotfiles" or path.startswith("~/dotfiles/")
    if in_dotfiles:
        return "target path cannot be in ~/dotfiles"
    return None


def is_platform_specific_save_path(rel_save_path):
    """Recognize only the canonical <md5>/<platform>/<basename> key."""
    rel_save_path = canonical_save_key(rel_save_path)
    if rel_save_path is None:
        return False
    parts = rel_save_path.split("/")
    return (
        len(parts) == 4
        and parts[0] == "files"
        and len(parts[1]) == 32
        and all(character in "0123456789abcdef" for character in parts[1])
        and parts[2] in SUPPORTED_SYSTEMS
        and bool(parts[3])
    )


def merge_targets(config, rel_save_path, targets):
    """Return a copy with compatible foreign mappings added, never replaced."""
    if is_platform_specific_save_path(rel_save_path) and targets:
        raise ValueError("platform-specific saved objects cannot have external targets")
    merged = copy.deepcopy(config)
    raw_key = raw_save_key(merged, rel_save_path)
    if raw_key is None:
        raw_key = canonical_save_key(rel_save_path)
    systems = merged["dotfiles"].setdefault(raw_key, {})
    for system, path in targets.items():
        error = validate_foreign_target(system, path)
        if error:
            raise ValueError(error)
        existing = systems.get(system)
        if existing is not None:
            if not target_paths_equal(system, existing.get("path"), path):
                raise ValueError(f"conflicting target mapping for {system}")
            continue
        systems[system] = {"path": path}
    return merged


def target_paths_equal(system, first, second):
    """Compare target data in its target platform's path semantics."""
    if not isinstance(first, str) or not isinstance(second, str):
        return False
    if system == "windows":
        return ntpath.normcase(
            ntpath.normpath(first.replace("/", "\\"))
        ) == ntpath.normcase(ntpath.normpath(second.replace("/", "\\")))
    return posixpath.normpath(first) == posixpath.normpath(second)


def _wizard_source_path(install_path):
    """Return a POSIX home-relative source even when it originated on Windows."""
    if os_name() != "windows":
        return shrinkuser(install_path)
    home = ntpath.normcase(ntpath.normpath(expanduser("~")))
    source = ntpath.normcase(ntpath.normpath(str(install_path)))
    try:
        relative = ntpath.relpath(source, home)
    except ValueError:
        return str(install_path).replace("\\", "/")
    if relative != ".." and not relative.startswith(".." + ntpath.sep):
        return "~/" + relative.replace("\\", "/")
    return str(install_path).replace("\\", "/")


_CATEGORY_ORDER = ("config", "data")
_UNIX_BASES = {
    "config": "~/.config",
    "data": "~/.local/share",
}
_MACOS_BASES = {
    "config": "~/Library/Application Support",
    "data": "~/Library/Application Support",
}
_WINDOWS_BASES = {
    "config": "~/AppData/Roaming",
    "data": "~/AppData/Local",
}


def _current_category_roots():
    """Read public category roots only for the current source classification."""
    current = os_name()
    if current in ("linux", "android"):
        provider = Unix(appname=None, ensure_exists=False)
        return {
            "config": provider.user_config_dir,
            "data": provider.user_data_dir,
        }
    if current == "darwin":
        provider = MacOS(appname=None, ensure_exists=False)
        return {
            "config": provider.user_config_dir,
            "data": provider.user_data_dir,
        }
    if current == "windows":
        roaming = Windows(appname=None, roaming=True, ensure_exists=False)
        local = Windows(appname=None, roaming=False, ensure_exists=False)
        return {
            "config": roaming.user_config_dir,
            "data": local.user_data_dir,
        }
    return {}


def _current_direct_only_roots():
    """Current roots that must not be mistaken for a CONFIG/DATA descendant."""
    current = os_name()
    if current in ("linux", "android"):
        provider = Unix(appname=None, ensure_exists=False)
        return (provider.user_cache_dir, provider.user_state_dir, provider.user_log_dir)
    if current == "darwin":
        provider = MacOS(appname=None, ensure_exists=False)
        return (provider.user_cache_dir, provider.user_log_dir)
    if current == "windows":
        return (Windows(appname=None, roaming=False, ensure_exists=False).user_log_dir,)
    return ()


def _classify_source_categories(install_path):
    """Return longest matching current-provider categories and source suffix."""
    module = ntpath if os_name() == "windows" else os.path
    source = module.normcase(module.normpath(normalize_path(install_path)))
    for root in _current_direct_only_roots():
        normalized_root = module.normcase(module.normpath(root))
        try:
            if module.commonpath((source, normalized_root)) == normalized_root:
                return (), None
        except ValueError:
            continue
    matches = []
    for category, root in _current_category_roots().items():
        normalized_root = module.normcase(module.normpath(root))
        try:
            contained = module.commonpath((source, normalized_root)) == normalized_root
        except ValueError:
            contained = False
        if contained:
            matches.append((category, normalized_root))
    if not matches:
        return (), None
    longest = max(len(root) for _, root in matches)
    selected = [(category, root) for category, root in matches if len(root) == longest]
    categories = tuple(
        category
        for category in _CATEGORY_ORDER
        if any(category == item[0] for item in selected)
    )
    suffix = module.relpath(source, selected[0][1]).replace("\\", "/")
    return categories, "" if suffix == "." else suffix


def _standard_target_path(system, category, suffix):
    """Use private append helpers with deterministic literal target bases."""
    if system in ("linux", "android"):
        return (
            Unix(appname=suffix or None, ensure_exists=False)
            ._append_app_name_and_version(_UNIX_BASES[category])
            .replace("\\", "/")
        )
    if system == "darwin":
        return (
            MacOS(appname=suffix or None, ensure_exists=False)
            ._append_app_name_and_version(_MACOS_BASES[category])
            .replace("\\", "/")
        )
    if system == "windows":
        return (
            Windows(
                appname=suffix or None,
                appauthor=False,
                roaming=category == "config",
                ensure_exists=False,
            )
            ._append_parts(_WINDOWS_BASES[category])
            .replace("\\", "/")
        )
    raise ValueError(f"unsupported target system: {system}")


def target_candidates(install_path, system):
    """Return literal foreign paths, without inspecting host directories or env."""
    source = _wizard_source_path(install_path)
    categories, suffix = _classify_source_categories(install_path)
    paths = []
    if system == "darwin":
        for category in categories:
            paths.append(_standard_target_path("linux", category, suffix))
    for category in categories:
        paths.append(_standard_target_path(system, category, suffix))
    if system != "darwin" and "config" in categories:
        paths.append(_standard_target_path("linux", "config", suffix))
    paths.append(source)
    deduplicated = []
    for path in paths:
        if path not in deduplicated:
            deduplicated.append(path)
    return [(path, path) for path in deduplicated]


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
        selected = save_path_to_key(abs_save_path, dotfiles_root)
    for rel_path in config["dotfiles"]:
        if selected is not None and canonical_save_key(rel_path) != selected:
            continue
        if get_path(config, rel_path) is None:
            continue
        saved = key_to_save_path(rel_path, dotfiles_root)
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
        expected = key_to_save_path(rel_save_path, dotfiles_root)
        if os.path.abspath(os.path.normpath(target)) != os.path.abspath(
            os.path.normpath(expected)
        ):
            return f"{install} is not a managed link; refusing to overwrite it"
    return None


def set_path(config, rel_save_path, install_path):
    current_os = os_name()
    raw_key = raw_save_key(config, rel_save_path)
    if raw_key is None:
        raw_key = canonical_save_key(rel_save_path)
    config["dotfiles"].setdefault(raw_key, {}).setdefault(current_os, {})["path"] = (
        shrinkuser(install_path)
    )
    return config


def get_path(config, rel_save_path):
    raw_key = raw_save_key(config, rel_save_path)
    item = config["dotfiles"].get(raw_key, {}).get(os_name()) if raw_key else None
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
    windows.create_symlink(target, link, target_is_directory=os.path.isdir(target))
    return True


def _git_crypt_rule(rel_save_path, is_directory):
    rule_path = str(rel_save_path).replace("\\", "/")
    return f"{rule_path}{'/**' if is_directory else ''} filter=git-crypt diff=git-crypt"


def _ensure_git_crypt_attributes(root, rel_save_path, is_directory):
    attributes_path = os.path.join(root, ".gitattributes")
    rule = _git_crypt_rule(rel_save_path, is_directory)
    if os.path.exists(attributes_path):
        contents = open(attributes_path, encoding="utf-8").read()
        if rule in contents.splitlines():
            return
    else:
        contents = ""
    with open(attributes_path, "a", encoding="utf-8") as attributes:
        if contents and not contents.endswith("\n"):
            attributes.write("\n")
        attributes.write(rule + "\n")


def _remove_git_crypt_attribute(root, rel_save_path, is_directory):
    attributes_path = os.path.join(root, ".gitattributes")
    if not os.path.exists(attributes_path):
        return
    rule = _git_crypt_rule(rel_save_path, is_directory)
    with open(attributes_path, encoding="utf-8") as attributes:
        lines = attributes.readlines()
    remaining = [line for line in lines if line.rstrip("\r\n") != rule]
    if remaining != lines:
        with open(attributes_path, "w", encoding="utf-8") as attributes:
            attributes.writelines(remaining)


def add(install_path, system, config, dotfiles_root, targets=None, encrypt=False):
    error = validate_add(install_path, system, dotfiles_root)
    if error:
        raise ValueError(error)
    if encrypt and shutil.which("git-crypt") is None:
        raise ValueError("git-crypt is not installed; install git-crypt and retry")
    abs_save_path = get_save_path(install_path, system, dotfiles_root)
    rel_save_path = save_path_to_key(abs_save_path, dotfiles_root)
    os.makedirs(dotfiles_root, exist_ok=True)
    if encrypt:
        _ensure_git_crypt_attributes(
            dotfiles_root, rel_save_path, os.path.isdir(install_path)
        )
    os.makedirs(os.path.dirname(abs_save_path), exist_ok=True)
    shutil.move(install_path, abs_save_path)
    windows.create_symlink(
        abs_save_path,
        install_path,
        target_is_directory=os.path.isdir(abs_save_path),
    )
    updated = set_path(copy.deepcopy(config), rel_save_path, install_path)
    updated = merge_targets(updated, rel_save_path, targets or {})
    return OperationResult(
        updated,
        [f"Add {install_path} to {rel_save_path}"],
    )


def remove(
    path,
    config,
    dotfiles_root,
    force=False,
    all_platforms=False,
    resolved_save_path=None,
    selected_systems=None,
):
    """Remove selected registrations, optionally using a pre-resolved saved path."""
    abs_save_path = resolved_save_path or _remove_save_path(path, dotfiles_root)
    rel_save_path = save_path_to_key(abs_save_path, dotfiles_root)
    raw_key = raw_save_key(config, rel_save_path)
    if all_platforms:
        # Preserve the historical --all behavior, including its special case
        # for entries without a current-platform registration.
        install_path = get_path(config, rel_save_path)
        if install_path is None:
            if raw_key is not None:
                is_directory = os.path.isdir(abs_save_path)
                if os.path.islink(abs_save_path) or os.path.isfile(abs_save_path):
                    os.unlink(abs_save_path)
                else:
                    shutil.rmtree(abs_save_path)
                del config["dotfiles"][raw_key]
                _remove_git_crypt_attribute(dotfiles_root, rel_save_path, is_directory)
                return OperationResult(config, [f"Remove {rel_save_path}"])
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
        is_directory = os.path.isdir(abs_save_path)
        del config["dotfiles"][raw_key][os_name()]
        shutil.move(abs_save_path, install_path)
        del config["dotfiles"][raw_key]
        _remove_git_crypt_attribute(dotfiles_root, rel_save_path, is_directory)
        return OperationResult(config, [f"Remove {rel_save_path}"])

    systems = config["dotfiles"].get(raw_key)
    if systems is None:
        return OperationResult(config)
    current = os_name()
    requested = {current} if selected_systems is None else set(selected_systems)
    selected = requested.intersection(systems)
    if not selected:
        return OperationResult(config)
    if current not in selected:
        for system in selected:
            del systems[system]
        if not systems:
            is_directory = os.path.isdir(abs_save_path)
            if os.path.islink(abs_save_path) or os.path.isfile(abs_save_path):
                os.unlink(abs_save_path)
            else:
                shutil.rmtree(abs_save_path)
            del config["dotfiles"][raw_key]
            _remove_git_crypt_attribute(dotfiles_root, rel_save_path, is_directory)
        return OperationResult(config, [f"Remove {rel_save_path}"])

    install_path = get_path(config, rel_save_path)
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
    for system in selected:
        del systems[system]
    if systems:
        if os.path.isfile(abs_save_path):
            shutil.copy(abs_save_path, install_path)
        else:
            shutil.copytree(abs_save_path, install_path)
    else:
        is_directory = os.path.isdir(abs_save_path)
        shutil.move(abs_save_path, install_path)
        del config["dotfiles"][raw_key]
        _remove_git_crypt_attribute(dotfiles_root, rel_save_path, is_directory)
    return OperationResult(config, [f"Remove {rel_save_path}"])


def install(abs_save_path, config, dotfiles_root, confirm_replace, accepted=None):
    rel_save_path = None
    if abs_save_path is not None:
        rel_save_path = save_path_to_key(abs_save_path, dotfiles_root)
        if get_path(config, rel_save_path) is None:
            return OperationResult(config, [f"{rel_save_path} is not kept in dotfiles"])
    error = validate_install_sources(config, dotfiles_root, abs_save_path)
    if error:
        return OperationResult(config, [error])

    # Ask every destructive question before changing anything.  This avoids a
    # partially-installed batch when a later replacement is declined.
    candidates = []
    for item_rel_save_path in config["dotfiles"]:
        if (
            rel_save_path is not None
            and canonical_save_key(item_rel_save_path) != rel_save_path
        ):
            continue
        item_install_path = get_path(config, item_rel_save_path)
        if item_install_path is None:
            continue
        item_abs_save_path = key_to_save_path(item_rel_save_path, dotfiles_root)
        candidates.append((item_rel_save_path, item_abs_save_path, item_install_path))
    approved = []
    for item_rel_save_path, item_abs_save_path, item_install_path in candidates:
        state = _link_state(item_abs_save_path, item_install_path)
        # An existing link to this exact saved object is already installed.
        # Keep it intact, including its relative target representation.
        if state == "correct":
            continue
        if accepted is not None and item_rel_save_path not in accepted:
            continue
        if accepted is not None and state != accepted[item_rel_save_path]:
            raise ValueError("install path changed after install preflight")
        # A broken destination symlink cannot provide a usable existing object,
        # so replace it without asking.  Other conflicts retain confirmation
        # semantics, including valid symlinks pointing at the wrong object.
        if state in ("missing", "dangling") or confirm_replace(item_install_path):
            approved.append((item_rel_save_path, item_abs_save_path, item_install_path))
    messages = []
    for item_rel_save_path, item_abs_save_path, item_install_path in approved:
        # The preflight above already obtained consent; do not prompt again.
        if _make_link(item_abs_save_path, item_install_path, lambda _: True):
            messages.append(f"Install {item_rel_save_path} -> {item_install_path}")
    return OperationResult(config, messages)


def _link_state(target, link):
    """Classify a local link without changing it."""
    if not os.path.lexists(link):
        return "missing"
    if not os.path.islink(link):
        return "conflict"
    if not os.path.exists(link):
        return "dangling"
    actual = os.readlink(link)
    module = ntpath if os_name() == "windows" else os.path
    if not module.isabs(actual):
        actual = module.join(module.dirname(link), actual)
    if module.normcase(module.normpath(actual)) == module.normcase(
        module.normpath(target)
    ):
        return "correct"
    return "conflict"


def _current_paths_equal(first, second):
    """Compare current-platform paths using the current platform's semantics."""
    if os_name() == "windows":
        return ntpath.normcase(ntpath.normpath(expanduser(first))) == ntpath.normcase(
            ntpath.normpath(second)
        )
    return normalize_path(first) == normalize_path(second)


def validate_share_state(abs_save_path, install_path, config, dotfiles_root):
    """Return an error for an immutable current mapping before mutation."""
    rel_save_path = save_path_to_key(abs_save_path, dotfiles_root)
    raw_key = raw_save_key(config, rel_save_path)
    current = (
        config.get("dotfiles", {}).get(raw_key, {}).get(os_name()) if raw_key else None
    )
    if current is not None and not _current_paths_equal(current["path"], install_path):
        return "current platform already has a different path; use dfm rm first"
    if (
        is_platform_specific_save_path(rel_save_path)
        and rel_save_path.split("/")[1] != os_name()
    ):
        return "platform-specific saved object belongs to another platform"
    return None


def share(
    abs_save_path,
    install_path,
    config,
    dotfiles_root,
    confirm_replace,
    targets=None,
    expected_state=None,
):
    rel_save_path = save_path_to_key(abs_save_path, dotfiles_root)
    raw_key = raw_save_key(config, rel_save_path)
    if raw_key is None:
        return OperationResult(config, [f"{rel_save_path} is not kept in dotfiles"])
    error = validate_saved_object(abs_save_path, dotfiles_root)
    if error:
        return OperationResult(config, [error])
    error = validate_install_target(install_path, dotfiles_root)
    if error:
        return OperationResult(config, [error])
    state_error = validate_share_state(
        abs_save_path, install_path, config, dotfiles_root
    )
    if state_error:
        raise ValueError(state_error)
    current = config["dotfiles"][raw_key].get(os_name())
    updated = merge_targets(config, rel_save_path, targets or {})
    state = _link_state(abs_save_path, install_path)
    if expected_state is not None and state != expected_state:
        raise ValueError("install path changed after share preflight")
    if state == "conflict" and not confirm_replace(install_path):
        return OperationResult(config, [])
    if state != "correct":
        _make_link(abs_save_path, install_path, lambda _: True)
    if current is None:
        updated = set_path(updated, rel_save_path, install_path)
    messages = []
    if state != "correct" or current is None or updated != config:
        messages.append(f"share {rel_save_path} -> {install_path}")
    return OperationResult(updated, messages)
