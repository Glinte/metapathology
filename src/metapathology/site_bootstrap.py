"""Manage the opt-in early-site ``.pth`` bootstrap.

Use ``python -m metapathology.site_bootstrap`` so the selected interpreter and
site-packages directory match the environment being diagnosed.
"""

import argparse
import os
import re
import secrets
import sys
import sysconfig
from contextlib import suppress

TYPE_CHECKING = False

if TYPE_CHECKING:
    from os import PathLike
    from typing import Literal

_BOOTSTRAP_FILENAME = "00_metapathology_early.pth"
_ACTIVATION_ENV = "METAPATHOLOGY_EARLY_BOOTSTRAP"
_FORMAT_VERSION = 1
_TOKEN_BYTES = 16
_MAX_FILE_BYTES = 4096
_HEADER_PREFIX = f"# metapathology early-site bootstrap v{_FORMAT_VERSION} token="
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{32}")
_DEPRECATED_PTH_VERSION = (3, 15)


class BootstrapStatus:
    """Inspection result for one selected site-packages directory."""

    __slots__ = ("path", "state", "token")

    def __init__(self, path: str, state: "Literal['absent', 'damaged', 'foreign', 'installed']", token: str | None):
        self.path = path
        self.state = state
        self.token = token


def install(site_packages: "str | PathLike[str] | None" = None) -> BootstrapStatus:
    """Install or repair the generated bootstrap without overwriting foreign files.

    Args:
        site_packages: Existing site-packages directory. Defaults to this
            interpreter's ``purelib`` path.

    Returns:
        The installed bootstrap status.

    Raises:
        FileExistsError: The target path contains a file not owned by this tool.
        OSError: The directory or bootstrap cannot be inspected or written.
        RuntimeError: This interpreter does not support the bootstrap.
    """
    _require_supported_interpreter()
    path = _bootstrap_path(site_packages)
    current = _inspect(path)
    if current.state == "installed":
        return current
    if current.state == "foreign":
        raise FileExistsError(f"bootstrap path is not owned by metapathology: {path!r}")
    if current.state == "damaged":
        os.unlink(path)

    token = secrets.token_hex(_TOKEN_BYTES)
    rendered = _render(path, token)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(OSError):
            os.unlink(path)
        raise
    return BootstrapStatus(path, "installed", token)


def remove(site_packages: "str | PathLike[str] | None" = None) -> BootstrapStatus:
    """Remove an owned bootstrap, treating an already-absent file as success.

    Args:
        site_packages: Existing site-packages directory. Defaults to this
            interpreter's ``purelib`` path.

    Returns:
        An absent status after successful cleanup.

    Raises:
        FileExistsError: The target path contains a file not owned by this tool.
        OSError: The bootstrap cannot be inspected or removed.
    """
    path = _bootstrap_path(site_packages)
    current = _inspect(path)
    if current.state == "absent":
        return current
    if current.state == "foreign":
        raise FileExistsError(f"bootstrap path is not owned by metapathology: {path!r}")
    os.unlink(path)
    return BootstrapStatus(path, "absent", None)


def status(site_packages: "str | PathLike[str] | None" = None) -> BootstrapStatus:
    """Inspect the selected bootstrap path without changing it."""
    return _inspect(_bootstrap_path(site_packages))


def _require_supported_interpreter() -> None:
    """Reject runtimes outside the executable-``.pth`` support window."""
    if sys.implementation.name != "cpython":
        raise RuntimeError("the early-site bootstrap requires CPython")
    if sys.version_info >= _DEPRECATED_PTH_VERSION:
        raise RuntimeError(
            "executable .pth bootstraps are unsupported on Python 3.15 and newer; the startup mechanism is deprecated"
        )


def _bootstrap_path(site_packages: "str | PathLike[str] | None") -> str:
    """Resolve and validate the selected site-packages directory."""
    raw_directory = sysconfig.get_path("purelib") if site_packages is None else os.fspath(site_packages)
    if not isinstance(raw_directory, str):
        raise TypeError("site-packages paths must resolve to str, not bytes")
    directory = os.path.abspath(raw_directory)
    if not os.path.isdir(directory):
        raise NotADirectoryError(f"site-packages directory does not exist: {directory!r}")
    return os.path.join(directory, _BOOTSTRAP_FILENAME)


def _render(path: str, token: str) -> str:
    """Render the exact owned file format."""
    activation = (
        f'import os; os.environ.get("{_ACTIVATION_ENV}") == "1" and '
        '__import__("metapathology._early_bootstrap", None, None, ("activate",))'
        f".activate({path!r})"
    )
    return f"{_HEADER_PREFIX}{token}\n{activation}\n"


def _inspect(path: str) -> BootstrapStatus:
    """Classify a bootstrap with bounded reads and no foreign-file deletion."""
    if not os.path.lexists(path):
        return BootstrapStatus(path, "absent", None)
    if os.path.islink(path) or not os.path.isfile(path):
        return BootstrapStatus(path, "foreign", None)
    try:
        with open(path, encoding="utf-8") as stream:
            contents = stream.read(_MAX_FILE_BYTES + 1)
    except (OSError, UnicodeError):
        return BootstrapStatus(path, "foreign", None)
    if len(contents.encode("utf-8")) > _MAX_FILE_BYTES:
        return BootstrapStatus(path, "foreign", None)
    first_line, separator, _rest = contents.partition("\n")
    if not separator or not first_line.startswith(_HEADER_PREFIX):
        return BootstrapStatus(path, "foreign", None)
    token = first_line.removeprefix(_HEADER_PREFIX)
    if _TOKEN_PATTERN.fullmatch(token) is None:
        return BootstrapStatus(path, "foreign", None)
    state = "installed" if contents == _render(path, token) else "damaged"
    return BootstrapStatus(path, state, token)


class _Arguments(argparse.Namespace):
    """Typed command destinations."""

    def __init__(self) -> None:
        super().__init__()
        self.command = ""
        self.site_packages: str | None = None


def _parser() -> argparse.ArgumentParser:
    """Build the standalone management CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m metapathology.site_bootstrap",
        description="Manage the experimental early-site .pth bootstrap.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "remove", "status"):
        command = commands.add_parser(name)
        command.add_argument(
            "--site-packages",
            metavar="DIR",
            help="site-packages directory; defaults to this interpreter's purelib",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the bootstrap management command and return its process status."""
    arguments = _Arguments()
    _parser().parse_args(argv, namespace=arguments)
    try:
        if arguments.command == "install":
            result = install(arguments.site_packages)
        elif arguments.command == "remove":
            result = remove(arguments.site_packages)
        else:
            result = status(arguments.site_packages)
    except (OSError, RuntimeError, TypeError) as exc:
        sys.stderr.write(f"metapathology early-site bootstrap: {exc}\n")
        return 1
    sys.stdout.write(f"{result.state}: {result.path}\n")
    return 0 if arguments.command != "status" or result.state == "installed" else 1


if __name__ == "__main__":
    sys.exit(main())
