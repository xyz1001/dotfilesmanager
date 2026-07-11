"""Configuration storage for dotfilesmanager."""

import errno
import os
import tempfile

import yaml


def default_dotfiles_root():
    """Return the directory that stores managed dotfiles."""
    return os.path.join(os.path.expanduser("~"), "dotfiles")


def load_config(dotfiles_root):
    """Load the dotfile mapping, initializing it when no config exists."""
    config_path = os.path.join(dotfiles_root, "dfm.yaml")
    if not os.path.isfile(config_path):
        return {"dotfiles": {}}
    with open(config_path) as config_file:
        config = yaml.load(config_file, Loader=yaml.SafeLoader)
        if "dotfiles" not in config:
            config["dotfiles"] = {}
        return config


def save_config(dotfiles_root, config):
    """Persist a dotfile mapping atomically (and durably where supported)."""
    os.makedirs(dotfiles_root, exist_ok=True)
    config_path = os.path.join(dotfiles_root, "dfm.yaml")
    fd, temporary_path = tempfile.mkstemp(prefix=".dfm.yaml.", dir=dotfiles_root)
    try:
        with os.fdopen(fd, "w", newline="\n") as config_file:
            config_file.write(yaml.dump(config, Dumper=yaml.SafeDumper))
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary_path, config_path)
        _sync_directory(dotfiles_root)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def restore_config_bytes(dotfiles_root, contents, exists):
    """Atomically restore the exact pre-transaction configuration bytes."""
    path = os.path.join(dotfiles_root, "dfm.yaml")
    if not exists:
        if os.path.exists(path):
            os.unlink(path)
            _sync_directory(dotfiles_root)
        return
    fd, temporary_path = tempfile.mkstemp(prefix=".dfm.yaml.", dir=dotfiles_root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _sync_directory(dotfiles_root)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _sync_directory(path):
    """Flush a rename's directory entry when the platform permits it."""
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


def _directory_sync_unsupported(error):
    unsupported = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
    return error.errno in unsupported or (
        os.name == "nt" and error.errno == errno.EACCES
    )
