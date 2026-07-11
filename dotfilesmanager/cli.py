"""Command-line adapter for dotfilesmanager."""

import ctypes

from docopt import docopt

from . import config, operations

USAGE = """
dotfile管理工具(dotfiles manager)，dotfile指保存配置信息的文件或包含配置文件的文件夹

Usage:
    dfm add <install_path> [--system]
    dfm rm <path>
    dfm install [<save_path>]
    dfm share <save_path> <install_path>

Commands:
    add 添加一个dotfile，并将其移动至配置项目录并创建相应软链接
    rm 删除一个dotfile，若该dotfile对应的软连接存在，则删除该软连接并恢复原始文件，若dotfile不再被任何平台使用，则将其从dotfiles目录移除
    install 安装dotfiles，若<path>为空则安装所有dotfile，否则安装指定dotfile
    share 共享一个dotfile，共享一个其他平台已添加的dotfile并安装

Arguments:
    path save_path或install_path
    save_path dotfile位于dotfiles中的保存路径
    install_path dotfile的安装路径

Options:
    -h --help  显示帮助
    --system  该dotfile和操作系统相关
"""


def _confirm_replace(link):
    return input(f"文件 {link} 已存在，是否替换？(y/N)").lower() == "y"


def _fail(message):
    print(message)
    raise SystemExit(-1)


def _render(result):
    for message in result.messages:
        print(message)


def main():
    if operations.os_name() == "windows":
        if ctypes.windll.shell32.IsUserAnAdmin() == 0:
            print("dfm must run with Administrator priviledges under Windows")
            return

    args = docopt(USAGE)
    dotfiles_root = config.default_dotfiles_root()
    dotfiles_config = config.load_config(dotfiles_root)

    if args["add"]:
        install_path = operations.normalize_path(args["<install_path>"])
        error = operations.validate_add(install_path, args["--system"], dotfiles_root)
        if error:
            _fail(error)
        result = operations.add(
            install_path, args["--system"], dotfiles_config, dotfiles_root
        )
    elif args["rm"]:
        path = operations.normalize_path(args["<path>"])
        error = operations.validate_remove(path, dotfiles_root)
        if error:
            _fail(error)
        result = operations.remove(path, dotfiles_config, dotfiles_root)
    elif args["install"]:
        result = operations.install(
            operations.normalize_path(args["<save_path>"]),
            dotfiles_config,
            dotfiles_root,
            _confirm_replace,
        )
    elif args["share"]:
        result = operations.share(
            operations.normalize_path(args["<save_path>"]),
            operations.normalize_path(args["<install_path>"]),
            dotfiles_config,
            dotfiles_root,
            _confirm_replace,
        )
    else:
        return

    _render(result)
    config.save_config(dotfiles_root, result.config)
