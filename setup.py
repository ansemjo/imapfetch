#!/usr/bin/env python

from os import environ
from subprocess import check_output
from setuptools import setup

# PEP 440 compatability
environ['REVISION_SEPERATOR'] = '.dev' 

# package metadata
name = "imapfetch"
version = check_output(['sh', 'version.sh', 'version']).strip().decode()
author = "Anton Semjonov"
email = "anton@semjonov.de"
github = "https://github.com/ansemjo/{}".format(name)

setup(
    name=name,
    version=version,
    author=author,
    author_email=email,
    url=github,
    packages=[name],
    entry_points={"console_scripts": ["{0} = {0}.{0}:{0}".format(name)]},
)
