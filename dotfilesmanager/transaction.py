"""Durable, validated transaction journal for filesystem-changing commands."""

import errno
import os
import re
import shutil
import tempfile
import uuid
from stat import S_ISDIR, S_ISLNK, S_ISREG

import yaml

from . import config

JOURNAL = ".dfm-transaction.yaml"
BACKUPS = ".dfm-backups"
LOCK = ".dfm.lock"
_IDENTIFIER = re.compile(r"^[0-9a-f]{32}$")


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
        _prepare_state(self.root, self.backup_dir, self.journal_path)
        entries = []
        for number, path in enumerate(self.paths):
            exists = os.path.lexists(path)
            backup = os.path.join(self.backup_dir, str(number))
            if exists:
                _copy_snapshot(path, backup)
            entries.append({"path": path, "exists": exists, "backup": backup})
        config_path = os.path.join(self.root, "dfm.yaml")
        config_backup = os.path.join(self.backup_dir, "config")
        config_exists = os.path.exists(config_path)
        if config_exists:
            shutil.copy2(config_path, config_backup)
            _sync_file(config_backup)
        _sync_directory(self.backup_dir)
        record = {
            "version": 1,
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

    def commit(self):
        _sync_destination_paths(self.paths)
        os.unlink(self.journal_path)
        _sync_directory(self.root)
        if not self.keep_backups:
            shutil.rmtree(self.backup_dir, ignore_errors=True)

    def rollback(self):
        recover(self.root)


def _copy_snapshot(source, destination):
    if os.path.islink(source):
        os.symlink(os.readlink(source), destination)
    elif os.path.isdir(source):
        shutil.copytree(source, destination, symlinks=True)
        _sync_tree(destination)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
        _sync_file(destination)


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
    if os.path.islink(base):
        raise JournalError("transaction restore base is a symlink")
    relative = os.path.relpath(os.path.dirname(path), base)
    current = base
    for component in () if relative == "." else relative.split(os.sep):
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise JournalError("transaction restore parent is a symlink")


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
    if not isinstance(record, dict) or record.get("version") != 1:
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
    return record


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
            _copy_snapshot(entry["backup"], path)
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
