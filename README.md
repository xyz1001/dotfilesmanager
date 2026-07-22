# 📂 dotfilesmanager (dfm)

**Language:** [Chinese/中文](README_zh.md)

<p align="center">
  <a href="https://pypi.org/project/dotfilesmanager/">
    <img src="https://img.shields.io/pypi/v/dotfilesmanager?color=blue&logo=pypi&logoColor=white" alt="PyPI version">
  </a>
  <a href="https://pypi.org/project/dotfilesmanager/">
    <img src="https://img.shields.io/pypi/pyversions/dotfilesmanager?color=brightgreen&logo=python&logoColor=white" alt="Python Versions">
  </a>
  <a href="https://github.com/xyz1001/dotfilesmanager/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/xyz1001/dotfilesmanager?color=orange" alt="License">
  </a>
</p>

`dotfilesmanager` (or `dfm` for short) is a **minimal, lightweight, and cross-platform** configuration file (dotfiles) manager.

Unlike traditional synchronization or copying tools, `dfm` uses a **“move the original file + automatically create a symlink”** workflow. It centrally archives your configuration files in `~/dotfiles` under your home directory and creates symbolic links at their original locations. This lets you synchronize and back up configurations across machines while preserving their native real-time update behavior.

---

## ✨ Core Features

- 🚀 **Immediate effect**: Uses symlinks, so configuration changes take effect immediately without manual copying or synchronization.
- 💻 **Native cross-platform support**: Consistently supports Linux, macOS, Windows, and Android (Termux).
- 🧠 **Smart path recommendations**: When sharing configurations across platforms, automatically recommends the most suitable path according to the target system (for example, `~/.config` on macOS and an AppData path on Windows).
- 🔍 **Clear view**: Automatically generates a read-only directory of links organized by platform under `~/dotfiles/view/` for easy overview.
- 🩺 **Health diagnostics**: Includes a one-command check to quickly locate and fix broken symlinks, configuration conflicts, and other issues.

---

## 💾 Installation

Install with `pip` in one step:

```bash
pip install dotfilesmanager
```

After installation, you can use the **`dfm`** command directly from the command line.

---

## ⌨️ Shell Autocompletion

Click's completion feature only generates completion scripts; it does not install or enable them automatically. Save the script to the appropriate location for your Shell, or output it and load it manually:

```bash
# Bash: common bash-completion directory (or source into the current Shell)
_DFM_COMPLETE=bash_source dfm > ~/.local/share/bash-completion/completions/dfm

# Zsh: completion function directory
_DFM_COMPLETE=zsh_source dfm > ~/.zfunc/_dfm

# Fish: completion script directory
_DFM_COMPLETE=fish_source dfm > ~/.config/fish/completions/dfm.fish
```

Before first use, create the directories above yourself and configure your Shell to load the scripts: for Bash, run `source` or reload bash-completion; for Zsh, add `~/.zfunc` to `fpath` and run `compinit`; Fish loads from its completions directory. Autocompletion is not enabled automatically by these steps.

---

## 🏁 Quick Start

### 🛠️ Scenario 1: Add a local configuration to management

Enter a file or directory path to add it to `~/dotfiles`:

```bash
dfm add ~/.bashrc
```

> 💡 **Interactive wizard**
>
> In an interactive terminal (TTY), `dfm` automatically detects and asks whether you also want to share this configuration on other platforms (such as Windows / macOS / Android), and intelligently recommends a default path.
> 
> If this configuration belongs only to the current system and does not need to be shared across platforms, use the `--system` option:
> ```bash
> dfm add ~/.bashrc --system
> ```

### 🔐 Encrypt a new configuration with git-crypt

Install, prepare, and unlock git-crypt yourself before using `--encrypt`:

```bash
dfm add ~/.secret-config --encrypt
```

### 🔄 Scenario 2: Restore configurations on a new machine or system

After cloning your `~/dotfiles` repository to a new machine, rebuild all symbolic links with one command:

```bash
dfm install
```

To install only a specific configuration:

```bash
dfm install <保存的配置名/路径>
```

### 🤝 Scenario 3: Share an existing configuration across systems or at a new path

To use a configuration already managed by `dfm` on the current system at a different path:

```bash
dfm share <已保存配置项的路径> <当前系统下的新安装目标路径>
```

### 🗑️ Scenario 4: Stop managing a configuration and restore the file

When you no longer want `dfm` to manage a configuration and want to restore it to its original state:

```bash
dfm rm <路径>
```
This safely removes the symbolic link and **restores the original file or directory without data loss** from `~/dotfiles` to its initial installation path.

> [!TIP]
> To completely remove this configuration's associations on all systems and delete its source file from `~/dotfiles`, use:
> ```bash
> dfm rm <路径> --all
> ```

---

## 📑 Common Commands

| Command | Description |
| :--- | :--- |
| **`dfm add <path>`** | Manage a configuration file or directory by moving it into `~/dotfiles` and creating a link at its original location. |
| **`dfm rm <path>`** | Stop managing a configuration, remove the symbolic link, and put the file back in its original location. |
| **`dfm install [<path>]`** | Rebuild symbolic links for all (or a specified) configuration files for the current system. |
| **`dfm share <saved> <new>`** | Share an existing configuration with the current system and install it at the specified new path. |
| **`dfm view`** | Generate a clearly categorized read-only link view under `~/dotfiles/view` for easy management and inspection. |
| **`dfm doctor`** | Scan and diagnose the current system's configurations for broken links, conflicts, or unregistered files. |
| **`dfm setup`** | **(Windows only)** Check and enable Developer Mode so ordinary user permissions can create symbolic links. |

---

## 🔧 Platform Notes

### 🪟 Windows Users
* Creating symbolic links on Windows usually requires administrator privileges or Developer Mode.
* If you encounter a permissions error while running a command, execute **`dfm setup`**. It will guide you through enabling Developer Mode via UAC, after which you can use `dfm` normally with standard user permissions.

### 🤖 Android (Termux) Users
* `dfm` fully supports the Termux environment on Android (the system identifier is `android`).
* You can rebuild or share Unix-style configuration files on mobile devices.

---

## 📂 Storage and Configuration Management

* **Physical storage**: The originals of all managed files are stored in `~/dotfiles/files/`.
* **Data manifest**: `dfm.yaml` is the only automatically generated configuration file and persists path mappings for each configuration across platforms.
* **Version control recommendation**: We strongly recommend initializing the entire `~/dotfiles` directory as a Git repository and pushing it to GitHub or another platform for backup.
  > [!TIP]
  > We recommend adding `/view/` to your `.gitignore` to avoid committing generated temporary view files to the Git repository.

### 🔐 Partial value encryption

This feature requires `cryptography` and an installed GPG recipient key. In the
repository, create `rules.json` with filename globs mapped to key lists, then run
`dfm encrypt init RECIPIENT`. This creates the wrapped key, local
`.git/line-crypt.key` cache, and Git filter attributes. Normal `git add`, commit,
and checkout store deterministic `ENCv1:` values while the worktree stays
plaintext. Delete `.git/line-crypt.key` to remove local key access.
