# dotfilesmanager

`dotfilesmanager`（命令名：`dfm`）是一个 Python 命令行工具，用于管理家目录中的配置文件和配置目录。它会将已纳管的内容移动到 `~/dotfiles`，并在原安装位置创建符号链接；安装或共享时则从该目录创建符号链接，以便集中保存和复用配置。

## 安装

从 PyPI 安装：

```bash
pip install dotfilesmanager
```

从源码目录安装（含开发与验证工具）：

```bash
python -m pip install -e '.[dev]'
```

安装后可使用 `dfm` 命令。

开发验证：

```bash
ruff check .
ruff format --check .
dfm --help
python -m build
twine check dist/*
```

## 命令

```text
dfm add <install_path> [--system] [--non-interactive] [--target=SYSTEM=PATH]... [--dry-run] [--force]
dfm rm <path> [--all] [--dry-run] [--force]
dfm install [<save_path>] [--dry-run] [--force]
dfm share <save_path> <install_path> [--non-interactive] [--target=SYSTEM=PATH]... [--dry-run] [--force]
dfm view [--dry-run]
dfm doctor
dfm setup
```

- `add <install_path> [--system]`：将家目录中的文件或目录移入 `~/dotfiles`，并在原路径创建符号链接。TTY 中（除 `--system` 外）会先选择尚未声明的平台，再逐项选择安装路径；`--system` 表示该配置与当前操作系统相关，会按当前系统名保存且不提供跨平台目标。
- `rm <path>`：TTY 中会选择要移除的已登记平台，默认选中当前平台；仅移除其他平台时不访问本机或外部安装路径，若仍有登记则只删除所选登记，若清空最后登记则删除保存对象。选中当前平台时会移除链接并恢复文件；若保留其他平台登记则复制保存对象。可传入其安装路径（符号链接）、`~/dotfiles` 中的保存路径，或 `view/` 下直接指向 `files/<32位md5>/...` 保存命名空间的符号链接。`--all` 跳过选择，删除所有平台登记和保存对象：当前平台已登记时先恢复到其本地安装路径，未登记时直接删除保存对象；绝不访问其他机器的安装路径。非 TTY 调用保持默认仅移除当前平台。
- `install [<save_path>]`：不带参数时，为当前系统配置的全部已纳管项创建符号链接；指定 `save_path`（也可为 `view/` 下直接指向 `files/<32位md5>/...` 保存命名空间的符号链接）时仅安装该保存项。
- `share <save_path> <install_path>`：将保存项关联到当前系统路径，并可在向导中补充其他平台声明。`save_path` 也可为 `view/` 下直接指向 `files/<32位md5>/...` 保存命名空间的符号链接。当前平台已经登记为不同路径时会失败，必须先 `rm`；相同登记且链接正确时不作改动。
- `view`：为所有已配置平台的项目生成 `~/dotfiles/view/<system>/home/` 下的可读相对符号链接视图。安装链接仍直接指向保存对象；视图是可再生的，每次执行都会重建已有视图目录。
- `setup`：仅 Windows 可用；实际探测普通用户的链接能力，必要时通过 UAC 启用 Developer Mode。

物理 `save_path` 位于 `~/dotfiles/files/<md5>/...`；`dfm.yaml` 中的 `dotfiles` 键相对 `files/` 保存，因此为 `<md5>/...` 且不含 `files/` 前缀。`install_path` 是配置的实际安装路径。路径可以使用 `~`。
`view/` 下仅接受直接存在的符号链接：其立即目标必须是根目录内的 `files/<32位md5>/...` 现有文件或目录。目录链接的子路径、悬挂链接及指向根目录外、配置/元数据或非该布局路径的链接仍会被拒绝。

### 跨平台目标

支持的平台固定为 `linux`、`darwin`、`windows`、`android`。脚本或 Agent 必须使用
`--non-interactive`，可重复传入 `--target=SYSTEM=~/path`；非 TTY 的 `add`/`share`
也必须显式使用该选项。目标不能是当前平台，必须是以 `/` 分隔且位于 home 下的
`~/...` 路径，不能包含 `..`、home 本身或 `~/dotfiles`。已存在的同一路径声明是
幂等的，不同路径永不被 `--force` 覆盖。TTY 向导先以复选框显示可用平台（Linux、
macOS、Windows、Android (Termux)），再按固定平台顺序提供可直接使用的路径。对于
CONFIG/DATA 来源会按当前类别生成候选：macOS 首先提供 Unix 路径（CONFIG 为
`~/.config/...`，DATA 为 `~/.local/share/...`），再提供 Application Support；Windows
提供 Roaming（CONFIG）/Local（DATA）AppData 路径。只有未分类来源只显示原路径；已分类的
重定向来源仍获得固定模板建议，目标重定向请使用定制路径；列表只含
路径候选和 Custom path，默认选择第一个建议路径。Custom path 留空或只输入空白会返回该系统的
路径选择。
最终确认默认否。`--dry-run` 保留路径选择但跳过最终确认；非交互 dry-run 不读取
stdin。

建议按**当前来源类别**生成：当前机器的 XDG/AppData 配置和数据根目录用于识别来源；
日志路径始终不转换；缓存/状态仅在其根目录能与 CONFIG/DATA 区分时不转换。macOS
Application Support 和 Windows Local 的缓存/状态别名无法按路径区分，仍按 DATA 转换。
目标机器始终使用固定的 home 相对模板。macOS 的 Unix CONFIG/DATA 候选排在首位，因而也是
默认值；Windows 配置首选 Roaming AppData，DATA 使用 Local。
来源在重定向根目录下时，最后的直接路径只是回退建议，目标重定向必须使用 Custom path。

Android（包括 Termux）使用唯一的 YAML 键 `android`。检测到 Android 时不会读取或
回退到 `linux` 映射；不会创建 `termux` 平台键。**先备份并提交 dotfiles 仓库**：保留
真实 Linux 机器共享使用的 `linux` 映射，同时在 Android 上添加 `android` 映射；仅将
旧 Termux 专用的 `linux` 平台映射和平台专用 `linux` 保存对象手动重新登记或迁移。
向导中 Android 始终显示为 **Android (Termux)**，并使用 Unix CONFIG/DATA 模板；
未分类路径只保留直接候选。

`share` 会先核对当前平台登记和本地链接：缺少登记但已有正确链接时只补登记；
登记和正确链接都存在时为 no-op；缺链接时重建。普通文件、目录或错误链接只能在
TTY 确认替换，非交互模式必须给 `--force`。确认之后如果目标状态发生变化，操作会失败；直接操作不会回滚。

### 变更选项与恢复

- `--dry-run`：读取并校验配置、路径、保存对象和直接变更安全性后输出命令级预览，**不写入任何文件**；不会询问覆盖，也不会创建链接。
- `--force`：跳过安装/共享的覆盖确认；对 `rm`，允许直接替换冲突的安装目标。不会跳过路径或保存对象校验。
- `doctor`：只读诊断配置结构/路径、缺失的保存对象、未引用保存对象和当前系统安装链接状态；不会创建根目录、修复链接或处理历史事务文件。

## 存储与配置

- 已纳管文件保存在 `~/dotfiles` 下；保存目录由安装路径生成，避免不同路径的同名文件冲突。
- `~/dotfiles/dfm.yaml` 由 `dfm` 自动读取和写入，记录各保存项在不同操作系统上的安装路径。写入先落到同目录临时文件、`fsync` 后以原子替换提交；请勿手工修改，除非了解其数据结构。
- 变更命令直接修改文件系统并使用原子配置写入；没有锁、日志、快照、回滚或自动恢复。遗留 `.dfm-transaction.yaml`、`.dfm-backups` 和 `.dfm.lock` 会被忽略，绝不自动删除。

## 注意事项

- `add` 仅接受家目录（`~`）内的文件或目录，且不能纳管 `~/dotfiles` 内的内容。
- `share` 的安装目标必须位于当前用户 home 且不在 `~/dotfiles` 内；`share` 和 `install` 在询问覆盖或写入前都要求保存对象已存在且为文件或目录。`install` 不带参数时会对当前系统全部待安装对象做此检查。
- `rm` 默认只会恢复到不存在的安装路径或指向对应保存对象的受管链接；若该位置是普通文件、目录或指向其他位置的链接，会拒绝操作。`--force` 会直接替换冲突目标。
- 配置始终检查保存路径及所有平台记录的结构；只有**当前平台**的安装路径会按本机 home/根目录规则进行文件系统路径校验，避免把其他系统的合法路径当作本机错误。
- 安装或共享时，如目标路径已存在，程序会询问是否覆盖；仅输入 `y` 才会替换。
- 操作异常或断电后状态可能部分完成；请先检查保存对象、安装路径和 `dfm.yaml`，再决定是否重试。`doctor` 仅报告问题，不会修复或处理遗留事务文件。
- Windows 普通命令会以当前用户身份创建符号链接。若 Windows 返回 `ERROR_PRIVILEGE_NOT_HELD`（1314），先运行 `dfm setup`，它会先实际测试文件和目录链接；只有测试确认缺少该权限时才通过 UAC 启用 Developer Mode（机器级 `AllowDevelopmentWithoutDevLicense=1`），随后重试原命令。该命令不启用 `AllowAllTrustedApps`，也不会提升整个 Python/dfm 进程。
- 如果 `~/dotfiles` 是 Git 仓库，请自行在该仓库的忽略规则中加入 `/view/`。`dfm view` 不会修改 `.gitignore`、`.git/info/exclude` 或任何 Git 忽略元数据。

## 发布到 PyPI

在 PyPI 项目的 **Publishing** 设置中配置 GitHub Trusted Publisher，仓库填写
`xyz1001/dotfilesmanager`，workflow 填写 `publish.yml`，environment 填写 `pypi`。发布
tag 必须与 `pyproject.toml` 中的版本一致，并以 `v` 开头，例如版本为 `1.1.5` 时：

```bash
git tag v1.1.5
git push origin v1.1.5
```

推送该 tag 后，GitHub Actions 会构建、检查并通过 Trusted Publishing 发布到 PyPI。
