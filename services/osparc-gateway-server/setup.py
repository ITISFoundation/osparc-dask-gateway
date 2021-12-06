#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys
from pathlib import Path
from typing import Set


def read_reqs(reqs_path: Path) -> Set[str]:
    return {
        r
        for r in re.findall(
            r"(^[^#\n-][\w\[,\]]+[-~>=<.\w]*)",
            reqs_path.read_text(),
            re.MULTILINE,
        )
        if isinstance(r, str)
    }


CURRENT_DIR = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent

NAME = "osparc-gateway-server"
VERSION = (CURRENT_DIR / "VERSION").read_text().strip()
AUTHORS = "Manuel Guidon (mguidon), Sylvain Anderegg (sanderegg)"
DESCRIPTION = "Osparc backend for dask-gateway-server"
README = (CURRENT_DIR / "README.md").read_text()

PROD_REQUIREMENTS = tuple(read_reqs(CURRENT_DIR / "requirements" / "_base.txt"))

TEST_REQUIREMENTS = tuple(read_reqs(CURRENT_DIR / "requirements" / "_test.txt"))


if __name__ == "__main__":
    from setuptools import find_packages, setup

    setup(
        name=NAME,
        version=VERSION,
        author=AUTHORS,
        description=DESCRIPTION,
        long_description=README,
        license="MIT license",
        python_requires="~=3.8",
        packages=find_packages(where="src"),
        package_dir={
            "": "src",
        },
        install_requires=PROD_REQUIREMENTS,
        test_suite="tests",
        tests_require=TEST_REQUIREMENTS,
        extras_require={"test": TEST_REQUIREMENTS},
        entry_points={
            "console_scripts": ["osparc-gateway-server=osparc_dask_gateway.app:start"]
        },
    )
