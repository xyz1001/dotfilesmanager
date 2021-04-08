#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

setup(
    name='dotfilesmanager',
    version="1.0.1",
    description="dotfiles管理工具",
    long_description="""dotfile管理工具，支持多平台""",
    keywords='python dotfiles',
    author='xyz1001',
    author_email='zgzf1001@gmail.com',
    url='https://github.com/xyz1001/dotfilesmanager',
    license='MIT',
    py_modules=['dfm'],
    include_package_data=True,
    zip_safe=False,
    install_requires=['docopt', 'pyyaml'],
    classifiers=[
        'Programming Language :: Python :: 3',
    ],
    entry_points={'console_scripts': [
        'dfm = dfm:main',
    ]},
)
