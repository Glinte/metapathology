#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "matplotlib>=3.9",
#   "psutil>=6",
# ]
# ///
"""Benchmark metapathology startup, import, mutation, and memory overhead."""

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias, cast

import matplotlib
import psutil
from _benchmark_cli import (
    PythonMetadata,
    inspect_python,
    parse_counts,
    parse_output_directory,
    parse_positive_int,
    parse_python_version,
    resolve_python,
    validate_expected_python,
)

matplotlib.use("Agg")
from matplotlib import pyplot as plt

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_WORKER = _SCRIPT_DIR / "_benchmark_worker.py"
_SCENARIOS = ("native", "attributed", "deep", "mutation")
_STARTUP_CASES = ("process", "package_import", "monitor_api_import", "direct_script", "monitored_script")
_COLORS = {False: "#6b7280", True: "#2563eb"}
_DEFAULT_COUNTS = "100,1000,5000"
_RecordValue: TypeAlias = str | bool | int | float
_Record: TypeAlias = dict[str, _RecordValue]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--counts",
        type=parse_counts,
        default=parse_counts(_DEFAULT_COUNTS),
        help=f"comma-separated workload sizes (default: {_DEFAULT_COUNTS})",
    )
    parser.add_argument("--repeats", type=parse_positive_int, default=5, help="fresh processes per timing point")
    parser.add_argument("--memory-repeats", type=parse_positive_int, default=3, help="fresh processes per memory point")
    parser.add_argument("--seed", type=int, default=0, help="reproducible trial-order shuffle seed")
    parser.add_argument(
        "--python",
        type=resolve_python,
        default=Path(sys.executable).resolve(),
        help="CPython executable path or command name to benchmark",
    )
    parser.add_argument(
        "--expect-python",
        type=parse_python_version,
        default=None,
        metavar="MAJOR.MINOR",
        help="fail unless the target interpreter has this major.minor version",
    )
    parser.add_argument(
        "--output-dir",
        type=parse_output_directory,
        default=None,
        help="result directory (default: .cache/metapathology-benchmarks/<timestamp>)",
    )
    parser.add_argument("--quick", action="store_true", help="use two small points and one sample for a smoke run")
    args = parser.parse_args()
    try:
        args.target = inspect_python(args.python)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        validate_expected_python(args.target, args.expect_python)
    except ValueError as exc:
        parser.error(str(exc))
    if args.quick:
        args.counts = [10, 50]
        args.repeats = 1
        args.memory_repeats = 1
    return args


def _make_fixture(root: Path, package: str, maximum: int) -> None:
    package_dir = root / package
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text('"""Synthetic benchmark package."""\n', encoding="utf-8")
    for index in range(maximum):
        source = f'"""Synthetic module {index}."""\nVALUE: int = {index}\n'
        (package_dir / f"module_{index:05d}.py").write_text(source, encoding="utf-8")
    (root / "benchmark_target.py").write_text('"""Minimal CLI startup target."""\n', encoding="utf-8")


def _worker_command(
    python: Path,
    fixture: Path,
    package: str,
    scenario: str,
    metric: str,
    count: int,
    monitored: bool,
) -> list[str]:
    command = [
        str(python),
        "-S",
        str(_WORKER),
        "--scenario",
        scenario,
        "--metric",
        metric,
        "--count",
        str(count),
        "--package",
        package,
        "--fixture",
        str(fixture),
    ]
    if monitored:
        command.append("--monitored")
    return command


def _worker_environment(fixture: Path) -> dict[str, str]:
    environment = os.environ.copy()
    source = str(_PROJECT_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    entries = (str(fixture), source) if not existing else (str(fixture), source, existing)
    environment["PYTHONPATH"] = os.pathsep.join(entries)
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _decode_line(line: str, command: Sequence[str]) -> _Record:
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"worker returned invalid JSON for {command!r}: {line!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"worker returned a non-object for {command!r}")
    return cast("_Record", value)


def _run_time(command: list[str], fixture: Path) -> _Record:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=_worker_environment(fixture),
    )
    lines = [line for line in completed.stdout.splitlines() if line]
    if len(lines) != 1:
        raise RuntimeError(f"unexpected timing-worker output: {completed.stdout!r}\nstderr: {completed.stderr!r}")
    return _decode_line(lines[0], command)


def _run_memory(command: list[str], fixture: Path) -> _Record:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_worker_environment(fixture),
    )
    assert process.stdout is not None
    assert process.stdin is not None
    ready_line = process.stdout.readline()
    ready = _decode_line(ready_line, command)
    if ready.get("kind") != "ready":
        process.kill()
        raise RuntimeError(f"memory worker did not send readiness record: {ready!r}")
    observed = psutil.Process(process.pid)
    rss_start = observed.memory_info().rss
    peak_rss = rss_start
    process.stdin.write("go\n")
    process.stdin.flush()
    while process.poll() is None:
        try:
            peak_rss = max(peak_rss, observed.memory_info().rss)
        except psutil.Error:
            break
        time.sleep(0.001)
    remainder = process.stdout.read()
    stderr = process.stderr.read() if process.stderr is not None else ""
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(
            f"memory worker exited with {process.returncode}: stdout={ready_line + remainder!r}, stderr={stderr!r}"
        )
    lines = [line for line in remainder.splitlines() if line]
    if len(lines) != 1:
        raise RuntimeError(f"unexpected memory-worker output: {ready_line + remainder!r}\nstderr: {stderr!r}")
    result = _decode_line(lines[0], command)
    result.update({key: value for key, value in ready.items() if key != "kind"})
    result["peak_rss_bytes"] = peak_rss - rss_start
    return result


def _startup_command(python: Path, fixture: Path, case: str) -> list[str]:
    """Build one fresh-process command for a startup or CLI timing case."""
    prefix = [str(python), "-S"]
    if case == "process":
        return [*prefix, "-c", "pass"]
    if case == "package_import":
        return [*prefix, "-c", "import metapathology"]
    if case == "monitor_api_import":
        return [*prefix, "-c", "from metapathology import install"]
    target = str(fixture / "benchmark_target.py")
    if case == "direct_script":
        return [*prefix, target]
    if case == "monitored_script":
        return [*prefix, "-m", "metapathology", target]
    raise ValueError(f"unknown startup benchmark case: {case}")


def _sample_startup(python: Path, fixture: Path, repeats: int, seed: int) -> list[_Record]:
    """Measure package and CLI startup in shuffled fresh interpreter processes."""
    trials = [(case, repeat) for case in _STARTUP_CASES for repeat in range(repeats)]
    random.Random(seed).shuffle(trials)
    rows: list[_Record] = []
    environment = _worker_environment(fixture)
    for number, (case, repeat) in enumerate(trials, start=1):
        command = _startup_command(python, fixture, case)
        started = time.perf_counter_ns()
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        elapsed_seconds = (time.perf_counter_ns() - started) / 1_000_000_000
        rows.append({"case": case, "repeat": repeat, "elapsed_seconds": elapsed_seconds})
        print(f"[startup {number:3}/{len(trials)}] {case}", flush=True)
    return rows


def _sample(
    python: Path,
    fixture: Path,
    package: str,
    counts: list[int],
    repeats: int,
    memory_repeats: int,
    seed: int,
) -> list[_Record]:
    rows: list[_Record] = []
    trials: list[tuple[str, int, bool, str, int]] = []
    for scenario in _SCENARIOS:
        for count in counts:
            for monitored in (False, True):
                for metric, samples in (("time", repeats), ("memory", memory_repeats)):
                    trials.extend((scenario, count, monitored, metric, repeat) for repeat in range(samples))
    random.Random(seed).shuffle(trials)
    for number, (scenario, count, monitored, metric, repeat) in enumerate(trials, start=1):
        command = _worker_command(python, fixture, package, scenario, metric, count, monitored)
        result = _run_time(command, fixture) if metric == "time" else _run_memory(command, fixture)
        rows.append(
            {
                "scenario": scenario,
                "metric": metric,
                "monitored": monitored,
                "count": count,
                "repeat": repeat,
                **{key: value for key, value in result.items() if key != "kind"},
            }
        )
        print(
            f"[{number:3}/{len(trials)}] {scenario:10} {metric:6} count={count:5} monitored={monitored}",
            flush=True,
        )
    return rows


def _values(rows: list[_Record], scenario: str, metric: str, monitored: bool, count: int, field: str) -> list[float]:
    return [
        float(row[field])
        for row in rows
        if row["scenario"] == scenario
        and row["metric"] == metric
        and row["monitored"] is monitored
        and row["count"] == count
    ]


def _median_series(
    rows: list[_Record], counts: list[int], scenario: str, metric: str, monitored: bool, field: str
) -> list[float]:
    return [statistics.median(_values(rows, scenario, metric, monitored, count, field)) for count in counts]


def _startup_median(rows: list[_Record], case: str) -> float:
    """Return the median elapsed seconds for one startup case."""
    return statistics.median(float(row["elapsed_seconds"]) for row in rows if row["case"] == case)


def _plot_imports(rows: list[_Record], counts: list[int], output: Path) -> None:
    figure, axes = plt.subplots(2, 3, figsize=(18, 8), constrained_layout=True)
    for column, scenario in enumerate(("native", "attributed", "deep")):
        axis = axes.flat[column]
        for monitored in (False, True):
            elapsed = _median_series(rows, counts, scenario, "time", monitored, "elapsed_seconds")
            axis.plot(
                counts,
                [value * 1_000 for value in elapsed],
                marker="o",
                color=_COLORS[monitored],
                label="monitored" if monitored else "control",
            )
        axis.set_title(f"{scenario.capitalize()} import time")
        axis.set_xlabel("imported modules")
        axis.set_ylabel("median elapsed (ms)")
        axis.grid(alpha=0.25)
        axis.legend()

    slowdown_axis = axes.flat[3]
    memory_axis = axes.flat[4]
    report_axis = axes.flat[5]
    for scenario, color in (("native", "#059669"), ("attributed", "#dc2626"), ("deep", "#7c3aed")):
        control = _median_series(rows, counts, scenario, "time", False, "elapsed_seconds")
        monitored = _median_series(rows, counts, scenario, "time", True, "elapsed_seconds")
        slowdown_axis.plot(
            counts,
            [after / before for before, after in zip(control, monitored, strict=True)],
            marker="o",
            label=scenario,
            color=color,
        )
        control_memory = _median_series(rows, counts, scenario, "memory", False, "traced_current_bytes")
        monitored_memory = _median_series(rows, counts, scenario, "memory", True, "traced_current_bytes")
        overhead = [
            (after - before) / (1024 * 1024) for before, after in zip(control_memory, monitored_memory, strict=True)
        ]
        memory_axis.plot(counts, overhead, marker="o", label=scenario, color=color)
        report_axis.plot(
            counts,
            [value * 1_000 for value in _median_series(rows, counts, scenario, "time", True, "report_seconds")],
            marker="o",
            label=scenario,
            color=color,
        )
    slowdown_axis.axhline(1.0, color="#111827", linewidth=1)
    slowdown_axis.set_title("Import slowdown")
    slowdown_axis.set_xlabel("imported modules")
    slowdown_axis.set_ylabel("monitored / control")
    slowdown_axis.grid(alpha=0.25)
    slowdown_axis.legend()
    memory_axis.axhline(0.0, color="#111827", linewidth=1)
    memory_axis.set_title("Retained Python-memory overhead")
    memory_axis.set_xlabel("imported modules")
    memory_axis.set_ylabel("monitored - control (MiB)")
    memory_axis.grid(alpha=0.25)
    memory_axis.legend()
    report_axis.set_title("JSON report rendering")
    report_axis.set_xlabel("imported modules")
    report_axis.set_ylabel("median elapsed (ms)")
    report_axis.grid(alpha=0.25)
    report_axis.legend()
    figure.suptitle("metapathology import overhead")
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _plot_mutations(rows: list[_Record], counts: list[int], output: Path) -> None:
    figure, axes = plt.subplots(1, 4, figsize=(20, 4.5), constrained_layout=True)
    control = _median_series(rows, counts, "mutation", "time", False, "elapsed_seconds")
    monitored = _median_series(rows, counts, "mutation", "time", True, "elapsed_seconds")
    for enabled, elapsed in ((False, control), (True, monitored)):
        axes[0].plot(
            counts,
            [value * 1_000 for value in elapsed],
            marker="o",
            color=_COLORS[enabled],
            label="monitored" if enabled else "control",
        )
    axes[0].set_title("Mutation time")
    axes[0].set_ylabel("median elapsed (ms)")
    axes[0].legend()
    axes[1].plot(
        counts, [after / before for before, after in zip(control, monitored, strict=True)], marker="o", color="#7c3aed"
    )
    axes[1].axhline(1.0, color="#111827", linewidth=1)
    axes[1].set_title("Mutation slowdown")
    axes[1].set_ylabel("monitored / control")
    control_memory = _median_series(rows, counts, "mutation", "memory", False, "traced_current_bytes")
    monitored_memory = _median_series(rows, counts, "mutation", "memory", True, "traced_current_bytes")
    axes[2].plot(
        counts,
        [(after - before) / (1024 * 1024) for before, after in zip(control_memory, monitored_memory, strict=True)],
        marker="o",
        color="#dc2626",
    )
    axes[2].axhline(0.0, color="#111827", linewidth=1)
    axes[2].set_title("Retained Python-memory overhead")
    axes[2].set_ylabel("monitored - control (MiB)")
    report = _median_series(rows, counts, "mutation", "time", True, "report_seconds")
    axes[3].plot(counts, [value * 1_000 for value in report], marker="o", color="#7c3aed")
    axes[3].set_title("JSON report rendering")
    axes[3].set_ylabel("median elapsed (ms)")
    for axis in axes:
        axis.set_xlabel("pop/append pairs")
        axis.grid(alpha=0.25)
    figure.suptitle("metapathology sys.meta_path mutation overhead")
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _write_summary(
    rows: list[_Record], startup_rows: list[_Record], counts: list[int], target: PythonMetadata, output: Path
) -> None:
    lines = [
        "# metapathology benchmark",
        "",
        f"Target: `{target['version'].splitlines()[0]}` on `{target['platform']}`.",
        "",
    ]
    install_times = [
        float(row["install_seconds"]) for row in rows if row["metric"] == "time" and row["monitored"] is True
    ]
    lines.extend(
        (
            f"Median monitor installation time: **{statistics.median(install_times) * 1_000:.3f} ms**.",
            "",
            "## Startup and CLI workload",
            "",
            "| Case | Median wall time (ms) |",
            "| --- | ---: |",
        )
    )
    lines.extend(
        f"| {case.replace('_', ' ')} | {_startup_median(startup_rows, case) * 1_000:.3f} |" for case in _STARTUP_CASES
    )
    import_overhead = _startup_median(startup_rows, "package_import") - _startup_median(startup_rows, "process")
    cli_overhead = _startup_median(startup_rows, "monitored_script") - _startup_median(startup_rows, "direct_script")
    lines.extend(
        (
            "",
            f"Package import overhead above process startup: **{import_overhead * 1_000:.3f} ms**.",
            f"CLI wrapper overhead above direct script execution: **{cli_overhead * 1_000:.3f} ms**.",
            "",
            "## Import workload",
            "",
            "| Scenario | Modules | Control (ms) | Monitored (ms) | Time ratio | Retained overhead (KiB) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for scenario in ("native", "attributed", "deep"):
        for count in counts:
            control_time = statistics.median(_values(rows, scenario, "time", False, count, "elapsed_seconds"))
            monitored_time = statistics.median(_values(rows, scenario, "time", True, count, "elapsed_seconds"))
            control_memory = statistics.median(_values(rows, scenario, "memory", False, count, "traced_current_bytes"))
            monitored_memory = statistics.median(_values(rows, scenario, "memory", True, count, "traced_current_bytes"))
            lines.append(
                f"| {scenario} | {count} | {control_time * 1_000:.3f} | {monitored_time * 1_000:.3f} "
                f"| {monitored_time / control_time:.3f}x | {(monitored_memory - control_memory) / 1024:.2f} |"
            )
    lines.extend(
        (
            "",
            "## JSON report rendering",
            "",
            "The report is rendered after the workload. It is not included in the import or mutation timing above.",
            "",
            "| Scenario | Modules / pairs | Render time (ms) | Render peak allocation (KiB) | JSON size (KiB) |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for scenario in (*("native", "attributed", "deep"), "mutation"):
        for count in counts:
            report_time = statistics.median(_values(rows, scenario, "time", True, count, "report_seconds"))
            report_peak = statistics.median(_values(rows, scenario, "memory", True, count, "report_peak_bytes"))
            report_size = statistics.median(_values(rows, scenario, "time", True, count, "report_bytes"))
            lines.append(
                f"| {scenario} | {count} | {report_time * 1_000:.3f} | {report_peak / 1024:.2f} | {report_size / 1024:.2f} |"
            )
    lines.extend(
        (
            "",
            "## `sys.meta_path` mutation workload",
            "",
            "| Pop/append pairs | Control (ms) | Monitored (ms) | Time ratio | Retained overhead (KiB) |",
            "| ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for count in counts:
        control_time = statistics.median(_values(rows, "mutation", "time", False, count, "elapsed_seconds"))
        monitored_time = statistics.median(_values(rows, "mutation", "time", True, count, "elapsed_seconds"))
        control_memory = statistics.median(_values(rows, "mutation", "memory", False, count, "traced_current_bytes"))
        monitored_memory = statistics.median(_values(rows, "mutation", "memory", True, count, "traced_current_bytes"))
        lines.append(
            f"| {count} | {control_time * 1_000:.3f} | {monitored_time * 1_000:.3f} "
            f"| {monitored_time / control_time:.3f}x | {(monitored_memory - control_memory) / 1024:.2f} |"
        )
    lines.extend(
        (
            "",
            "Times and retained allocations are medians of fresh-process samples. "
            "See `benchmark.json` for every sample and the complete environment metadata.",
            "",
        )
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def _git_revision() -> str | None:
    if shutil.which("git") is None:
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() or None


def main() -> int:
    """Collect isolated samples, persist raw data, and render two graphs."""
    args = _parse_args()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or (_PROJECT_ROOT / ".cache" / "metapathology-benchmarks" / timestamp)
    output.mkdir(parents=True, exist_ok=True)
    package = "metapathology_benchmark_fixture"
    with tempfile.TemporaryDirectory(prefix="metapathology-benchmark-") as temporary:
        fixture = Path(temporary)
        _make_fixture(fixture, package, max(args.counts))
        startup_rows = _sample_startup(args.python, fixture, args.repeats, args.seed)
        rows = _sample(
            args.python,
            fixture,
            package,
            args.counts,
            args.repeats,
            args.memory_repeats,
            args.seed,
        )
    target = args.target
    document = {
        "schema_version": 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_driver_python": sys.version,
        "benchmark_driver_platform": platform.platform(),
        "target": target,
        "git_revision": _git_revision(),
        "configuration": {
            "counts": args.counts,
            "repeats": args.repeats,
            "memory_repeats": args.memory_repeats,
            "seed": args.seed,
        },
        "startup_rows": startup_rows,
        "rows": rows,
    }
    data_path = output / "benchmark.json"
    data_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    import_graph = output / "imports.png"
    mutation_graph = output / "mutations.png"
    summary_path = output / "summary.md"
    _plot_imports(rows, args.counts, import_graph)
    _plot_mutations(rows, args.counts, mutation_graph)
    _write_summary(rows, startup_rows, args.counts, target, summary_path)
    print(f"raw data:       {data_path}")
    print(f"summary:        {summary_path}")
    print(f"import graph:   {import_graph}")
    print(f"mutation graph: {mutation_graph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
