# Speed and memory use

`metapathology` is a diagnostic tool rather than permanent application
instrumentation. Its overhead depends primarily on which finder handles an
import and how much `sys.meta_path` changes during the capture window.

## Where the cost comes from

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

The import graph separates two cases:

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

## Reference results

A [default environment-matrix run][reference-run] on July 14, 2026, measured
commit `a94f2ee` on GitHub-hosted Linux, Windows, and macOS runners with CPython
3.10 and 3.14. Each environment used five fresh-process timing samples and
three memory samples per point.

The 400-operation points produced these cross-environment ranges:

| Measure | Range | Median across environments |
| --- | ---: | ---: |
| Monitor installation | 0.020–0.043 ms | 0.033 ms |
| 400 native imports, monitored/control time | 1.02–1.11x | 1.03x |
| 400 attributed imports, monitored/control time | 1.00–1.19x | 1.13x |
| Retained memory per attributed finder-call record | 203–240 bytes | 223 bytes |
| Monitored `pop`/`append` pair | 17–44 µs | 29 µs |
| Retained memory per mutation record | 665–830 bytes | 747 bytes |

The native result is the best approximation of import overhead when only the
standard class finders handle imports and no finder-call records are retained.
The attributed result includes search-path snapshots and retained records.
Mutation ratios are intentionally not summarized: a plain-list `pop`/`append`
pair is so short that a relative multiplier exaggerates the practical cost;
the absolute microseconds per pair are more useful.

These figures describe the synthetic workload on shared hosted runners, not a
performance guarantee. Real applications can probe several instrumentable
finders per import, use longer search paths, or mutate `sys.meta_path` with
deeper stacks. Re-run the benchmark in the target environment when sizing a
long capture.

## Run the environment matrix

The repository's [**Benchmarks** GitHub Actions workflow][benchmark-workflow]
is manually triggered with `workflow_dispatch`. By default it benchmarks
Python 3.10 and 3.14 on Linux, Windows, and macOS. Workload sizes, timing
repetitions, memory repetitions, and the shuffle seed can be changed in the
dispatch form.

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
[reference-run]: https://github.com/Glinte/metapathology/actions/runs/29309310010
