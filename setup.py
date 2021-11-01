#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys
from pathlib import Path

from setuptools import find_packages, setup

current_dir = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent


def read_reqs(reqs_path: Path):
    reqs = re.findall(r"(^[^#-][\w]+[-~>=<.\w]+)", reqs_path.read_text(), re.MULTILINE)
    # TODO: temporary excluding requirements using git
    # https://pip.pypa.io/en/stable/reference/pip_install/#vcs-support
    return [r for r in reqs if not r.startswith("git")]


readme = (current_dir / "README.md").read_text()
version = "0.0.1"

install_requirements = read_reqs(current_dir / "requirements" / "_base.txt")

test_requirements = read_reqs(current_dir / "requirements" / "_test.txt")


setup(
    name="osparc-dask-gateway",
    version=version,
    author="Manuel Guidon (mguidon)",
    description="osparc backend for dask-gateway-server",
    classifiers=[
        "Development Status :: 1 - Planning",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.8",
    ],
    long_description=readme,
    license="MIT license",
    python_requires="~=3.8",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=install_requirements,
    test_suite="tests",
    tests_require=test_requirements,
    extras_require={"test": test_requirements},
    entry_points={
        "console_scripts": [
            "osparc-dask-gateway=osparc_dask_gateway.app:start"
        ]
    },
)
