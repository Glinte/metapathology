"""Command-line entry point: run a script or module under the monitor.

Mirrors the invocation style of ``python -m cProfile`` / ``python -m trace``.
The usual runpy caveats apply: the target runs as ``__main__`` but with a
slightly different ``__spec__`` than a direct invocation would have.
"""

import sys

import metapathology

_USAGE = """\
usage: python -m metapathology <script.py> [args...]
       python -m metapathology -m <module> [args...]
       metapathology <script.py> [args...]
       metapathology -m <module> [args...]

Runs the target under the metapathology import-machinery monitor and writes a
diagnostic report to stderr when the target finishes.

Documentation: https://glinte.github.io/metapathology/usage/
"""


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to script or module execution.

    Args:
        argv: Argument list without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        sys.stderr.write(_USAGE)
        return 2
    if args[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0
    if args[0] == "-m":
        if len(args) < 2:
            sys.stderr.write(_USAGE)
            return 2
        return _run(args[1], args[2:], is_module=True)
    return _run(args[0], args[1:], is_module=False)


def _run(target: str, target_args: list[str], *, is_module: bool) -> int:
    """Install the monitor, run the target via runpy, and always write the report.

    Args:
        target: Script path, or module name when ``is_module`` is true.
        target_args: Arguments the target sees as ``sys.argv[1:]``.
        is_module: Select ``python -m``-style execution instead of a script path.

    Returns:
        The exit code a direct invocation of the target would produce.
    """
    # Help and argument errors never execute a target, so keep these relatively
    # expensive modules off those paths. They still load before install(), not
    # from inside any import hook or finder wrapper.
    import os
    import runpy
    import traceback

    metapathology.install(report_at_exit=False)
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
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    finally:
        metapathology.report()
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
