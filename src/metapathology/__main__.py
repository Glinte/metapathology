"""Command-line entry point: run a script or module under the monitor.

Mirrors the invocation style of ``python -m cProfile`` / ``python -m trace``.
The usual runpy caveats apply: the target runs as ``__main__`` but with a
slightly different ``__spec__`` than a direct invocation would have.
"""

import argparse
import sys

import metapathology
from metapathology._record import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal, NoReturn

_REPORT_FORMATS = ("text", "json")
_COLOR_MODES = ("auto", "always", "never")
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
        self.report_color: Literal["auto", "always", "never"] | None = None
        self.monitor_path_hooks: bool | None = None
        self.monitor_importer_cache: bool | None = None
        self.monitor_sys_path: bool | None = None
        self.deep: bool | None = None
        self.deep_path_hooks: bool | None = None
        self.deep_path_entry_finders: bool | None = None
        self.deep_loaders: bool | None = None
        self.deep_import_outcomes: bool | None = None
        self.deep_import_calls: bool | None = None
        self.speculative_replay: bool | None = None
        self.is_module = False
        self.target: str | None = None
        self.target_args: list[str] = []

    def invocation(self) -> "_Invocation":
        """Freeze parsed launch settings before target execution mutates process state."""
        return _Invocation(
            self.target,
            tuple(self.target_args),
            self.is_module,
            self.report_destination,
            self.report_format,
            self.report_color,
            self.monitor_path_hooks,
            self.monitor_importer_cache,
            self.monitor_sys_path,
            self.deep,
            self.deep_path_hooks,
            self.deep_path_entry_finders,
            self.deep_loaders,
            self.deep_import_outcomes,
            self.deep_import_calls,
            self.speculative_replay,
        )


class _Invocation(_Record):
    """Immutable CLI target and monitor configuration."""

    target: str | None
    target_args: tuple[str, ...]
    is_module: bool
    report_destination: str | None
    report_format: "Literal['text', 'json'] | None"
    report_color: "Literal['auto', 'always', 'never'] | None"
    monitor_path_hooks: bool | None
    monitor_importer_cache: bool | None
    monitor_sys_path: bool | None
    deep: bool | None
    deep_path_hooks: bool | None
    deep_path_entry_finders: bool | None
    deep_loaders: bool | None
    deep_import_outcomes: bool | None
    deep_import_calls: bool | None
    speculative_replay: bool | None


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
        "--color",
        dest="report_color",
        choices=_COLOR_MODES,
        help="color text reports automatically, always, or never",
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
    parser.add_argument(
        "--sys-path-monitoring",
        dest="monitor_sys_path",
        action=argparse.BooleanOptionalAction,
        help="enable opt-in sys.path mutation monitoring",
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
    deep.add_argument(
        "--deep-import-calls",
        action=argparse.BooleanOptionalAction,
        help="capture builtins.__import__ calls, including sys.modules cache hits",
    )
    deep.add_argument(
        "--speculative-replay",
        action=argparse.BooleanOptionalAction,
        help="at report time, replay a displaced importer-cache finder against a module that later failed on its path",
    )
    parser.add_argument("-m", dest="is_module", action="store_true", help="run TARGET as a module")
    parser.add_argument(
        "target",
        metavar="TARGET",
        nargs="?",
        default=None,
        help="script path, or module name with -m; omit to start a monitored interactive interpreter",
    )
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
    if parsed.target is None:
        if parsed.is_module:
            _PARSER._print_error("-m requires a module name")
            return 2
        if parsed.target_args:
            _PARSER._print_error("arguments require a TARGET")
            return 2
    return _run(parsed.invocation())


def _run(invocation: _Invocation) -> int:
    """Install the monitor, run the target via runpy, and always write the report.

    Args:
        invocation: Frozen target and monitoring configuration.

    Returns:
        The exit code a direct invocation of the target would produce.
    """
    # Validate script launch errors before installing: no target code or import
    # machinery ran, so a diagnostic report would describe only our bootstrap.
    import os

    target = invocation.target
    if target is not None and not invocation.is_module and not os.path.exists(target):
        _PARSER._print_error(f"script target does not exist: {target!r}")
        return 2

    # Keep target-execution dependencies off help and argument-error paths.
    # They still load before install(), never inside a hook or finder wrapper.
    # The interactive console module also loads here, before install(), so it
    # does not weaken the observation boundary.
    import code
    import runpy
    import traceback
    from contextlib import suppress

    if target is None:
        # Tab completion is quality-of-life only; readline is unavailable on
        # some platforms (notably Windows before the 3.13 REPL rewrite).
        with suppress(ImportError, AttributeError):
            import readline
            import rlcompleter  # noqa: F401

            readline.parse_and_bind("tab: complete")  # pyrefly: ignore[missing-attribute]

    monitor = metapathology.install(
        report_at_exit=False,
        report_destination=invocation.report_destination,
        report_format=invocation.report_format,
        report_color=invocation.report_color,
        monitor_path_hooks=invocation.monitor_path_hooks,
        monitor_importer_cache=invocation.monitor_importer_cache,
        monitor_sys_path=invocation.monitor_sys_path,
        deep=invocation.deep,
        deep_path_hooks=invocation.deep_path_hooks,
        deep_path_entry_finders=invocation.deep_path_entry_finders,
        deep_loaders=invocation.deep_loaders,
        deep_import_outcomes=invocation.deep_import_outcomes,
        deep_import_calls=invocation.deep_import_calls,
        speculative_replay=invocation.speculative_replay,
    )
    exit_code = 0
    try:
        if target is None:
            # Mimic the bare interpreter prompt: sys.argv == [''] and an
            # interactive namespace with metapathology preloaded for reports.
            sys.argv = [""]
            banner = (
                f"Python {sys.version} on {sys.platform}\n"
                f"metapathology {metapathology.__version__}: import monitoring is active; "
                "the report is written when this session ends.\n"
                "'metapathology' is preloaded — try print(metapathology.render_report()) after an import."
            )
            # readfunc=input keeps interact() from re-importing readline
            # inside the monitored window; our attempt above already ran.
            code.interact(
                banner=banner,
                readfunc=input,
                local={"__name__": "__console__", "__doc__": None, "metapathology": metapathology},
                exitmsg="",
            )
            monitor.record_target_outcome(exit_code=exit_code)
            return exit_code
        sys.argv = [target, *invocation.target_args]
        if invocation.is_module:
            # Mimic `python -m target`: cwd on sys.path; run_module fixes argv[0].
            # Bypass our optional list override: this is launcher setup, not a
            # target mutation, and should not appear as diagnostic evidence.
            list.insert(sys.path, 0, os.getcwd())
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        else:
            # Mimic `python target.py`: the script's directory on sys.path.
            list.insert(sys.path, 0, os.path.dirname(os.path.abspath(target)))
            runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        exit_code = _exit_code(exc)
        monitor.record_target_outcome(exit_code=exit_code)
    except Exception as exc:
        traceback.print_exc()
        exit_code = 1
        monitor.record_target_outcome(exception=exc, exit_code=exit_code)
    else:
        monitor.record_target_outcome(exit_code=exit_code)
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
