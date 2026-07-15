"""Command-line entry point: run a script or module under the monitor.

Mirrors the invocation style of ``python -m cProfile`` / ``python -m trace``.
The usual runpy caveats apply: the target runs as ``__main__`` but with a
slightly different ``__spec__`` than a direct invocation would have.
"""

import sys

import metapathology

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

_USAGE = """\
usage: python -m metapathology [--report PATH] [--report-format {text,json}] <script.py> [args...]
       python -m metapathology [--report PATH] [--report-format {text,json}] -m <module> [args...]
       metapathology [--report PATH] [--report-format {text,json}] <script.py> [args...]
       metapathology [--report PATH] [--report-format {text,json}] -m <module> [args...]

Runs the target under the metapathology import-machinery monitor. Reports go
to stderr as text by default; file reports default to JSON. Tool options must
precede the target. Use -- to run a script whose name begins with a dash.

Documentation: https://glinte.github.io/metapathology/usage/
"""


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to script or module execution.

    Args:
        argv: Argument list without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    report_destination: str | None = None
    report_format: Literal["text", "json"] | None = None
    while args:
        option = args[0]
        if option == "--":
            args.pop(0)
            break
        if option in ("-h", "--help"):
            sys.stdout.write(_USAGE)
            return 0
        if option == "--report" or option == "--report-format":
            if len(args) < 2:
                sys.stderr.write(_USAGE)
                return 2
            value = args[1]
            del args[:2]
            if option == "--report":
                report_destination = value
            else:
                report_format = _parse_report_format(value)
                if report_format is None:
                    sys.stderr.write(_USAGE)
                    return 2
            continue
        break
    if not args:
        sys.stderr.write(_USAGE)
        return 2
    if args[0] == "-m":
        if len(args) < 2:
            sys.stderr.write(_USAGE)
            return 2
        return _run(
            args[1],
            args[2:],
            is_module=True,
            report_destination=report_destination,
            report_format=report_format,
        )
    return _run(
        args[0],
        args[1:],
        is_module=False,
        report_destination=report_destination,
        report_format=report_format,
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


def _parse_report_format(value: str) -> "Literal['text', 'json'] | None":
    """Narrow a CLI string to a supported report format."""
    if value == "text":
        return "text"
    if value == "json":
        return "json"
    return None


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
