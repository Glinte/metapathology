__all__ = [
    "alias",
    "bdist_egg",
    "bdist_rpm",
    "bdist_wininst",
    "build_clib",
    "build_ext",
    "build_py",
    "develop",
    "dist_info",
    "easy_install",
    "egg_info",
    "install",
    "install_egg_info",
    "install_lib",
    "install_scripts",
    "rotate",
    "saveopts",
    "sdist",
    "setopt",
    "test",
    "upload_docs",
]

import sys
from distutils.command.bdist import bdist

from setuptools.command import install_scripts

if "egg" not in bdist.format_commands:
    bdist.format_command["egg"] = ("bdist_egg", "Python .egg file")
    bdist.format_commands.append("egg")

del bdist, sys
