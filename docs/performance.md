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
monitor-API import, direct-script, and monitored-CLI timings. By default, the
import graph separates two cases:

- `native` uses only the controlled standard-finder path and measures one
  retained audit-start record per synthetic builtin import, without
  finder-call records.
- `attributed` installs the same delegating instance finder in control and
  monitored processes. The monitored process retains an audit-start plus one
  finder-call record per synthetic import.

Pass `--include-deep` to add a third case:

- `deep` (enabled by `--include-deep`) enables every opt-in deep diagnostic around the controlled
  standard-finder path. This measures the delegated path-hook,
  path-entry-finder, loader, and CPython import-outcome capture path.

Every monitored sample then renders the JSON report after its workload. That
separate measurement includes report-time route analysis and loader inventory,
without folding report cost into import or mutation throughput. The graphs and
summary show report-render time and allocation peak, plus rendered JSON size.

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

## Current reference results

A [default environment-matrix run][reference-run] and separate
[deep-diagnostics run][deep-reference-run] measured the current benchmark on
GitHub-hosted Linux, Windows, and macOS runners with CPython 3.10 and 3.14.
Each environment used five fresh-process timing samples and three memory
samples per point. The tables take the median within each environment, then
show the range and median across the six environments.

The default monitor's 5,000-operation points produced these results:

| Measure | Cross-environment range | Median |
| --- | ---: | ---: |
| Monitor installation | 0.107–0.671 ms | 0.207 ms |
| Native imports, monitored/control time | 0.96–1.19x | 1.10x |
| Attributed imports, monitored/control time | 1.18–1.65x | 1.35x |
| Retained overhead per native import | 335–349 bytes | 342 bytes |
| Retained overhead per attributed import | 934–1,014 bytes | 974 bytes |
| Monitored `pop`/`append` pair | 21.8–42.3 µs | 39.9 µs |
| Retained overhead per `pop`/`append` pair | 1.31–1.63 KiB | 1.47 KiB |
| Native JSON report rendering | 0.163–0.775 s | 0.355 s |
| Attributed JSON report rendering | 0.564–3.033 s | 1.375 s |

The deep monitor's 1,000-import point produced these results:

| Measure | Cross-environment range | Median |
| --- | ---: | ---: |
| Monitored/control time | 7.06–15.34x | 13.08x |
| Monitored import time | 0.72–2.17 s | 1.40 s |
| Retained overhead per import | 3.65–4.38 KiB | 4.01 KiB |
| JSON report rendering | 2.69–32.79 s | 11.24 s |
| JSON report peak allocation | 24.94–51.46 MiB | 38.19 MiB |
| Rendered JSON size | 6.98–7.22 MiB | 7.19 MiB |

Deep report rendering has a pronounced interpreter-version split in this
run: Python 3.14 took 2.69–4.69 seconds, while Python 3.10 took 17.79–32.79
seconds. Mutation ratios are intentionally not summarized: a plain-list
`pop`/`append` pair is so short that a relative multiplier exaggerates the
practical cost; the absolute microseconds per pair are more useful.

These figures describe synthetic workloads on shared hosted runners, not a
performance guarantee. Real applications can probe several instrumentable
finders per import, use longer search paths, or mutate `sys.meta_path` with
deeper stacks. Re-run the benchmark in the target environment when sizing a
long capture.

Deep diagnostics retain one constant-size record for every observed delegated
boundary for the lifetime of the monitor. This is exhaustive and unbounded in
the number of calls: a modern loader can produce separate creation and
execution records, and no events are silently dropped. Path-hook, path-entry
finder, and loader wrappers also add one Python call boundary to the selected
foreign operations, so their overhead is intentionally outside the default
mode's performance guarantee.

When deep import outcomes are active on a supported CPython, successful
aggregate `PathFinder` results add one constant-size semantic spec record to
the same exhaustive event log. They introduce no separate queue, cache, retry
loop, or overflow policy. Text displays at most 50 derived standard outcomes;
JSON retains all attempt-linked outcomes.

The post-hoc loader inventory is not a lifetime producer. Each report copies
`sys.modules.items()` once and retains plain metadata proportional to the
number of string-keyed entries for the lifetime of that report document. Text
output caps displayed module names per loader; JSON intentionally projects the
complete copied inventory.

Finder-contract capture retains one constant-size plain record for every
distinct meta-path object observed while monitoring is active. This set grows
with distinct inserted finders, retains all records, and drops none. Raw
protocol inspection occurs only on first observation, never per finder call;
text shows at most 50 risk/standard entries while JSON remains exhaustive.

## Run the environment matrix

The repository's [**Benchmarks** GitHub Actions workflow][benchmark-workflow]
is manually triggered with `workflow_dispatch`. It benchmarks the default
monitoring configuration on
Python 3.10 and 3.14 on Linux, Windows, and macOS at 100, 1,000, and 5,000
operations. Workload sizes, timing repetitions, memory repetitions, and the
shuffle seed can be changed in the dispatch form.

The separate [**Deep benchmarks** workflow][deep-benchmark-workflow] runs only
the opt-in deep scenario at 100 and 1,000 operations with five timing and
three memory samples. Deep capture and report generation retain exhaustive
evidence, so its costs are intentionally kept out of the normal regression
matrix. Use its dispatch inputs to investigate a specific environment at a
larger size.

Each matrix job adds its Markdown table to the workflow summary and uploads a
30-day artifact containing:

- `benchmark.json` with all measurements and environment metadata;
- `summary.md` with median tables;
- `imports.png`; and
- `mutations.png` for the default workflow. The deep-only workflow has no
  mutation workload and therefore omits this graph.

Hosted runners are shared, variable machines. Their results are useful for
cross-platform shape and large regressions, but they are not a stable
microbenchmark baseline. Use a fixed self-hosted runner and more repetitions
before treating a small percentage change as significant.

[benchmark-script]: https://github.com/Glinte/metapathology/blob/main/scripts/benchmark.py
[benchmark-workflow]: https://github.com/Glinte/metapathology/actions/workflows/benchmark.yml
[deep-benchmark-workflow]: https://github.com/Glinte/metapathology/actions/workflows/deep-benchmark.yml
[reference-run]: https://github.com/Glinte/metapathology/actions/runs/29637641815
[deep-reference-run]: https://github.com/Glinte/metapathology/actions/runs/29638973492
