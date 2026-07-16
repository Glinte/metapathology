# Limitations and resource behavior

`metapathology` prioritizes preserving the target program's import outcome.
That creates deliberate observation boundaries.

## Platform and timing

- Only CPython 3.10 and newer is supported. The implementation relies on the
  CPython [`import` audit event][audit-events] and import-system internals.
- Monitoring starts at `install()`. Existing modules are treated as a
  baseline rather than attributed retrospectively.
- Under the normal CLI/API workflow, finders installed by [`.pth`
  files][site-pth] are present before metapathology runs. They appear in the
  initial snapshot, but their insertion has no mutation stack trace.
- The optional [early-site bootstrap](usage.md#observe-later-pth-files) can
  observe later `.pth` files in one selected directory on CPython 3.10--3.14.
  It cannot observe lexically earlier files, site directories Python processed
  first, `-S` startup, or configurations that disable ordinary site
  processing. Python 3.15 deprecates its executable `.pth` mechanism, so the
  manager rejects that version and newer.
- When early-site activation is explicitly enabled, its environment and
  report configuration are inherited by child processes. This is not a
  collector: each child independently activates and writes a PID-safe report.

## Events that are not visible

The retained `ImportAuditStart` occurs before resolution and does not identify
the winner or prove success or failure. It also does not fire for `sys.modules`
cache hits. Manual loading such
as [`spec_from_file_location()`][spec-from-file] followed by
[`exec_module()`][exec-module] can avoid both the normal meta-path search and
its audit coverage; some resulting modules can only be recognized post hoc as
[`[no-spec]`](report.md#suspicious-findings).
Direct use of lower-level importlib entry points, including
`importlib.import_module()`, can also perform resolution without crossing the
builtin import audit boundary on supported CPython versions. Finder wrappers
may still record instrumented custom finders, but there will be no corresponding
`ImportAuditStart`.

Finder wrappers capture only the target name's state before and after the
outer call. A delta proves a boundary transition, not which nested import
caused it or whether temporary intermediate objects existed. Deep loader
states require `--deep-loaders` and an instrumentable mutable loader reached
after activation. Standard class loaders, legacy `load_module()` calls, and
earlier loader activity remain outside that evidence. A non-dictionary
`sys.modules` replacement is reported unavailable rather than invoked through
foreign mapping methods.

The loader inventory observes only modules still present at report time and
metadata they retain then. It cannot prove which loader originally executed a
module, recover removed modules, or distinguish metadata replaced after load.
Real module dictionaries are read without subclass dispatch so lazy modules
are not materialized; arbitrary module-like cache values are labeled
unavailable instead of probed dynamically.

Default standard-finder attribution inherits those post-hoc limitations and
is always labeled inferred. It also requires an import audit start; cache hits,
manual loader calls, and import entry points that do not emit that event cannot
be reconstructed from inventory alone. Exact aggregate `PathFinder` evidence
requires deep import outcomes, an unused profiling slot, and a discoverable
Python `PathFinder.find_spec` code object on the running CPython. The report
states when either prerequisite is unavailable and falls back to inference.

[audit-events]: https://docs.python.org/3/library/audit_events.html#audit-events
[site-pth]: https://docs.python.org/3/library/site.html
[spec-from-file]: https://docs.python.org/3/library/importlib.html#importlib.util.spec_from_file_location
[exec-module]: https://docs.python.org/3/library/importlib.html#importlib.abc.Loader.exec_module

Wholesale `sys.meta_path` and `sys.path_hooks` replacements are discovered at
the next import. Their exact assignment stacks and assignment-time contents
are unavailable.

Importer-cache observation is passive. Full snapshots occur at installation,
around observed path-hook list mutations, and at report time. The import audit
hook checks only cache identity and length, so a same-size replacement or a
clear-and-repopulate sequence entirely between full observations may be
visible only in the final state or missed altogether. Non-string keys are
counted but omitted without inspection.

## Replay is diagnostic

Bypass detection replays `PathFinder` at report time. It uses the search path
captured with the original finder call, but the filesystem and other import
state may have changed. The accompanying historical structural comparison is
also bounded: it compares install and report snapshots plus passive cache-diff
events, not a reconstructed import-time cache. Historical finder objects are
identified but never called. A difference is a reason to investigate, not
proof of a bug. Source bypass findings still require `.py` or `.pyc` origins,
while package, namespace, and other spec-semantic comparisons require both
claims to expose comparable safe fields.
Calling `PathFinder.find_spec()` can populate
`sys.path_importer_cache`; replay therefore has that standard-library side
effect even though metapathology suppresses its own event recording during
report analysis.

Finder-contract auditing intentionally ignores protocols available only
through `__getattr__`, custom `__getattribute__`, or a descriptor that must be
bound. Such a protocol is reported as absent or indeterminate from raw
dictionary evidence rather than executed during observation. Entries first
seen after direct `sys.meta_path` reassignment have a reassignment observation
boundary but no exact insertion event or insertion stack.

## Runtime perturbation and cleanup

The monitor temporarily installs `list` subclasses for `sys.meta_path` and,
by default, `sys.path_hooks`, and shadows instance
`find_spec` methods where safe. It does not replace finders with proxies,
return specs, or load modules. The list records `append`, `insert`, `extend`,
`remove`, `pop`, `clear`, `reverse`, `sort`, item and slice assignment or
deletion, `+=`, and `*=`. Mutations performed directly through CPython's
`PyList_*` C API do not call these Python overrides and cannot be recorded.

Replacing either monitored list also bypasses those overrides. The
next uncached import detects that replacement and installs a new instrumented
list, as described under [imports and `sys.meta_path`
reassignment](concepts.md#imports-and-import-list-reassignment). That
recovery is a copy-and-swap: the replacement list assigned by the other code
is left untouched and goes stale, so a caller that kept a reference to it and
mutates it later no longer affects the corresponding live `sys` attribute.
This is the known case where monitoring changes the behavior of code that was
otherwise working. Path-hook monitoring can be disabled independently.
Importer-cache monitoring can also be disabled independently and never
replaces or subclasses `sys.path_importer_cache`.

`uninstall()` restores plain lists preserving the live objects and ordering,
and reverses finder changes. Python cannot unregister
an audit hook, so its callback remains as a cheap inactive no-op.

Use this tool during diagnosis rather than as permanent application
instrumentation.

## Memory growth

The monitor retains every import-audit start, finder call, mutation,
reassignment, importer-cache diff, and internal error to keep the report
exhaustive. Memory use grows with import activity for as long as monitoring
remains enabled. There is no event limit and no silent dropped-record policy.
Each successful finder call also retains its plain spec summary, including all
exact string locations from list or tuple package paths. Foreign location
sequences are not retained or iterated in the import hot path.

One small slotted audit-start record is retained for each observed builtin
import resolution. It copies the current meta-path finder type names; auxiliary
path-hook and cache evidence is constant-size. Re-entrant imports that occur
while a finder wrapper or hook is already active deliberately remain
unobserved under the mandatory re-entrancy guard.

Meta-path and path-hooks mutation and reassignment records are more expensive because they retain stack
summaries. In a long-running or import-heavy process, install immediately
before the behavior of interest, then call `write_report()` and `uninstall()` after
capturing it. See [speed and memory use](performance.md) for the cost model and
reproducible benchmarks, and the [library API](api.md) for lifecycle details.

Importer-cache storage retains two full plain-data maps: the install snapshot
and a rolling latest snapshot that is replaced on every successful
observation. Diff events are retained without a limit. Concurrent full
observation requests are coalesced rather than queued, and reports expose the
coalesced count. Strong references to unique observed cached finders remain
live until uninstall so object IDs cannot be reused during one capture.

Rendering temporarily copies the retained events and relevant interpreter
state into one cutoff-based document. JSON file size and peak report-time
memory are therefore also proportional to captured activity. Automatic file
reporting makes one synchronous write attempt and does not queue, aggregate,
or retry records.

Reporting copies mutable interpreter collections and tolerates concurrent
changes, so a report produced while daemon threads are importing may be
slightly inconsistent. Observation and cleanup failures are isolated and
recorded where possible instead of changing import outcomes.
