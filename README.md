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
dfm add <install_path> [--system] [--non-interactive] [--target=SYSTEM=PATH]... [--dry-run] [--force] [--backup]
dfm rm <path> [--dry-run] [--force] [--backup]
dfm install [<save_path>] [--dry-run] [--force] [--backup]
dfm share <save_path> <install_path> [--non-interactive] [--target=SYSTEM=PATH]... [--dry-run] [--force] [--backup]
dfm view [--dry-run] [--force] [--backup]
dfm doctor [--repair]
```

- `add <install_path> [--system]`：将家目录中的文件或目录移入 `~/dotfiles`，并在原路径创建符号链接。TTY 中（除 `--system` 外）会先选择尚未声明的平台，再逐项选择安装路径；`--system` 表示该配置与当前操作系统相关，会按当前系统名保存且不提供跨平台目标。
- `rm <path>`：移除已纳管项。可传入其安装路径（符号链接）或 `~/dotfiles` 中的保存路径；工具会移除链接并恢复文件。若该保存项仍被其他系统使用，会复制内容到当前安装路径。
- `install [<save_path>]`：不带参数时，为当前系统配置的全部已纳管项创建符号链接；指定 `save_path` 时仅安装该保存项。
- `share <save_path> <install_path>`：将保存项关联到当前系统路径，并可在向导中补充其他平台声明。当前平台已经登记为不同路径时会失败，必须先 `rm`；相同登记且链接正确时不作改动。
- `view`：为当前系统已配置的项目生成 `~/dotfiles/view/<system>/home/` 下的可读相对符号链接视图。安装链接仍直接指向保存对象；视图是可再生的，已有视图目录需使用 `--force` 重建。

`save_path` 是 `~/dotfiles` 中的保存路径，`install_path` 是配置的实际安装路径。路径可以使用 `~`。

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
TTY 事务前确认替换，非交互模式必须给 `--force`。确认之后如果目标状态发生变化，
操作会回滚而不是再次询问或盲目替换。

### 变更选项与恢复

- `--dry-run`：读取并校验配置，以及校验本命令可校验的路径/保存对象后输出命令级预览，**不创建锁、日志、备份或任何其他文件**；不会询问覆盖，也不会创建链接。
- `--force`：跳过安装/共享的覆盖确认；对 `rm`，允许在事务快照保护下替换冲突的安装目标。不会跳过路径、保存对象或事务恢复校验。
- `--backup`：成功提交后保留本次操作开始前的快照及其 `manifest.yaml`；不带此选项时，成功提交会删除该快照目录。
- `doctor`：诊断待处理事务、配置结构/路径、缺失的保存对象、当前保存命名空间内未被配置引用的保存对象，以及当前系统安装链接的缺失、悬挂、错误目标或“不是链接”情况。未引用对象检查只扫描 dfm 保存路径使用的 MD5 命名空间，不扫描仓库中的 `.git`、README 等普通文件。无 `--repair` 时发现问题即失败。`--repair` **只**回滚可验证的待处理事务，或在没有其他问题时事务性重建“保存对象存在且目标明确”的缺失或错误符号链接；不会修复 YAML、缺失对象、悬挂链接、普通文件链接、未引用对象或历史备份。无效日志或清单会报告为问题，且不会被自动删除或执行恢复。

## 存储与配置

- 已纳管文件保存在 `~/dotfiles` 下；保存目录由安装路径生成，避免不同路径的同名文件冲突。
- `~/dotfiles/dfm.yaml` 由 `dfm` 自动读取和写入，记录各保存项在不同操作系统上的安装路径。写入先落到同目录临时文件、`fsync` 后以原子替换提交；请勿手工修改，除非了解其数据结构。
- 变更命令在 `~/dotfiles/.dfm.lock` 上持有排他锁，并在 `~/dotfiles/.dfm-transaction.yaml` 写入可恢复日志。创建及恢复事务状态时，会在**读取日志或清单之前**以 `lstat` 验证 root、`.dfm-backups`、事务目录、日志和 `manifest.yaml`：目录必须是实际目录，日志/清单必须是普通文件；符号链接、非目录或非普通文件一律拒绝，避免元数据穿越或状态逃逸。路径和 `dfm.yaml` 快照位于 `~/dotfiles/.dfm-backups/<事务 ID>/`，其中 `manifest.yaml` 与日志内容相互对应。快照和清单完成同步后才写入日志；成功后日志删除，快照默认删除，使用 `--backup` 时保留。
- 仓库中的 `dfm_template.yaml` 仅为旧版示例，不是当前程序读取的配置文件。

## 注意事项

- `add` 仅接受家目录（`~`）内的文件或目录，且不能纳管 `~/dotfiles` 内的内容。
- `share` 的安装目标必须位于当前用户 home 且不在 `~/dotfiles` 内；`share` 和 `install` 在询问覆盖或写入事务前都要求保存对象已存在且为文件或目录。`install` 不带参数时会对当前系统全部待安装对象做此检查。
- `rm` 默认只会恢复到不存在的安装路径或指向对应保存对象的受管链接；若该位置是普通文件、目录或指向其他位置的链接，会拒绝操作。使用 `--force` 时会在事务快照保护下替换冲突目标；加 `--backup` 可在提交后保留该替换前快照。
- 配置始终检查保存路径及所有平台记录的结构；只有**当前平台**的安装路径会按本机 home/根目录规则进行文件系统路径校验，避免把其他系统的合法路径当作本机错误。
- 安装或共享时，如目标路径已存在，程序会询问是否覆盖；仅输入 `y` 才会替换。
- `add`、`rm`、`share` 和 `install` 的已接受安装批次在同一事务中执行；创建事务前与恢复前使用相同的路径规则，拒绝 root/home 范围外、配置/锁/日志/备份等受保护状态，以及已有符号链接父目录下的路径。下一次变更命令会先验证并回滚遗留的**有效**未完成事务。恢复使用复制的不可变快照；只有全部路径和配置恢复完成后才删除日志。`share` 的替换若被拒绝，不会修改配置。
- 操作异常时，程序会尝试立即恢复；若恢复本身失败，日志和快照保持不变，可安全重试。恢复会拒绝日志中指向配置、锁、日志或备份目录的受保护路径，以及经符号链接父目录逃逸的路径。无效、缺失或不匹配的日志/清单会使恢复和后续变更失败（fail closed），不会据此删除用户路径。断电、磁盘/权限错误等情况下先运行 `dfm doctor`；仅在日志有效时使用 `dfm doctor --repair`。不要手动删除事务日志、快照或保留备份中的 `manifest.yaml`。
- 提交和恢复在删除日志前会同步直接变更的文件、目录树及其父目录；文件或目录同步失败会中止提交/恢复并保留日志和快照，供后续恢复。目录打开或目录 `fsync` **仅**在明确报告不受平台支持（`EINVAL`、`ENOTSUP`/`EOPNOTSUPP`），或 Windows 上目录打开报告 `EACCES` 时跳过；其他打开或同步错误会传播并保留恢复状态。目录树中的符号链接（包括悬挂链接）不会被跟随或同步其目标，仍会同步链接所在父目录。这仍不能替代文件系统/硬件的持久性保证。快照仅覆盖事务列出的直接变更路径（`rm` 列出当前平台的配置安装路径）及配置文件；它不是整个家目录或仓库的长期备份。需要长期/跨机器恢复时请自行备份整个 `~/dotfiles`。
- Windows 上运行 `dfm` 需要管理员权限，以便创建符号链接。
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
