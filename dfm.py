#! /usr/bin/env python


'''
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
'''

from docopt import docopt
from pathlib import Path
import yaml
import os
import platform
import shutil
import hashlib
import posixpath
import ctypes


dotfiles_root = os.path.join(*[Path.home(), "dotfiles"])


def __load_config():
    config_path = os.path.join(*[dotfiles_root, "dfm.yaml"])
    if not os.path.isfile(config_path):
        return {"dotfiles": {}}
    with open(config_path) as fin:
        config = yaml.load(fin, Loader=yaml.SafeLoader)
        if "dotfiles" not in config:
            config["dotfiles"] = {}
        return config


def __save_config(config):
    config_path = os.path.join(*[dotfiles_root, "dfm.yaml"])
    with open(config_path, 'w') as fout:
        fout.write(yaml.dump(config, Dumper=yaml.SafeDumper))


def __get_os_name():
    return platform.system().lower()


def __normalize_path(path):
    if path is None:
        return None
    return os.path.abspath(os.path.normpath(__expanduser(path)))


def __expanduser(path):
    if path is None:
        return None

    if __get_os_name() != "windows":
        return os.path.expanduser(path)

    home = str(Path.home())
    if path.startswith('~'):
        path = path.replace('~', home, 1)
    return path


# opposite of os.path.expanduser
def __shrinkuser(path):
    if path is None:
        return None

    home = str(Path.home())
    if path.startswith(home):
        path = path.replace(home, '~', 1)
    return path


def __get_save_path(install_path, system):
    install_path = __shrinkuser(install_path)
    md5 = hashlib.md5()
    md5.update(str(os.path.dirname(install_path)).encode("utf8"))
    save_dir = md5.hexdigest()

    filename = os.path.basename(install_path)
    system_sep = __get_os_name() if system else ""
    save_path = os.path.join(*[dotfiles_root, save_dir, system_sep, filename])
    return save_path


def __mklink(target, link):
    if not os.path.isdir(os.path.dirname(link)):
        os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link):
        is_replace = input("文件 %s 已存在，是否替换？(y/N)" % link)
        if is_replace.lower() != 'y':
            return False

        if os.path.islink(link) or os.path.isfile(link):
            os.remove(link)
        else:
            shutil.rmtree(link)
    os.symlink(target, link)
    return True


def __set_path(config, rel_save_path, install_path):
    os_name = __get_os_name()
    if rel_save_path not in config["dotfiles"]:
        config["dotfiles"][rel_save_path] = {}
    if os_name not in config["dotfiles"][rel_save_path]:
        config["dotfiles"][rel_save_path][os_name] = {}
    config["dotfiles"][rel_save_path][os_name]["path"] = __shrinkuser(
        install_path)
    return config


def __get_path(config, rel_save_path):
    os_name = __get_os_name()
    if rel_save_path not in config["dotfiles"]:
        return None
    if os_name not in config["dotfiles"][rel_save_path]:
        return None
    install_path = config["dotfiles"][rel_save_path][os_name]["path"]
    return __expanduser(install_path)


def __add(install_path, system, config):
    abs_save_path = __get_save_path(install_path, system)
    os.makedirs(os.path.dirname(abs_save_path), exist_ok=True)
    shutil.move(install_path, abs_save_path)
    os.symlink(abs_save_path, install_path)

    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root)
    rel_save_path = rel_save_path.replace(os.sep, posixpath.sep)
    print("Add %s to %s" % (install_path, rel_save_path))
    return __set_path(config, rel_save_path, install_path)


def __rm(path, config):
    abs_save_path = path
    if os.path.islink(path):
        abs_save_path = os.readlink(path)
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root)
    rel_save_path = rel_save_path.replace(os.sep, posixpath.sep)

    install_path = __get_path(config, rel_save_path)
    if install_path is None:
        return config

    if os.path.islink(install_path):
        os.unlink(install_path)
    del config["dotfiles"][rel_save_path][__get_os_name()]

    if config["dotfiles"][rel_save_path]:
        if os.path.isfile(abs_save_path):
            shutil.copy(abs_save_path, install_path)
        else:
            shutil.copytree(abs_save_path, install_path)
    else:
        shutil.move(abs_save_path, install_path)
        del config["dotfiles"][rel_save_path]
    abs_save_dir = os.path.dirname(abs_save_path)
    if len(os.listdir(abs_save_dir)) == 0:
        os.rmdir(abs_save_dir)

    print("Remove %s" % rel_save_path)
    return config


def __install(abs_save_path, config):
    rel_save_path = None
    if abs_save_path is not None:
        rel_save_path = os.path.relpath(abs_save_path, dotfiles_root)
        rel_save_path = rel_save_path.replace(os.sep, posixpath.sep)
        if __get_path(config, rel_save_path) is None:
            print("%s is not kept in dotfiles" % rel_save_path)
            return config

    for item_rel_save_path in config["dotfiles"]:
        if rel_save_path is not None and item_rel_save_path != rel_save_path:
            continue

        item_install_path = __get_path(config, item_rel_save_path)
        if item_install_path is None:
            continue
        item_abs_save_path = os.path.join(dotfiles_root, item_rel_save_path)
        item_abs_save_path = item_abs_save_path.replace(posixpath.sep, os.sep)

        if __mklink(item_abs_save_path, item_install_path):
            print("Install %s -> %s" % (item_rel_save_path, item_install_path))
    return config


def __share(abs_save_path, install_path, config):
    rel_save_path = os.path.relpath(abs_save_path, dotfiles_root)
    rel_save_path = rel_save_path.replace(os.sep, posixpath.sep)
    if rel_save_path not in config["dotfiles"]:
        print("%s is not kept in dotfiles" % rel_save_path)
        return config

    config = __set_path(config, rel_save_path, install_path)

    if __mklink(abs_save_path, install_path):
        print("share %s -> %s" % (rel_save_path, install_path))
    return config


def __dispatch(args):
    def check_add_args():
        path = __normalize_path((args["<install_path>"]))
        system = args["--system"]
        if not os.path.isfile(path) and not os.path.isdir(path):
            print("%s is not valid file or directory" % path)
            exit(-1)
        if path.startswith(dotfiles_root):
            print("%s cannot be in dotfiles" % path)
            exit(-1)
        if not path.startswith(str(Path.home())):
            print("%s must be in home" % path)
            exit(-1)
        if os.path.exists(__get_save_path(path, system)):
            print("%s has been kept in dotfiles" % path)
            exit(-1)

    def check_rm_args():
        raw_path = __normalize_path(args["<path>"])
        path = raw_path
        if os.path.islink(raw_path):
            path = os.readlink(raw_path)
        if not path.startswith(dotfiles_root):
            print("%s is not in dotfiles" % raw_path)
            exit(-1)

    if args["add"]:
        check_add_args()

        def add(config):
            return __add(__normalize_path(args["<install_path>"]),
                         args["--system"], config)
        return add
    elif args["rm"]:
        check_rm_args()

        def rm(config):
            return __rm(__normalize_path(args["<path>"]), config)
        return rm
    elif args["install"]:
        def install(config):
            return __install(__normalize_path(args["<save_path>"]), config)
        return install
    elif args["share"]:
        def share(config):
            return __share(__normalize_path(args["<save_path>"]),
                           __normalize_path(args["<install_path>"]), config)
        return share
    else:
        return None


def main():
    if __get_os_name() == "windows":
        if ctypes.windll.shell32.IsUserAnAdmin() == 0:
            print("dfm must run with Administrator priviledges under Windows")
            return

    args = docopt(__doc__)

    config = __load_config()
    config = __dispatch(args)(config)
    __save_config(config)


if __name__ == "__main__":
    main()
