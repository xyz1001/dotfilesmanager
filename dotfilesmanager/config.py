"""Configuration storage for dotfilesmanager."""

import os

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
    """Persist a dotfile mapping."""
    config_path = os.path.join(dotfiles_root, "dfm.yaml")
    with open(config_path, "w", newline="\n") as config_file:
        config_file.write(yaml.dump(config, Dumper=yaml.SafeDumper))
