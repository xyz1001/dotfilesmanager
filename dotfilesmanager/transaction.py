"""Durable, validated transaction journal for filesystem-changing commands."""

import errno
import ntpath
import os
import posixpath
import re
import shutil
import stat
import tempfile
import uuid
from stat import S_ISDIR, S_ISLNK, S_ISREG

import yaml

from . import config, windows

JOURNAL = ".dfm-transaction.yaml"
BACKUPS = ".dfm-backups"
LOCK = ".dfm.lock"
_IDENTIFIER = re.compile(r"^[0-9a-f]{32}$")
_REPARSE_POINT = 0x400
_REPARSE_TAG_SYMLINK = 0xA000000C
_REPARSE_TAG_MOUNT_POINT = 0xA0000003


class JournalError(RuntimeError):
    """A journal is malformed or escapes the transaction storage area."""


def _sync_file(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_directory(path):
    """Sync a directory, tolerating only platforms without directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as error:
        if _directory_sync_unsupported(error):
            return
        raise
    try:
        try:
            os.fsync(fd)
        except OSError as error:
            if not _directory_sync_unsupported(error):
                raise
    finally:
        os.close(fd)


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _atomic_yaml(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".dfm-journal.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", newline="\n") as handle:
            yaml.safe_dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(os.path.dirname(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProcessLock:
    """Advisory exclusive lock, held for recovery and a complete mutation."""

    def __init__(self, root):
        self.root = root
        self.handle = None

    def __enter__(self):
        if os.path.lexists(self.root):
            _reject_symlink(self.root)
        else:
            os.makedirs(self.root, exist_ok=True)
        _require_directory(self.root, "transaction root")
        lock_path = os.path.join(self.root, LOCK)
        if os.path.lexists(lock_path):
            _reject_symlink(lock_path)
        self.handle = open(lock_path, "a+")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback
            import msvcrt

            self.handle.seek(0)
            self.handle.write("0")
            self.handle.flush()
            # locking() uses the current file position.  Always lock byte 0.
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *unused):
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


class Transaction:
    """Snapshot direct mutation paths and config before performing an operation."""

    def __init__(self, root, paths, keep_backups=False):
        self.root = os.path.abspath(root)
        self.paths = list(
            dict.fromkeys(os.path.abspath(path) for path in paths if path)
        )
        self.keep_backups = keep_backups
        self.identifier = uuid.uuid4().hex
        self.backup_dir = os.path.join(self.root, BACKUPS, self.identifier)
        self.journal_path = os.path.join(self.root, JOURNAL)

    def begin(self):
        if os.path.exists(self.journal_path):
            raise JournalError("pending transaction must be recovered first")
        _validate_mutation_paths(self.paths, self.root)
        try:
            _prepare_state(self.root, self.backup_dir, self.journal_path)
            entries = []
            for number, path in enumerate(self.paths):
                exists = os.path.lexists(path)
                backup = os.path.join(self.backup_dir, str(number))
                descriptor = _describe_snapshot(path) if exists else None
                if exists:
                    _copy_snapshot(path, backup, descriptor)
                    _validate_backup_descriptor(backup, descriptor)
                entries.append(
                    {
                        "path": path,
                        "exists": exists,
                        "backup": backup,
                        "snapshot": descriptor,
                    }
                )
            config_path = os.path.join(self.root, "dfm.yaml")
            config_backup = os.path.join(self.backup_dir, "config")
            config_exists = os.path.exists(config_path)
            if config_exists:
                shutil.copy2(config_path, config_backup)
                _sync_file(config_backup)
            _sync_directory(self.backup_dir)
            record = {
                "version": 2,
                "id": self.identifier,
                "backup_dir": self.backup_dir,
                "config": {
                    "path": config_path,
                    "exists": config_exists,
                    "backup": config_backup,
                },
                "paths": entries,
            }
            # This atomic manifest is written only after every snapshot is durable.
            _atomic_yaml(os.path.join(self.backup_dir, "manifest.yaml"), record)
            _atomic_yaml(self.journal_path, record)
        except Exception:
            # Before a journal exists nothing can recover these private snapshots.
            if not os.path.exists(self.journal_path):
                shutil.rmtree(self.backup_dir, ignore_errors=True)
            raise

    def commit(self):
        _sync_destination_paths(self.paths)
        os.unlink(self.journal_path)
        _sync_directory(self.root)
        if not self.keep_backups:
            shutil.rmtree(self.backup_dir, ignore_errors=True)

    def rollback(self):
        recover(self.root)


def _copy_snapshot(source, destination, descriptor=None):
    if _is_windows():
        descriptor = descriptor or _describe_snapshot(source)
        _copy_windows_snapshot(source, destination, descriptor)
        return
    if os.path.islink(source):
        windows.create_symlink(
            os.readlink(source),
            destination,
            target_is_directory=_symlink_target_is_directory(source),
        )
    elif os.path.isdir(source):
        try:
            shutil.copytree(source, destination, symlinks=True)
        except shutil.Error:
            # copytree turns nested failures into strings.  Without a typed
            # OSError, it is unsafe to diagnose this as a privilege failure.
            raise
        except OSError as error:
            if windows.is_privilege_not_held(error):
                raise windows.SymlinkPrivilegeError(*error.args) from error
            raise
        _sync_tree(destination)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
        _sync_file(destination)


def _symlink_target_is_directory(source):
    """Legacy fallback when a pre-metadata journal must recreate a link."""
    target = os.readlink(source)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(source), target)
    return os.path.isdir(target)


def _link_is_directory(source):
    """Read Windows' reparse-point directory bit without following the link."""
    attributes = getattr(os.lstat(source), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10))


def _is_windows():
    return os.name == "nt"


def _windows_kind(path, reject_mount_points=False):
    """Classify Windows reparse points without following them."""
    status = os.lstat(path)
    attributes = getattr(status, "st_file_attributes", 0)
    if attributes & _REPARSE_POINT:
        tag = getattr(status, "st_reparse_tag", None)
        if tag == _REPARSE_TAG_SYMLINK:
            return "symlink"
        if tag == _REPARSE_TAG_MOUNT_POINT:
            if reject_mount_points:
                raise JournalError("junctions are not valid transaction backups")
            return "directory"
        raise JournalError("unsupported Windows reparse point in transaction snapshot")
    # The fallback permits Linux unit tests to mock Windows behavior. Real
    # Windows links always expose a reparse tag through lstat.
    if os.path.islink(path):
        return "symlink"
    return "directory" if S_ISDIR(status.st_mode) else "file"


def _canonical_key(parts):
    key = "/".join(parts)
    if not _valid_nested_key(key):
        raise JournalError("transaction nested link key is invalid")
    return key


def _valid_nested_key(key):
    if not isinstance(key, str) or not key or "\x00" in key:
        return False
    if "\\" in key or ":" in key or key.startswith("/"):
        return False
    if posixpath.isabs(key) or ntpath.isabs(key) or ntpath.splitdrive(key)[0]:
        return False
    return all(part not in ("", ".", "..") for part in key.split("/"))


def _describe_snapshot(path, reject_mount_points=False):
    if _is_windows():
        kind = _windows_kind(path, reject_mount_points)
    elif os.path.islink(path):
        kind = "symlink"
    elif os.path.isdir(path):
        kind = "directory"
    else:
        kind = "file"
    descriptor = {"kind": kind, "nested_symlinks": {}}
    if kind == "symlink":
        descriptor["link_is_directory"] = _link_is_directory(path)
    if kind == "directory":
        _describe_nested_links(
            path, (), descriptor["nested_symlinks"], reject_mount_points
        )
    return descriptor


def _describe_nested_links(directory, parts, links, reject_mount_points=False):
    for entry in os.scandir(directory):
        path = entry.path
        kind = (
            _windows_kind(path, reject_mount_points)
            if _is_windows()
            else (
                "symlink"
                if os.path.islink(path)
                else "directory"
                if entry.is_dir()
                else "file"
            )
        )
        current = (*parts, entry.name)
        if kind == "symlink":
            links[_canonical_key(current)] = _link_is_directory(path)
        elif kind == "directory":
            _describe_nested_links(path, current, links, reject_mount_points)


def _copy_windows_snapshot(source, destination, descriptor):
    kind = descriptor["kind"]
    actual = _windows_kind(source)
    if actual != kind:
        raise JournalError("transaction snapshot changed while being copied")
    if kind == "symlink":
        if _link_is_directory(source) != descriptor["link_is_directory"]:
            raise JournalError("transaction symlink type changed while being copied")
        windows.create_symlink(
            os.readlink(source),
            destination,
            target_is_directory=descriptor["link_is_directory"],
        )
        return
    if kind == "file":
        shutil.copy2(source, destination, follow_symlinks=False)
        _sync_file(destination)
        return
    os.mkdir(destination)
    consumed = set()
    for entry in os.scandir(source):
        consumed.update(
            _copy_windows_child(
                entry.path,
                os.path.join(destination, entry.name),
                entry.name,
                descriptor["nested_symlinks"],
            )
        )
    if consumed != set(descriptor["nested_symlinks"]):
        raise JournalError("transaction snapshot link disappeared while being copied")
    shutil.copystat(source, destination, follow_symlinks=False)
    _sync_directory(destination)


def _copy_windows_child(source, destination, name, links):
    kind = _windows_kind(source)
    exact_link = name in links
    nested = _nested_link_descriptors(links, name)
    if kind == "symlink":
        if not exact_link or nested:
            raise JournalError("transaction snapshot link descriptor is incomplete")
        if _link_is_directory(source) != links[name]:
            raise JournalError("transaction symlink type changed while being copied")
        windows.create_symlink(
            os.readlink(source), destination, target_is_directory=links[name]
        )
        return {name}
    if exact_link:
        raise JournalError("transaction snapshot link changed while being copied")
    if kind == "file":
        if nested:
            raise JournalError("transaction snapshot link changed while being copied")
        shutil.copy2(source, destination, follow_symlinks=False)
        _sync_file(destination)
        return set()
    os.mkdir(destination)
    consumed = set()
    for entry in os.scandir(source):
        consumed.update(
            _copy_windows_child(
                entry.path, os.path.join(destination, entry.name), entry.name, nested
            )
        )
    expected = set(nested)
    if consumed != expected:
        raise JournalError("transaction snapshot link disappeared while being copied")
    shutil.copystat(source, destination, follow_symlinks=False)
    _sync_directory(destination)
    return {name + "/" + key for key in consumed}


def _nested_link_descriptors(links, name):
    prefix = name + "/"
    return {
        key[len(prefix) :]: value
        for key, value in links.items()
        if key.startswith(prefix)
    }


def _sync_tree(directory):
    for current, _, files in os.walk(directory):
        for name in files:
            path = os.path.join(current, name)
            if S_ISLNK(os.lstat(path).st_mode):
                continue
            _sync_file(path)
        _sync_directory(current)


def _directory_sync_unsupported(error):
    unsupported = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
    return error.errno in unsupported or (
        os.name == "nt" and error.errno == errno.EACCES
    )


def _within(path, directory):
    try:
        return os.path.commonpath(
            (os.path.abspath(path), os.path.abspath(directory))
        ) == os.path.abspath(directory)
    except ValueError:
        return False


def _reject_symlink(path):
    """Use lstat so transaction metadata never follows a substituted link."""
    if os.path.lexists(path) and os.path.islink(path):
        raise JournalError(f"transaction state path is a symlink: {path}")


def _lstat(path, label):
    try:
        return os.lstat(path)
    except OSError as error:
        raise JournalError(f"{label} is unavailable") from error


def _require_directory(path, label):
    status = _lstat(path, label)
    if os.path.islink(path) or not S_ISDIR(status.st_mode):
        raise JournalError(f"{label} is not a real directory")


def _require_regular(path, label):
    status = _lstat(path, label)
    if os.path.islink(path) or not S_ISREG(status.st_mode):
        raise JournalError(f"{label} is not a regular file")


def _prepare_state(root, backup_dir, journal_path):
    """Create and validate transaction state without traversing symlinks."""
    _require_directory(root, "transaction root")
    backups = os.path.join(root, BACKUPS)
    if os.path.lexists(backups):
        _require_directory(backups, "transaction backup root")
    else:
        os.mkdir(backups)
    _require_directory(backups, "transaction backup root")
    if os.path.lexists(journal_path):
        _reject_symlink(journal_path)
        raise JournalError("pending transaction must be recovered first")
    if os.path.lexists(backup_dir):
        raise JournalError("transaction backup directory already exists")
    os.mkdir(backup_dir)
    _require_directory(backup_dir, "transaction backup directory")


def _is_protected(path, root):
    protected = (
        os.path.join(root, "dfm.yaml"),
        os.path.join(root, LOCK),
        os.path.join(root, JOURNAL),
        os.path.join(root, BACKUPS),
    )
    return any(path == item or _within(path, item) for item in protected)


def _safe_parent(path, root):
    """Reject existing symlink parents before a restore can traverse them."""
    base = root if _within(path, root) else os.path.expanduser("~")
    if _is_link_or_reparse(base):
        raise JournalError("transaction restore base is a symlink")
    relative = os.path.relpath(os.path.dirname(path), base)
    current = base
    for component in () if relative == "." else relative.split(os.sep):
        current = os.path.join(current, component)
        if os.path.lexists(current) and _is_link_or_reparse(current):
            raise JournalError("transaction restore parent is a symlink")


def _is_link_or_reparse(path):
    if os.path.islink(path):
        return True
    if not _is_windows() or not os.path.lexists(path):
        return False
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE_POINT)


def _sync_destination_parents(paths):
    for path in paths:
        parent = os.path.dirname(path)
        if os.path.isdir(parent):
            _sync_directory(parent)


def _sync_destination_paths(paths):
    """Flush copied/moved content and directory entries before journal deletion."""
    for path in paths:
        if os.path.islink(path):
            continue
        if os.path.isdir(path):
            _sync_tree(path)
        elif os.path.isfile(path):
            _sync_file(path)
    _sync_destination_parents(paths)


def _validate_mutation_paths(paths, root):
    """Use the same containment rules before journaling and before recovery."""
    for path in paths:
        if not _within(path, root) and not _within(path, os.path.expanduser("~")):
            raise JournalError("transaction path escapes managed locations")
        if _is_protected(path, root):
            raise JournalError("transaction path targets protected state")
        _safe_parent(path, root)


def _read_and_validate(root):
    root = os.path.abspath(root)
    journal_path = os.path.join(root, JOURNAL)
    _require_directory(root, "transaction root")
    _require_regular(journal_path, "transaction journal")
    try:
        with open(journal_path) as handle:
            record = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise JournalError("cannot read pending transaction journal") from error
    if not isinstance(record, dict) or record.get("version") not in (1, 2):
        raise JournalError("invalid pending transaction journal")
    identifier = record.get("id")
    backup_dir = record.get("backup_dir")
    expected_dir = (
        os.path.join(root, BACKUPS, identifier) if isinstance(identifier, str) else None
    )
    if (
        not isinstance(identifier, str)
        or not _IDENTIFIER.match(identifier)
        or backup_dir != expected_dir
    ):
        raise JournalError("transaction backup location is invalid")
    backups = os.path.join(root, BACKUPS)
    _require_directory(backups, "transaction backup root")
    _require_directory(backup_dir, "transaction backup directory")
    manifest_path = os.path.join(backup_dir, "manifest.yaml")
    _require_regular(manifest_path, "transaction backup manifest")
    try:
        with open(manifest_path) as handle:
            manifest = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise JournalError("transaction backup manifest is missing") from error
    if manifest != record:
        raise JournalError("transaction backup manifest does not match journal")
    config_entry = record.get("config")
    entries = record.get("paths")
    if not isinstance(config_entry, dict) or not isinstance(entries, list):
        raise JournalError("transaction journal has invalid entries")
    if config_entry.get("path") != os.path.join(root, "dfm.yaml"):
        raise JournalError("transaction config path is invalid")
    if not isinstance(config_entry.get("exists"), bool) or config_entry.get(
        "backup"
    ) != os.path.join(backup_dir, "config"):
        raise JournalError("transaction config snapshot is invalid")
    if config_entry["exists"] and (
        not os.path.isfile(config_entry["backup"])
        or os.path.islink(config_entry["backup"])
    ):
        raise JournalError("transaction config snapshot is missing")
    seen = set()
    for number, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise JournalError("transaction path entry is invalid")
        path = entry["path"]
        if not os.path.isabs(path) or path in seen or path == root:
            raise JournalError("transaction path is invalid")
        # A journal may only restore the repository itself or the current
        # user's home tree; never let a corrupt journal target arbitrary paths.
        _validate_mutation_paths((path,), root)
        seen.add(path)
        if not isinstance(entry.get("exists"), bool) or entry.get(
            "backup"
        ) != os.path.join(backup_dir, str(number)):
            raise JournalError("transaction snapshot location is invalid")
        if entry["exists"] and not os.path.lexists(entry["backup"]):
            raise JournalError("transaction snapshot is missing")
        if record["version"] == 1:
            if any(
                key in entry
                for key in ("snapshot", "link_is_directory", "nested_link_types")
            ):
                raise JournalError("version 1 transaction has version 2 fields")
            if entry["exists"]:
                # Derive and validate now, before recovery can remove a target.
                descriptor = _describe_snapshot(
                    entry["backup"], reject_mount_points=_is_windows()
                )
                _validate_descriptor(descriptor, True)
                entry["_legacy_snapshot"] = descriptor
        else:
            _validate_descriptor(entry.get("snapshot"), entry["exists"])
            if entry["exists"]:
                _validate_backup_descriptor(entry["backup"], entry["snapshot"])
    return record


def _validate_descriptor(descriptor, exists):
    if not exists:
        if descriptor is not None:
            raise JournalError("missing transaction snapshot has a descriptor")
        return
    if not isinstance(descriptor, dict):
        raise JournalError("transaction snapshot descriptor is invalid")
    kind = descriptor.get("kind")
    links = descriptor.get("nested_symlinks")
    expected = {"kind", "nested_symlinks"}
    if kind == "symlink":
        expected.add("link_is_directory")
    if kind not in ("file", "directory", "symlink") or set(descriptor) != expected:
        raise JournalError("transaction snapshot descriptor is invalid")
    if kind == "symlink" and not isinstance(descriptor["link_is_directory"], bool):
        raise JournalError("transaction snapshot descriptor is invalid")
    if not isinstance(links, dict):
        raise JournalError("transaction snapshot descriptor is invalid")
    for key, value in links.items():
        if not _valid_nested_key(key) or not isinstance(value, bool):
            raise JournalError("transaction snapshot descriptor is invalid")
        if any(other != key and other.startswith(key + "/") for other in links):
            raise JournalError("transaction snapshot descriptor is invalid")
    if kind != "directory" and links:
        raise JournalError("transaction snapshot descriptor is invalid")


def _validate_backup_descriptor(backup, descriptor):
    """Compare real backup nodes before recovery can remove a destination."""
    actual = _describe_snapshot(backup, reject_mount_points=_is_windows())
    if actual != descriptor:
        raise JournalError("transaction backup does not match its descriptor")


def recover(root):
    """Restore an interrupted transaction without consuming its snapshots.

    A failed restore leaves the journal and every snapshot intact, so retrying is
    safe.  The journal is removed only after all path and config restores finish.
    """
    root = os.path.abspath(root)
    journal_path = os.path.join(root, JOURNAL)
    if not os.path.lexists(journal_path):
        return False
    record = _read_and_validate(root)  # validate before deleting any user path
    for entry in record["paths"]:
        path = entry["path"]
        if os.path.lexists(path):
            _remove(path)
        if entry["exists"]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            descriptor = (
                entry["snapshot"]
                if record["version"] == 2
                else entry["_legacy_snapshot"]
            )
            _copy_snapshot(entry["backup"], path, descriptor)
    config_entry = record["config"]
    contents = b""
    if config_entry["exists"]:
        with open(config_entry["backup"], "rb") as handle:
            contents = handle.read()
    config.restore_config_bytes(root, contents, config_entry["exists"])
    # Commit the rollback only after every restore is durable.
    _sync_destination_paths(entry["path"] for entry in record["paths"])
    os.unlink(journal_path)
    _sync_directory(root)
    shutil.rmtree(record["backup_dir"], ignore_errors=True)
    return True


def inspect(root):
    """Return a validated pending journal, None, or raise JournalError."""
    if not os.path.lexists(os.path.join(root, JOURNAL)):
        return None
    return _read_and_validate(root)
