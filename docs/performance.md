# Speed and memory

Metapathology is intended for a focused reproduction, not permanent production
instrumentation. Cost depends on how many imports occur, how many custom
finders see each import, which detailed capture options are active, and how
often import state changes.

## Reference results

These measurements come from the linked
[default benchmark run][reference-run] and
[detailed-capture run][detailed-reference-run] on GitHub-hosted Linux,
Windows, and macOS runners using CPython 3.10 and 3.14. Each environment used
five fresh-process timing samples and three memory samples per point. The table
shows the range across the six environments and their median.

At 5,000 operations, default capture produced:

| Measure | Range | Median |
| --- | ---: | ---: |
| Monitor installation | 0.107–0.671 ms | 0.207 ms |
| Standard-only imports, monitored/control time | 0.96–1.19× | 1.10× |
| Imports seen by a custom finder, monitored/control time | 1.18–1.65× | 1.35× |
| Retained data per standard-only import | 335–349 bytes | 342 bytes |
| Retained data per attributed import | 934–1,014 bytes | 974 bytes |
| Monitored `pop`/`append` pair | 21.8–42.3 µs | 39.9 µs |
| Retained data per `pop`/`append` pair | 1.31–1.63 KiB | 1.47 KiB |
| Standard-only JSON report rendering | 0.163–0.775 s | 0.355 s |
| Attributed JSON report rendering | 0.564–3.033 s | 1.375 s |

At 1,000 imports, capture with every detailed mechanism enabled produced:

| Measure | Range | Median |
| --- | ---: | ---: |
| Monitored/control import time | 7.06–15.34× | 13.08× |
| Total monitored import time | 0.72–2.17 s | 1.40 s |
| Retained data per import | 3.65–4.38 KiB | 4.01 KiB |
| JSON report rendering | 2.69–32.79 s | 11.24 s |
| JSON report peak allocation | 24.94–51.46 MiB | 38.19 MiB |
| Rendered JSON size | 6.98–7.22 MiB | 7.19 MiB |

Detailed report rendering differed sharply by Python version in this run:
Python 3.14 took 2.69–4.69 seconds, while Python 3.10 took 17.79–32.79
seconds.

These are synthetic measurements on shared hosted runners, not performance
guarantees. They are useful for scale: default capture usually adds modest
import time but retains data for every observed import; full detailed capture
is several times slower and can produce multi-megabyte reports quickly.

## Where the cost comes from

For an uncached import, default capture records the import-search start. Each
writable custom finder instance also records one call, whether it finds the
module or declines it. Several custom finders can therefore produce several
records for one import.

Changes to `sys.meta_path`, `sys.path_hooks`, and opt-in `sys.path` cost more
because metapathology records a stack. Importer-cache snapshots scan the cache
at installation, around observed path-hook changes, and when the report is
built; the audit hook itself reads only the cache identity and size.

Detailed capture places Python wrappers around the selected path-hook,
path-entry-finder, loader, or `__import__` calls. A single import can cross
several of these points, so detailed event counts can be much larger than the
number of imported modules.

Every event is retained until the process ends or the monitor is uninstalled.
Nothing is silently discarded. Importer-cache snapshot storage keeps the
installation snapshot and latest snapshot, while cache-change events remain in
the event log.

## Keep the capture short

For a long-running process, monitor only the operation under investigation:

```python
import metapathology

with metapathology.monitoring():
    reproduce_problem()

metapathology.write_report("diagnosis.json", format="json")
```

Writing a report does not clear events. Use a fresh process for an independent
capture.

## Reproduce the benchmark

The benchmark runs control and monitored workloads in fresh processes and
writes raw JSON, a Markdown summary, and graphs:

```console
uv run --script scripts/benchmark.py
```

Default scenarios:

- `native`: imports resolved entirely by controlled standard machinery;
- `attributed`: the same import passes through a delegating custom finder; and
- `mutation`: repeated `sys.meta_path` `pop`/`append` pairs, including stack
  capture.

Add the full detailed-capture scenario with:

```console
uv run --script scripts/benchmark.py --include-detailed
```

Timing and memory trials are separate so `tracemalloc` does not distort timing.
Trial order is shuffled from a recorded seed. `benchmark.json` records every
sample, interpreter and platform details, Git revision, event counts, retained
allocations, peak traced allocations, and sampled peak RSS growth.

The [benchmark script][benchmark-script] and
[benchmark workflow][benchmark-workflow] are the authoritative methodology.
The [detailed benchmark workflow][detailed-benchmark-workflow] keeps the
expensive all-mechanisms scenario out of the normal regression matrix.
Use a fixed machine and more repetitions before treating a small difference as
a regression.

[benchmark-script]: https://github.com/Glinte/metapathology/blob/main/scripts/benchmark.py
[benchmark-workflow]: https://github.com/Glinte/metapathology/actions/workflows/benchmark.yml
[detailed-benchmark-workflow]: https://github.com/Glinte/metapathology/actions/workflows/deep-benchmark.yml
[reference-run]: https://github.com/Glinte/metapathology/actions/runs/29637641815
[detailed-reference-run]: https://github.com/Glinte/metapathology/actions/runs/29638973492
