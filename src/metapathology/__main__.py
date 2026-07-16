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
_DOCUMENTATION_URL = "https://glinte.github.io/metapathology/usage/"
_MODULE_PROG = "python -m metapathology"
_CONSOLE_PROG_NAMES = frozenset(("metapathology", "metapathology.exe", "metapathology-script.py"))


class _ArgumentParser(argparse.ArgumentParser):
    """Keep discovery information visible when command-line parsing fails."""

    def _print_error(self, message: str) -> None:
        """Write the common usage, error, and documentation sequence."""
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\nDocumentation: {_DOCUMENTATION_URL}\n")

    def error(self, message: str) -> "NoReturn":
        """Keep failures concise while pointing users to detailed guidance."""
        self._print_error(message)
        self.exit(2)


class _Arguments(argparse.Namespace):
    """Typed destination populated by :class:`argparse.ArgumentParser`."""

    def __init__(self) -> None:
        super().__init__()
        self.report_destination: str | None = None
        self.report_format: Literal["text", "json"] | None = None
        self.monitor_path_hooks: bool | None = None
        self.monitor_importer_cache: bool | None = None
        self.deep: bool | None = None
        self.deep_path_hooks: bool | None = None
        self.deep_path_entry_finders: bool | None = None
        self.deep_loaders: bool | None = None
        self.deep_import_outcomes: bool | None = None
        self.is_module = False
        self.target = ""
        self.target_args: list[str] = []


def _make_parser() -> _ArgumentParser:
    """Build the command-line grammar without importing target execution code."""
    parser = _ArgumentParser(
        prog=_program_name(sys.argv[0]),
        description="Run a script or module under the metapathology import-machinery monitor.",
        epilog=(
            "Tool options must precede TARGET.\n"
            "Use -- to run a script whose name begins with a dash.\n\n"
            "Documentation:\n"
            f"  {_DOCUMENTATION_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--report", dest="report_destination", metavar="PATH", help="write an automatic report file")
    parser.add_argument(
        "--report-format",
        choices=_REPORT_FORMATS,
        help="select text or JSON output; defaults to text on stderr and JSON for files",
    )
    parser.add_argument(
        "--path-hook-monitoring",
        dest="monitor_path_hooks",
        action=argparse.BooleanOptionalAction,
        help="enable or disable sys.path_hooks mutation monitoring",
    )
    parser.add_argument(
        "--importer-cache-monitoring",
        dest="monitor_importer_cache",
        action=argparse.BooleanOptionalAction,
        help="enable or disable sys.path_importer_cache monitoring",
    )
    deep = parser.add_argument_group("opt-in deep diagnostics (may perturb third-party identity checks)")
    deep.add_argument("--deep", action=argparse.BooleanOptionalAction, help="enable or disable all deep mechanisms")
    deep.add_argument(
        "--deep-path-hooks", action=argparse.BooleanOptionalAction, help="capture delegated path-hook calls"
    )
    deep.add_argument(
        "--deep-path-entry-finders",
        action=argparse.BooleanOptionalAction,
        help="capture delegated path-entry finder decisions",
    )
    deep.add_argument(
        "--deep-loaders", action=argparse.BooleanOptionalAction, help="capture delegated loader creation and execution"
    )
    deep.add_argument(
        "--deep-import-outcomes",
        action=argparse.BooleanOptionalAction,
        help="capture exact CPython import invocation outcomes",
    )
    parser.add_argument("-m", dest="is_module", action="store_true", help="run TARGET as a module")
    parser.add_argument("target", metavar="TARGET", help="script path, or module name with -m")
    parser.add_argument("target_args", metavar="ARG", nargs=argparse.REMAINDER, help="arguments passed to TARGET")
    return parser


def _program_name(argv0: str) -> str:
    """Name the supported entry point used to start this process."""
    basename = argv0.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    return "metapathology" if basename in _CONSOLE_PROG_NAMES else _MODULE_PROG


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
            monitor_path_hooks=parsed.monitor_path_hooks,
            monitor_importer_cache=parsed.monitor_importer_cache,
            deep=parsed.deep,
            deep_path_hooks=parsed.deep_path_hooks,
            deep_path_entry_finders=parsed.deep_path_entry_finders,
            deep_loaders=parsed.deep_loaders,
            deep_import_outcomes=parsed.deep_import_outcomes,
        )
    return _run(
        parsed.target,
        parsed.target_args,
        is_module=False,
        report_destination=parsed.report_destination,
        report_format=parsed.report_format,
        monitor_path_hooks=parsed.monitor_path_hooks,
        monitor_importer_cache=parsed.monitor_importer_cache,
        deep=parsed.deep,
        deep_path_hooks=parsed.deep_path_hooks,
        deep_path_entry_finders=parsed.deep_path_entry_finders,
        deep_loaders=parsed.deep_loaders,
        deep_import_outcomes=parsed.deep_import_outcomes,
    )


def _run(
    target: str,
    target_args: list[str],
    *,
    is_module: bool,
    report_destination: str | None,
    report_format: "Literal['text', 'json'] | None",
    monitor_path_hooks: bool | None,
    monitor_importer_cache: bool | None,
    deep: bool | None,
    deep_path_hooks: bool | None,
    deep_path_entry_finders: bool | None,
    deep_loaders: bool | None,
    deep_import_outcomes: bool | None,
) -> int:
    """Install the monitor, run the target via runpy, and always write the report.

    Args:
        target: Script path, or module name when ``is_module`` is true.
        target_args: Arguments the target sees as ``sys.argv[1:]``.
        is_module: Select ``python -m``-style execution instead of a script path.
        report_destination: Explicit automatic report path, or None.
        report_format: Explicit report format, or None for environment/default resolution.
        monitor_path_hooks: Whether to instrument ``sys.path_hooks``.
        monitor_importer_cache: Whether to observe
            ``sys.path_importer_cache``.
        deep: Whether to enable every deep mechanism.
        deep_path_hooks: Capture path-hook calls through replacement delegates.
        deep_path_entry_finders: Capture path-entry finder calls.
        deep_loaders: Capture modern loader creation and execution.
        deep_import_outcomes: Capture exact CPython import invocation outcomes.

    Returns:
        The exit code a direct invocation of the target would produce.
    """
    # Validate script launch errors before installing: no target code or import
    # machinery ran, so a diagnostic report would describe only our bootstrap.
    import os

    if not is_module and not os.path.exists(target):
        _PARSER._print_error(f"script target does not exist: {target!r}")
        return 2

    # Keep target-execution dependencies off help and argument-error paths.
    # They still load before install(), never inside a hook or finder wrapper.
    import runpy
    import traceback
    from contextlib import suppress

    monitor = metapathology.install(
        report_at_exit=False,
        report_destination=report_destination,
        report_format=report_format,
        monitor_path_hooks=monitor_path_hooks,
        monitor_importer_cache=monitor_importer_cache,
        deep=deep,
        deep_path_hooks=deep_path_hooks,
        deep_path_entry_finders=deep_path_entry_finders,
        deep_loaders=deep_loaders,
        deep_import_outcomes=deep_import_outcomes,
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
