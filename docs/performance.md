# Speed and memory use

`metapathology` is a diagnostic tool rather than permanent application
instrumentation. Its overhead depends primarily on which finder handles an
import and how much `sys.meta_path` changes during the capture window.

## Where the cost comes from

Importing the top-level package does not load the monitor implementation until
a public monitoring or record API is first accessed. CLI help and argument
errors likewise avoid importing target-execution modules. A real monitored run
still loads all dependencies needed by hooks and finder wrappers before
`install()` enables observation; this fixed startup work is intentional.

An uncached import always invokes the audit hook. When `sys.meta_path` has not
been replaced, that path performs an enabled check, a thread-local re-entrancy
check, and an identity comparison. Standard CPython class finders such as
`PathFinder` are deliberately not wrapped, so a normal import resolved entirely
by those entries does not create a finder-call record.

Instrumentable finder instances have their `find_spec()` method wrapped. Each
probe snapshots the effective search path and retains a `FindSpecCall`, whether
the finder claims the module or returns `None`. Time and memory therefore grow
with finder probes, not merely with successfully imported modules. A custom
finder early in `sys.meta_path` may see one probe per uncached import; several
instrumentable finders that decline the same import can produce several
records.

Event records use small read-only slotted classes rather than dataclasses.
This keeps externally visible snapshots immutable while avoiding dataclass
code-generation and value-comparison costs in import hot paths.

List mutations are intentionally heavier. Each append, removal, replacement,
or reorder captures a stack summary for attribution. Reassignments capture a
stack when the next import detects them. These records consume more time and
memory than finder-call records, but `sys.meta_path` mutations are normally
rare compared with imports.

The monitor keeps every event so the final report is exhaustive. Retained
memory grows approximately with the number and kind of recorded events until
the monitor is uninstalled; there is no fixed limit or silent dropping policy.

## Limit the capture window

Install immediately before the behavior being investigated, then report and
uninstall as soon as it finishes:

```python
import metapathology

monitor = metapathology.install(report_at_exit=False)
try:
    import package_under_investigation
finally:
    metapathology.report()
    metapathology.uninstall()
```

This bounds both runtime perturbation and retained event data. Modules already
present in `sys.modules` are cache hits and do not generate new finder probes.

## Reproduce the benchmarks

The repository [`scripts/benchmark.py`][benchmark-script] compares control and
monitored workloads in fresh processes and generates raw JSON, a Markdown
summary, and two PNG graphs:

```console
uv run --script scripts/benchmark.py
```

The summary first reports fresh-process startup, package import, deferred
monitor-API import, direct-script, and monitored-CLI timings. The import graph
separates two cases:

- `native` uses only the controlled standard-finder path and measures the
  audit-hook/list-check overhead without retained finder-call records.
- `attributed` installs the same delegating instance finder in control and
  monitored processes. The monitored process retains one finder-call record
  per synthetic import, exposing per-record time and memory growth.

The mutation graph measures repeated `pop`/`append` pairs, including stack
capture. Timing and memory trials are separate so `tracemalloc` does not alter
the speed measurements. Workers disable `site` initialization and bytecode
writes, and trial order is shuffled from a recorded seed to reduce cache and
warm-up bias.

Each graph plots medians. `benchmark.json` contains every sample, the target
Python and platform, the Git revision, configuration, event counts, retained
and peak traced allocations, and sampled peak RSS growth. Treat small timing
differences as noise unless they persist across more repetitions and dedicated
hardware.

## Run the environment matrix

The repository's [**Benchmarks** GitHub Actions workflow][benchmark-workflow]
is manually triggered with `workflow_dispatch`. By default it benchmarks
Python 3.10 and 3.14 on Linux, Windows, and macOS at 100, 1,000, and 5,000
operations. Workload sizes, timing repetitions, memory repetitions, and the
shuffle seed can be changed in the dispatch form.

Each matrix job adds its Markdown table to the workflow summary and uploads a
30-day artifact containing:

- `benchmark.json` with all measurements and environment metadata;
- `summary.md` with median tables;
- `imports.png`; and
- `mutations.png`.

Hosted runners are shared, variable machines. Their results are useful for
cross-platform shape and large regressions, but they are not a stable
microbenchmark baseline. Use a fixed self-hosted runner and more repetitions
before treating a small percentage change as significant.

[benchmark-script]: https://github.com/Glinte/metapathology/blob/main/scripts/benchmark.py
[benchmark-workflow]: https://github.com/Glinte/metapathology/actions/workflows/benchmark.yml
