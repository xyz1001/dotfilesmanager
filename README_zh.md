# 📂 dotfilesmanager (dfm)

**语言：** [English](README.md)

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

`dotfilesmanager`（简称 `dfm`）是一个**极简、轻量且跨平台**的配置文件（dotfiles）管理工具。

不同于传统的同步或复制工具，`dfm` 采用 **“移动原文件 + 自动建链 (Symlink)”** 的工作流。它将你的配置文件集中归档在用户目录下的 `~/dotfiles`，并在原安装位置创建符号链接，让你不仅能优雅地多端同步、备份配置，还能保持文件的原生实时更新状态。

---

## ✨ 核心特性

- 🚀 **即时生效**：采用符号链接 (Symlink)，配置修改立即可用，无需手动拷贝或同步。
- 💻 **跨平台原生支持**：一致性适配 Linux, macOS, Windows 以及 Android (Termux)。
- 🧠 **智能路径推荐**：在不同平台共享配置时，自动根据目标系统规范推荐最合适的配置路径（例如：macOS 上的 `~/.config`，Windows 下的 AppData 路径）。
- 🔍 **清晰视图 (View)**：可在 `~/dotfiles/view/` 下自动生成按平台分类的只读链接目录，方便概览。
- 🩺 **健康诊断 (Doctor)**：内置一键检查命令，快速定位并修复软链接失效、配置冲突等状态。

---

## 💾 安装

使用 `pip` 一键安装：

```bash
pip install dotfilesmanager
```

安装完成后，你即可在命令行中直接使用 **`dfm`** 指令。

---

## ⌨️ Shell 自动补全

Click 的补全功能只负责生成补全脚本，不会自动安装或启用它。请按所用的
Shell 将脚本保存到相应位置，或直接输出后手动加载：

```bash
# Bash：bash-completion 常用目录（也可 source 到当前 Shell）
_DFM_COMPLETE=bash_source dfm > ~/.local/share/bash-completion/completions/dfm

# Zsh：补全函数目录
_DFM_COMPLETE=zsh_source dfm > ~/.zfunc/_dfm

# Fish：补全脚本目录
_DFM_COMPLETE=fish_source dfm > ~/.config/fish/completions/dfm.fish
```

首次使用前请自行创建上述目录，并根据 Shell 配置加载脚本：Bash 可执行
`source` 或重载 bash-completion，Zsh 将 `~/.zfunc` 加入 `fpath` 后运行
`compinit`，Fish 则从其 completions 目录加载。补全不会因此自动启用。

---

## 🏁 快速上手

### 🛠️ 场景一：将一个本地配置加入纳管

只需输入文件或目录路径，即可将其收入 `~/dotfiles`：

```bash
dfm add ~/.bashrc
```

> 💡 **交互向导**
>
> 在交互式终端（TTY）中，`dfm` 会自动检测并询问你是否在其他平台（如 Windows / macOS / Android）上也共享此配置，并智能推荐默认路径。
>
> 如果此配置仅属于当前系统而无需跨平台共享，请使用 `--system` 参数：
> ```bash
> dfm add ~/.bashrc --system
> ```

### 🔐 使用 git-crypt 加密新配置

请先安装并自行准备、解锁 git-crypt，再使用 `--encrypt`：

```bash
dfm add ~/.secret-config --encrypt
```

### 🔄 场景二：在新机器或新系统上恢复配置

将你的 `~/dotfiles` 仓库克隆到新机器后，一键重建所有符号链接：

```bash
dfm install
```

如果你只想安装特定的某项配置：

```bash
dfm install <保存的配置名/路径>
```

### 🤝 场景三：跨系统/新路径共享已有配置

如果你想在当前系统上使用已经在 `dfm` 中管理的某份配置，但路径不同：

```bash
dfm share <已保存配置项的路径> <当前系统下的新安装目标路径>
```

### 🗑️ 场景四：停止纳管并还原文件

当你不想再用 `dfm` 管理某个配置，希望它还原至原来状态时：

```bash
dfm rm <路径>
```
这会安全地移除符号链接，并把保存在 `~/dotfiles` 下的原文件或目录**无损还原**至其初始安装路径。

> [!TIP]
> 如果想要彻底删除此配置在所有系统上的关联，并清除 `~/dotfiles` 内的源文件，可使用：
> ```bash
> dfm rm <路径> --all
> ```

---

## 📑 常用命令一览

| 命令 | 用途说明 |
| :--- | :--- |
| **`dfm add <path>`** | 纳管一个配置文件或目录，移入 `~/dotfiles` 并在原位置建立链接。 |
| **`dfm rm <path>`** | 移除纳管，删除符号链接，并将文件放回原处。 |
| **`dfm install [<path>]`** | 为当前系统一键重建所有（或指定的）配置文件的符号链接。 |
| **`dfm share <saved> <new>`** | 将已有配置共享给当前系统，并安装到指定的新路径。 |
| **`dfm view`** | 在 `~/dotfiles/view` 下生成一个分类清晰的只读链接视图，方便管理和查看。 |
| **`dfm doctor`** | 扫描并诊断当前系统的配置，检查是否有失效链接、冲突或未登记的文件。 |
| **`dfm setup`** | **（Windows 专用）** 检查并开启系统的开发者模式，以便普通用户权限也能创建符号链接。 |

---

## 🔧 平台注意事项

### 🪟 Windows 用户
* 在 Windows 下创建符号链接通常需要管理员权限或启用开发者模式。
* 如果运行命令时遇到权限报错，请直接执行 **`dfm setup`**，它会自动引导你通过 UAC 开启系统的开发者模式，之后就可以在普通用户权限下正常使用 `dfm`。

### 🤖 Android (Termux) 用户
* `dfm` 完全支持 Android 上的 Termux 环境（系统标识键为 `android`）。
* 可在移动端完美重建或共享 Unix 风格的配置文件。

---

## 📂 存储与配置管理

* **物理存储**：所有纳管文件的原件都保存在 `~/dotfiles/files/` 目录下。
* **数据清单**：`dfm.yaml` 是唯一自动生成的配置文件，用于持久化各配置项在多平台下的路径映射。
* **版本控制推荐**：强烈建议将整个 `~/dotfiles` 目录初始化为 Git 仓库并推送到 GitHub 等平台备份。
  > [!TIP]
  > 建议在你的 `.gitignore` 中加入 `/view/` 规则，避免把生成的临时视图文件提交到 Git 仓库。
