#!/usr/bin/env python3

from os.path import isdir
from subprocess import check_output as cmd
from setuptools import setup

# package name
name = "imapfetch"

# package version and commit hash
version = "0.2.0"
commit = "$Format:%h$"

# try to get the most specific version possible
if "Format:" not in commit:
    version = version + "-" + commit

if isdir(".git"):
    version = cmd(["git", "describe", "--always", "--long", "--dirty"]).strip().decode()

# author information
author = "Anton Semjonov"
email = "anton@semjonov.de"
github = f"https://github.com/ansemjo/{name}"

setup(
    name=name,
    version=version,
    author=author,
    author_email=email,
    url=github,
    packages=[name],
    entry_points={"console_scripts": [f"{name} = {name}.{name}:{name}"]},
)
