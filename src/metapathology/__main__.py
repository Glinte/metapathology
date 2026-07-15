"""Command-line entry point: run a script or module under the monitor.

Mirrors the invocation style of ``python -m cProfile`` / ``python -m trace``.
The usual runpy caveats apply: the target runs as ``__main__`` but with a
slightly different ``__spec__`` than a direct invocation would have.
"""

import argparse
import sys

import metapathology

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal, NoReturn

_REPORT_FORMATS = ("text", "json")


class _ArgumentParser(argparse.ArgumentParser):
    """Keep discovery information visible when command-line parsing fails."""

    def error(self, message: str) -> "NoReturn":
        """Print full help before terminating with argparse's error status."""
        self.print_help(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


class _Arguments(argparse.Namespace):
    """Typed destination populated by :class:`argparse.ArgumentParser`."""

    def __init__(self) -> None:
        super().__init__()
        self.report_destination: str | None = None
        self.report_format: Literal["text", "json"] | None = None
        self.is_module = False
        self.target = ""
        self.target_args: list[str] = []


def _make_parser() -> argparse.ArgumentParser:
    """Build the command-line grammar without importing target execution code."""
    parser = _ArgumentParser(
        prog="python -m metapathology",
        description="Run a script or module under the metapathology import-machinery monitor.",
        epilog=(
            "Tool options must precede TARGET. Use -- to run a script whose name begins with a dash. "
            "Documentation: https://glinte.github.io/metapathology/usage/"
        ),
    )
    parser.add_argument("--report", dest="report_destination", metavar="PATH", help="write an automatic report file")
    parser.add_argument(
        "--report-format",
        choices=_REPORT_FORMATS,
        help="select text or JSON output; defaults to text on stderr and JSON for files",
    )
    parser.add_argument("-m", dest="is_module", action="store_true", help="run TARGET as a module")
    parser.add_argument("target", metavar="TARGET", help="script path, or module name with -m")
    parser.add_argument("target_args", metavar="ARG", nargs=argparse.REMAINDER, help="arguments passed to TARGET")
    return parser


_PARSER = _make_parser()


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to script or module execution.

    Args:
        argv: Argument list without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parsed = _Arguments()
    try:
        _PARSER.parse_args(sys.argv[1:] if argv is None else argv, namespace=parsed)
    except SystemExit as exc:
        return _exit_code(exc)
    if parsed.is_module:
        return _run(
            parsed.target,
            parsed.target_args,
            is_module=True,
            report_destination=parsed.report_destination,
            report_format=parsed.report_format,
        )
    return _run(
        parsed.target,
        parsed.target_args,
        is_module=False,
        report_destination=parsed.report_destination,
        report_format=parsed.report_format,
    )


def _run(
    target: str,
    target_args: list[str],
    *,
    is_module: bool,
    report_destination: str | None,
    report_format: "Literal['text', 'json'] | None",
) -> int:
    """Install the monitor, run the target via runpy, and always write the report.

    Args:
        target: Script path, or module name when ``is_module`` is true.
        target_args: Arguments the target sees as ``sys.argv[1:]``.
        is_module: Select ``python -m``-style execution instead of a script path.
        report_destination: Explicit automatic report path, or None.
        report_format: Explicit report format, or None for environment/default resolution.

    Returns:
        The exit code a direct invocation of the target would produce.
    """
    # Help and argument errors never execute a target, so keep these relatively
    # expensive modules off those paths. They still load before install(), not
    # from inside any import hook or finder wrapper.
    import os
    import runpy
    import traceback
    from contextlib import suppress

    monitor = metapathology.install(
        report_at_exit=False,
        report_destination=report_destination,
        report_format=report_format,
    )
    exit_code = 0
    try:
        sys.argv = [target, *target_args]
        if is_module:
            # Mimic `python -m target`: cwd on sys.path; run_module fixes argv[0].
            sys.path.insert(0, os.getcwd())
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        else:
            # Mimic `python target.py`: the script's directory on sys.path.
            sys.path.insert(0, os.path.dirname(os.path.abspath(target)))
            runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        exit_code = _exit_code(exc)
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        # Reporting is diagnostic-only and must not replace the target's exit
        # status when stderr or a configured file is unusable.
        with suppress(Exception):
            monitor._write_configured_report()
    return exit_code


def _exit_code(exc: SystemExit) -> int:
    """Translate ``SystemExit`` into an exit code, printing string payloads the way the interpreter does."""
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    sys.stderr.write(f"{exc.code}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
