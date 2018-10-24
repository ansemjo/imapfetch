#!/usr/bin/env python3

from setuptools import setup, find_packages

setup(
    name="imapfetch",
    version="0.1",
    author="Anton Semjonov",
    author_email="anton@semjonov.de",
    url="https://github.com/ansemjo/imapfetch",
    packages=find_packages(),
    entry_points={"console_scripts": ["imapfetch = imapfetch.imapfetch:imapfetch"]},
)
