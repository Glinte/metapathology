"""Isolated worker for ``scripts/benchmark.py``.

This file intentionally has no inline dependencies. The parent may run it with
any supported CPython interpreter without installing benchmark libraries into
the process whose imports are being measured.
"""

import argparse
import gc
import json
import sys
import time
import tracemalloc
from collections.abc import Sequence
from importlib.machinery import ModuleSpec, PathFinder
from pathlib import Path
from types import ModuleType

import metapathology

_ResultValue = int | float | str
# Keep implementation-module loading outside timed and traced regions, as it
# was before the package began exposing its public API lazily. Startup cases in
# benchmark.py measure the one-time public-API cost separately.
_install = metapathology.install
_render_report = metapathology.render_report


class _DelegatingFinder:
    """An instance finder that delegates synthetic imports to ``PathFinder``."""

    def __init__(self, package: str) -> None:
        self._package = package

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname == self._package or fullname.startswith(f"{self._package}."):
            return PathFinder.find_spec(fullname, path, target)
        return None


class _NoOpFinder:
    """Settable finder used to exercise instrumented-list mutations."""

    @staticmethod
    def find_spec(
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> None:
        return None


def _positive_int(value: str) -> int:
    """Parse a positive worker count."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _existing_directory(value: str) -> Path:
    """Parse an existing fixture directory."""
    path = Path(value)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"fixture directory does not exist: {value}")
    return path


def _package_name(value: str) -> str:
    """Parse a dotted Python package name."""
    if not value or any(not part.isidentifier() for part in value.split(".")):
        raise argparse.ArgumentTypeError(f"invalid package name: {value}")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("native", "attributed", "deep", "mutation"), required=True)
    parser.add_argument("--metric", choices=("time", "memory"), required=True)
    parser.add_argument("--count", type=_positive_int, required=True)
    parser.add_argument("--package", type=_package_name, required=True)
    parser.add_argument("--fixture", type=_existing_directory, required=True)
    parser.add_argument("--monitored", action="store_true")
    return parser.parse_args()


def _prepare(
    args: argparse.Namespace,
) -> tuple[metapathology.Monitor | None, list[str], _NoOpFinder | None, float]:
    sys.path.insert(0, str(args.fixture))
    names = [f"{args.package}.module_{index:05d}" for index in range(args.count)]
    if args.scenario == "attributed":
        sys.meta_path.insert(0, _DelegatingFinder(args.package))
    monitor = None
    install_seconds = 0.0
    if args.monitored:
        started = time.perf_counter()
        monitor = _install(
            report_at_exit=False,
            capture=metapathology.CaptureConfig(
                deep=metapathology.DeepConfig(
                    path_hooks=args.scenario == "deep",
                    path_entry_finders=args.scenario == "deep",
                    loaders=args.scenario == "deep",
                    import_outcomes=args.scenario == "deep",
                )
            ),
        )
        install_seconds = time.perf_counter() - started
    mutation_finder = None
    if args.scenario == "mutation":
        mutation_finder = _NoOpFinder()
        sys.meta_path.append(mutation_finder)
    return monitor, names, mutation_finder, install_seconds


def _run_workload(args: argparse.Namespace, names: list[str], mutation_finder: _NoOpFinder | None) -> None:
    if args.scenario == "mutation":
        assert mutation_finder is not None
        for _ in range(args.count):
            sys.meta_path.pop()
            sys.meta_path.append(mutation_finder)
        return
    for name in names:
        # importlib.import_module() bypasses the builtin audit boundary that
        # this workload is intended to measure.
        __import__(name)


def _event_count(monitor: metapathology.Monitor | None) -> int:
    return 0 if monitor is None else len(monitor.events())


def _time_trial(args: argparse.Namespace) -> dict[str, _ResultValue]:
    monitor, names, mutation_finder, install_seconds = _prepare(args)
    started = time.perf_counter_ns()
    _run_workload(args, names, mutation_finder)
    elapsed_ns = time.perf_counter_ns() - started
    event_count = _event_count(monitor)
    report_seconds = 0.0
    report_bytes = 0
    if monitor is not None:
        report_started = time.perf_counter_ns()
        report = _render_report(format="json")
        report_seconds = (time.perf_counter_ns() - report_started) / 1_000_000_000
        report_bytes = len(report.encode("utf-8"))
    return {
        "elapsed_seconds": elapsed_ns / 1_000_000_000,
        "event_count": event_count,
        "install_seconds": install_seconds,
        "report_seconds": report_seconds,
        "report_bytes": report_bytes,
    }


def _memory_trial(args: argparse.Namespace) -> dict[str, _ResultValue]:
    tracemalloc.start()
    gc.collect()
    before_install, _ = tracemalloc.get_traced_memory()
    monitor, names, mutation_finder, install_seconds = _prepare(args)
    gc.collect()
    after_install, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    ready = {
        "kind": "ready",
        "install_seconds": install_seconds,
        "traced_install_bytes": after_install - before_install,
    }
    print(json.dumps(ready), flush=True)
    if not sys.stdin.readline():
        raise RuntimeError("benchmark parent closed the memory-trial handshake")
    _run_workload(args, names, mutation_finder)
    gc.collect()
    current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    if monitor is not None:
        report = _render_report(format="json")
        del report
    gc.collect()
    _, report_peak = tracemalloc.get_traced_memory()
    return {
        "event_count": _event_count(monitor),
        "traced_current_bytes": current - after_install,
        "report_peak_bytes": report_peak - current,
    }


def main() -> int:
    """Run one isolated sample and write its result as JSON."""
    args = _parse_args()
    result = _time_trial(args) if args.metric == "time" else _memory_trial(args)
    result["kind"] = "result"
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
