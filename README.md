# dotfilesmanager

又一个 dotfiles 管理工具（yet another dotfiles manager），该工具支持以下特性

- 多平台支持（目前支持 Windows，Linux和MacOS）

## 安装

```
pip3 install dotfilesmanager
```

# 基本使用

```
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
```
