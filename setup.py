#!/usr/bin/env python3

import setuptools

setuptools.setup(
    
    name = "imapfetch",
    version = "0.1",
    url = "https://github.com/ansemjo/imapfetch",
    
    author = "Anton Semjonov",
    author_email = "anton@semjonov.de",
    
    packages=setuptools.find_packages(),
    entry_points={
        "console_scripts": [
          "imapfetch = imapfetch.imapfetch:imapfetch",
        ]
    },

)
