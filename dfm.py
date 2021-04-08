#!/usr/bin/env python

'''
dotfile管理工具(dotfiles manager)，dotfile指保存配置信息的文件或包含配置文件的文件夹

Usage:
    dfm add <dotfile_path>
    dfm rm <path>
    dfm install [<dotfile_path>]
    dfm share <dotfile_path> <install_path>

Commands:
    add 添加一个dotfile，并将其移动至配置项目录并创建相应软链接
    rm 删除一个dotfile，若该dotfile对应的软连接存在，则删除该软连接并恢复原始文件，若dotfile不再被任何平台使用，则将其从dotfiles目录移除
    install 安装dotfiles，若<path>为空则安装所有dotfile，否则安装指定dotfile
    share 共享一个dotfile，共享一个其他平台已添加的dotfile并安装

Arguments:
    path dotfile_path或install_path
    dotfile_path 位于dotfiles中的dotfile路径
    install_path 安装路径

Options:
    -h --help
'''

from docopt import docopt
from pathlib import Path
import yaml
import os
import platform
import shutil
import hashlib


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


def __get_keep_path(path):
    md5 = hashlib.md5()
    md5.update(str(os.path.dirname(path)).encode("utf8"))
    keep_dir = md5.hexdigest()

    filename = os.path.relpath(path, Path.home())
    keep_path = os.path.join(*[dotfiles_root, keep_dir, filename])
    return keep_path


def __normalize_path(abspath):
    return abspath.replace(str(Path.home()), '~')


def __add(path, config):
    abspath = os.path.abspath(path)
    keep_path = __get_keep_path(abspath)
    os.makedirs(os.path.dirname(keep_path), exist_ok=True)
    shutil.move(abspath, keep_path)
    os.symlink(keep_path, abspath)

    df_path = os.path.relpath(keep_path, dotfiles_root)
    raw_path = __normalize_path(abspath)
    if df_path not in config["dotfiles"]:
        config["dotfiles"][df_path] = {}
    if __get_os_name() not in config["dotfiles"][df_path]:
        config["dotfiles"][df_path][__get_os_name()] = {}
    config["dotfiles"][df_path][__get_os_name()]["path"] = raw_path
    print("Add %s to %s" % (abspath, df_path))
    return config


def __rm(path, config):
    if os.path.islink(path):
        path = os.readlink(path)
    df_path = os.path.relpath(path, dotfiles_root)
    if df_path not in config["dotfiles"]:
        return config
    if __get_os_name() not in config["dotfiles"][df_path]:
        return config

    realpath = os.path.expanduser(
        config["dotfiles"][df_path][__get_os_name()]["path"])

    if os.path.islink(realpath):
        os.unlink(realpath)
    del config["dotfiles"][df_path][__get_os_name()]

    if config["dotfiles"][df_path]:
        if os.path.isfile(path):
            shutil.copy(path, realpath)
        else:
            shutil.copytree(path, realpath)
    else:
        shutil.move(path, realpath)
        del config["dotfiles"][df_path]
    print("Remove %s" % df_path)
    return config


def __install(path, config):
    os_name = __get_os_name()

    df_path = ""
    if path is not None:
        df_path = os.path.relpath(path, dotfiles_root)
        if df_path not in config["dotfiles"]:
            print("%s is not kept in dotfiles" % df_path)
            return config
        if os_name not in config["dotfiles"][df_path]:
            return config

    for item in config["dotfiles"]:
        if os_name not in config["dotfiles"][item]:
            continue
        if df_path != "" and item != df_path:
            continue
        item_path = os.path.join(dotfiles_root, item)
        sym_path = os.path.expanduser(
            config["dotfiles"][item][os_name]["path"])
        if os.path.exists(sym_path):
            is_replace = input("文件 %s 已存在，是否替换？(y/N)" % sym_path)
            if (is_replace.lower() != 'y'):
                continue
            else:
                if os.path.isfile(sym_path) or os.path.islink(sym_path):
                    os.remove(sym_path)
                else:
                    shutil.rmtree(sym_path)
        dst_path = os.path.expanduser(config["dotfiles"]
                                      [item][os_name]["path"])
        os.symlink(item_path, dst_path)
        print("Install %s -> %s" % (item, dst_path))
    return config


def __share(src_path, dst_path, config):
    os_name = __get_os_name()
    df_path = os.path.relpath(src_path, dotfiles_root)
    dst_path = os.path.abspath(dst_path)

    if df_path not in config["dotfiles"]:
        print("%s is not kept in dotfiles" % df_path)
        return config

    if os_name not in config["dotfiles"][df_path]:
        config["dotfiles"][df_path][os_name] = {}
        config["dotfiles"][df_path][os_name]["path"] = __normalize_path(
            dst_path)

    if os.path.exists(dst_path):
        is_replace = input("文件 %s 已存在，是否替换？(y/N)" % dst_path)
        if (is_replace.lower() != 'y'):
            return config
        else:
            if os.path.isfile(dst_path) or os.path.islink(dst_path):
                os.remove(dst_path)
            else:
                shutil.rmtree(dst_path)
    os.symlink(src_path, dst_path)
    print("share %s -> %s" % (df_path, dst_path))
    return config


def __dispatch(args):
    def check_add_args():
        path = os.path.abspath(args["<dotfile_path>"])
        if not os.path.isfile(path) and not os.path.isdir(path):
            print("%s is not valid file or directory" % path)
            exit(-1)
        if path.startswith(dotfiles_root):
            print("%s cannot be in dotfiles" % path)
            exit(-1)
        if not path.startswith(str(Path.home())):
            print("%s must be in home" % path)
            exit(-1)
        if os.path.exists(__get_keep_path(path)):
            print("%s has been kept in dotfiles" % path)
            exit(-1)

    def check_rm_args():
        raw_path = os.path.abspath(args["<path>"])
        path = raw_path
        if os.path.islink(raw_path):
            path = os.readlink(raw_path)
        if not path.startswith(dotfiles_root):
            print("%s is not in dotfiles" % raw_path)
            exit(-1)

    if args["add"]:
        check_add_args()

        def add(config):
            return __add(args["<dotfile_path>"], config)
        return add
    elif args["rm"]:
        check_rm_args()

        def rm(config):
            return __rm(args["<path>"], config)
        return rm
    elif args["install"]:
        def install(config):
            return __install(args["<install_path>"], config)
        return install
    elif args["share"]:
        def share(config):
            return __share(args["<dotfile_path>"], args["<install_path>"], config)
        return share
    else:
        return None


def main():
    args = docopt(__doc__)

    config = __load_config()
    config = __dispatch(args)(config)
    __save_config(config)


if __name__ == "__main__":
    main()
