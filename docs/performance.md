# Speed and memory use

`metapathology` is a diagnostic tool rather than permanent application
instrumentation. Its overhead depends primarily on which finder handles an
import, how much `sys.meta_path` or `sys.path_hooks` changes, and the size of
`sys.path_importer_cache` at passive observation boundaries.

## Where the cost comes from

Importing the top-level package does not load the monitor implementation until
a public monitoring or record API is first accessed. CLI help and argument
errors likewise avoid importing target-execution modules. A real monitored run
still loads all dependencies needed by hooks and finder wrappers before
`install()` enables observation; this fixed startup work is intentional.

An uncached builtin import normally invokes the audit hook. Each observed
resolution start copies the current `sys.meta_path` identity and finder type
names and retains one slotted `ImportAuditStart`. It also performs a
thread-local re-entrancy check and identity comparisons for the enabled
instrumented lists. Standard CPython class finders such as `PathFinder` are
deliberately not wrapped, so a normal import resolved entirely by those entries
creates an audit-start record but no finder-call record. Lower-level importlib
entry points such as `importlib.import_module()` can bypass this audit boundary.

When importer-cache monitoring is enabled, that same audit path additionally
reads only the cache dictionary's identity and length. It never performs a
full cache scan inside the audit hook. Full scans occur at installation,
before and after observed path-hook mutations, and during report capture, so
their cost is proportional to cache size but normally paid at rare boundaries.

Instrumentable finder instances have their `find_spec()` method wrapped. Each
probe snapshots the effective search path and retains a `FindSpecCall`, whether
the finder claims the module or returns `None`. Time and memory therefore grow
with finder probes, not merely with successfully imported modules. A custom
finder early in `sys.meta_path` may see one probe per uncached import; several
instrumentable finders that decline the same import can produce several
records.

Event and report records use small read-only slotted classes rather than
dataclasses. This keeps captured snapshots immutable while avoiding dataclass
code-generation and value-comparison costs in import hot paths.

List mutations are intentionally heavier. Each append, removal, replacement,
or reorder captures a stack summary for attribution. Reassignments capture a
stack when the next import detects them. These records consume more time and
memory than finder-call records, but import-list mutations are normally
rare compared with imports.

The monitor keeps every event so the final report is exhaustive. Retained
memory grows approximately with the number and kind of recorded events until
the monitor is uninstalled; there is no fixed limit or silent dropping policy.
Importer-cache full-snapshot storage is bounded to the install and rolling
latest maps; its diff events follow the retain-all event policy. Concurrent
snapshot requests are coalesced without a queue.

## Limit the capture window

Install immediately before the behavior being investigated, then report and
uninstall as soon as it finishes:

```python
import metapathology

monitor = metapathology.install(report_at_exit=False)
try:
    import package_under_investigation
finally:
    metapathology.write_report()
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

- `native` uses only the controlled standard-finder path and measures one
  retained audit-start record per synthetic builtin import, without
  finder-call records.
- `attributed` installs the same delegating instance finder in control and
  monitored processes. The monitored process retains an audit-start plus one
  finder-call record per synthetic import.

The mutation graph measures repeated `pop`/`append` pairs, including stack
capture. Timing and memory trials are separate so `tracemalloc` does not alter
the speed measurements. Workers disable `site` initialization and bytecode
writes, use `__import__()` so the workload crosses the monitored audit
boundary, and shuffle trial order from a recorded seed to reduce cache and
warm-up bias.

Each graph plots medians. `benchmark.json` contains every sample, the target
Python and platform, the Git revision, configuration, event counts, retained
and peak traced allocations, and sampled peak RSS growth. Treat small timing
differences as noise unless they persist across more repetitions and dedicated
hardware.

## Pre-T3 reference results

A [default environment-matrix run][reference-run] measured commit `56f0d5b`
on GitHub-hosted Linux, Windows, and macOS runners with CPython 3.10 and 3.14.
Each environment used five fresh-process timing samples and three memory
samples per point. This commit predates retained audit-start records, so these
numbers are a historical v0.3 baseline and do not describe the current native
scenario. Run the benchmark on the current revision when sizing T3 captures.

The 5,000-operation points produced these cross-environment results:

| Measure | Range | Median across environments |
| --- | ---: | ---: |
| Monitor installation | 0.021–0.045 ms | 0.030 ms |
| 5,000 native imports, monitored/control time | 0.90–1.05x | 1.03x |
| 5,000 attributed imports, monitored/control time | 1.01–1.17x | 1.11x |
| Retained memory per attributed finder-call record | 148–151 bytes | 150 bytes |
| Monitored `pop`/`append` pair | 16–42 µs | 30 µs |
| Retained memory per mutation record | 672–836 bytes | 754 bytes |

In that pre-T3 revision, the native result approximated imports without any
retained per-import record, while the attributed result included search-path
snapshots and finder-call records. Current runs retain audit starts in both
scenarios.
Mutation ratios are intentionally not summarized: a plain-list `pop`/`append`
pair is so short that a relative multiplier exaggerates the practical cost;
the absolute microseconds per pair are more useful.

These figures describe synthetic workloads on shared hosted runners, not a
performance guarantee. Real applications can probe several instrumentable
finders per import, use longer search paths, or mutate `sys.meta_path` with
deeper stacks. Re-run the benchmark in the target environment when sizing a
long capture.

Deep diagnostics retain one constant-size record for every observed delegated
boundary for the lifetime of the monitor. This is exhaustive and unbounded in
the number of calls: no events are silently dropped. Path-hook, path-entry
finder, and loader wrappers also add one Python call boundary to the selected
foreign operations, so their overhead is intentionally outside the default
mode's performance guarantee.

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
[reference-run]: https://github.com/Glinte/metapathology/actions/runs/29398284346
