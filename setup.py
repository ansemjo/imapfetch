#!/usr/bin/env python

from subprocess import check_output
from shlex import split
cmd = lambda s: check_output(split(s)).strip().decode()

from setuptools import setup

# package metadata
name = "imapfetch"
version = cmd('sh ./version.sh describe')
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
