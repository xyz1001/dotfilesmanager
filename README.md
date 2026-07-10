# dotfilesmanager

`dotfilesmanager`（命令名：`dfm`）是一个 Python 命令行工具，用于管理家目录中的配置文件和配置目录。它会将已纳管的内容移动到 `~/dotfiles`，并在原安装位置创建符号链接；安装或共享时则从该目录创建符号链接，以便集中保存和复用配置。

## 安装

从 PyPI 安装：

```bash
pip install dotfilesmanager
```

从源码目录安装：

```bash
pip install .
```

安装后可使用 `dfm` 命令。

## 命令

```text
dfm add <install_path> [--system]
dfm rm <path>
dfm install [<save_path>]
dfm share <save_path> <install_path>
```

- `add <install_path> [--system]`：将家目录中的文件或目录移入 `~/dotfiles`，并在原路径创建符号链接。`--system` 表示该配置与当前操作系统相关，会按当前系统名保存。
- `rm <path>`：移除已纳管项。可传入其安装路径（符号链接）或 `~/dotfiles` 中的保存路径；工具会移除链接并恢复文件。若该保存项仍被其他系统使用，会复制内容到当前安装路径。
- `install [<save_path>]`：不带参数时，为当前系统配置的全部已纳管项创建符号链接；指定 `save_path` 时仅安装该保存项。
- `share <save_path> <install_path>`：将 `~/dotfiles` 中已有的保存项关联到当前系统的另一个安装路径，并创建符号链接。

`save_path` 是 `~/dotfiles` 中的保存路径，`install_path` 是配置的实际安装路径。路径可以使用 `~`。

## 存储与配置

- 已纳管文件保存在 `~/dotfiles` 下；保存目录由安装路径生成，避免不同路径的同名文件冲突。
- `~/dotfiles/dfm.yaml` 由 `dfm` 自动读取和写入，记录各保存项在不同操作系统上的安装路径。请勿手工修改，除非了解其数据结构。
- 仓库中的 `dfm_template.yaml` 仅为旧版示例，不是当前程序读取的配置文件。

## 注意事项

- `add` 仅接受家目录（`~`）内的文件或目录，且不能纳管 `~/dotfiles` 内的内容。
- 安装或共享时，如目标路径已存在，程序会询问是否覆盖；仅输入 `y` 才会替换。
- Windows 上运行 `dfm` 需要管理员权限，以便创建符号链接。
