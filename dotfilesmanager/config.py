"""Configuration storage for dotfilesmanager."""

import errno
import os
import tempfile
from typing import Any, Dict, Optional

import yaml

from . import operations
from ._types import RawConfig


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ValueError("invalid YAML mapping key") from error
        if duplicate:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def default_dotfiles_root() -> str:
    """Return the directory that stores managed dotfiles."""
    root = os.environ.get("DFM_ROOT") or os.path.join(
        os.path.expanduser("~"), "dotfiles"
    )
    return operations.normalize_path(root)


def resolve_dotfiles_root(root: Optional[str] = None) -> str:
    """Resolve a root override, falling back to the environment/default root."""
    if root is not None:
        return operations.normalize_path(root)
    return default_dotfiles_root()


def load_config(dotfiles_root: str) -> RawConfig:
    """Load the dotfile mapping, initializing it when no config exists."""
    config_path = os.path.join(dotfiles_root, "dfm.yaml")
    if not os.path.isfile(config_path):
        return {"dotfiles": {}}
    try:
        with open(config_path, encoding="utf-8") as config_file:
            config: Any = yaml.load(config_file, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError("invalid dfm.yaml syntax") from error
    except UnicodeError as error:
        raise ValueError("invalid dfm.yaml encoding; expected UTF-8") from error
    except TypeError as error:
        raise ValueError("invalid dfm.yaml mapping key") from error
    if not isinstance(config, dict):
        raise ValueError("dfm.yaml must contain a mapping")
    if "dotfiles" not in config:
        config["dotfiles"] = {}
    normalized = _load_schema_paths(config)
    if normalized is not None:
        config["dotfiles"] = normalized
    # YAML may contain additional top-level keys; preserve them at the raw
    # boundary while exposing the validated portion through RawConfig.
    return config


def save_config(dotfiles_root: str, config: RawConfig) -> None:
    """Persist a dotfile mapping atomically (and durably where supported)."""
    config_to_save: Any = config
    normalized = _save_schema_paths(config)
    if normalized is not None:
        config_to_save = dict(config)
        config_to_save["dotfiles"] = normalized
    os.makedirs(dotfiles_root, exist_ok=True)
    config_path = os.path.join(dotfiles_root, "dfm.yaml")
    fd, temporary_path = tempfile.mkstemp(prefix=".dfm.yaml.", dir=dotfiles_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as config_file:
            config_file.write(yaml.dump(config_to_save, Dumper=yaml.SafeDumper))
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary_path, config_path)
        _sync_directory(dotfiles_root)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _canonical_saved_key(key: Any) -> Optional[str]:
    """Return a canonical YAML saved key, accepting either separator."""
    if not isinstance(key, str) or not key:
        return None
    internal = operations.canonical_save_key("files/" + key.replace("\\", "/"))
    if internal is None:
        return None
    return internal[len("files/") :]


def _load_schema_paths(config: Any) -> Optional[Dict[str, Any]]:
    """Convert YAML saved keys to the internal ``files/`` namespace."""
    if not isinstance(config, dict) or not isinstance(config.get("dotfiles"), dict):
        return None
    normalized = {}
    for saved_path, systems in config["dotfiles"].items():
        canonical = _canonical_saved_key(saved_path)
        if (
            canonical is None
            or saved_path.startswith("files/")
            or saved_path.startswith("files\\")
        ):
            raise ValueError("invalid saved path in dfm.yaml")
        internal = "files/" + canonical
        if internal in normalized:
            raise ValueError("normalized saved paths collide in dfm.yaml")
        if isinstance(systems, dict):
            normalized_systems = {}
            for system, item in systems.items():
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    item = dict(item)
                    item["path"] = item["path"].replace("\\", "/")
                elif isinstance(item, dict):
                    item = dict(item)
                normalized_systems[system] = item
            systems = normalized_systems
        normalized[internal] = systems
    return normalized


def _save_schema_paths(config: Any) -> Optional[Dict[str, Any]]:
    """Convert internal ``files/`` saved keys to YAML keys."""
    if not isinstance(config, dict) or not isinstance(config.get("dotfiles"), dict):
        return None
    normalized = {}
    for saved_path, systems in config["dotfiles"].items():
        canonical = operations.canonical_save_key(saved_path)
        if canonical is None or canonical in normalized:
            raise ValueError("invalid internal saved path")
        normalized[canonical[len("files/") :]] = systems
    return normalized


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
