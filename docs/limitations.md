# Limitations and resource behavior

`metapathology` prioritizes preserving the target program's import outcome.
That creates deliberate observation boundaries.

## Platform and timing

- Only CPython 3.10 and newer is supported. The implementation relies on the
  CPython [`import` audit event][audit-events] and import-system internals.
- Monitoring starts at `install()`. Existing modules are treated as a
  baseline rather than attributed retrospectively.
- Finders installed by [`.pth` files][site-pth] are present before any
  metapathology code can run, including the CLI. They appear in the initial
  `sys.meta_path` snapshot and are instrumented from then on, but their
  insertion cannot have a mutation stack trace.
- Automatic `.pth` injection into child processes is out of scope. Instrument
  each process explicitly when subprocess coverage is needed.

## Events that are not visible

The `import` audit event occurs before resolution and does not identify the
winner. It also does not fire for `sys.modules` cache hits. Manual loading such
as [`spec_from_file_location()`][spec-from-file] followed by
[`exec_module()`][exec-module] can avoid both the normal meta-path search and
its audit coverage; some resulting modules can only be recognized post hoc as
[`[no-spec]`](report.md#suspicious-findings).

[audit-events]: https://docs.python.org/3/library/audit_events.html#audit-events
[site-pth]: https://docs.python.org/3/library/site.html
[spec-from-file]: https://docs.python.org/3/library/importlib.html#importlib.util.spec_from_file_location
[exec-module]: https://docs.python.org/3/library/importlib.html#importlib.abc.Loader.exec_module

Wholesale `sys.meta_path` and `sys.path_hooks` replacements are discovered at
the next import. Their exact assignment stacks and assignment-time contents
are unavailable.

## Replay is diagnostic

Bypass detection replays `PathFinder` at report time. It uses the search path
captured with the original finder call, but the filesystem and other import
state may have changed. A difference is a reason to investigate, not proof of
a bug. Only `.py` and `.pyc` origins with a usable loader baseline are checked.

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

`uninstall()` restores plain lists preserving the live objects and ordering,
and reverses finder changes. Python cannot unregister
an audit hook, so its callback remains as a cheap inactive no-op.

Use this tool during diagnosis rather than as permanent application
instrumentation.

## Memory growth

The monitor retains every finder call, mutation, reassignment, and internal
error to keep the report exhaustive. Memory use grows with import activity for
as long as monitoring remains enabled. There is no event limit and no silent
dropped-record policy.

Meta-path and path-hooks mutation and reassignment records are more expensive because they retain stack
summaries. In a long-running or import-heavy process, install immediately
before the behavior of interest, then call `write_report()` and `uninstall()` after
capturing it. See [speed and memory use](performance.md) for the cost model and
reproducible benchmarks, and the [library API](api.md) for lifecycle details.

Rendering temporarily copies the retained events and relevant interpreter
state into one cutoff-based document. JSON file size and peak report-time
memory are therefore also proportional to captured activity. Automatic file
reporting makes one synchronous write attempt and does not queue, aggregate,
or retry records.

Reporting copies mutable interpreter collections and tolerates concurrent
changes, so a report produced while daemon threads are importing may be
slightly inconsistent. Observation and cleanup failures are isolated and
recorded where possible instead of changing import outcomes.
