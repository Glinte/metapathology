# Limitations and resource behavior

`metapathology` never risks changing your program's import behavior to get a
better observation. That priority creates the boundaries below.

## Platform and timing

- Only CPython 3.10 and newer is supported. The implementation relies on the
  CPython [`import` audit event][audit-events] and import-system internals.
- Monitoring starts at `install()`. Modules imported earlier are a baseline;
  nothing before installation is attributed.
- Finders installed by [`.pth` files][site-pth] run before metapathology
  under the normal CLI workflow. They appear in the initial `sys.meta_path`
  snapshot, but there is no stack trace for their insertion. The
  [optional startup bootstrap](usage.md#observe-later-pth-files) can catch
  `.pth` files that sort after it in one site-packages directory on CPython
  3.10–3.14; it cannot see earlier files, other site directories, or `-S`
  startup.

## What is not recorded

- **Cache hits.** An import satisfied from `sys.modules` never reaches the
  finders and produces no event.
- **Import outcomes**, by default. The `import` audit event fires when
  resolution starts; success or failure is unknown unless
  `--deep-import-outcomes` is enabled.
- **Manual loading.** Code using
  [`spec_from_file_location()`][spec-from-file] plus
  [`exec_module()`][exec-module] skips the finder search entirely. The
  resulting modules can only be recognized afterwards as
  [`[no-spec]`](report.md#findings) findings.
- **Some importlib entry points.** `importlib.import_module()` and
  lower-level calls can resolve a module without firing the audit event, so
  a finder call may appear in the report with no matching `import started:`
  event.
- **What happened inside a finder.** Finder wrappers record the target's
  `sys.modules` state before and after each call. A change proves the finder
  boundary altered it, not which nested action did, or whether temporary
  objects existed in between.
- **Loader activity**, unless `--deep-loaders` is enabled and the loader is
  an instrumentable instance reached after activation. Standard class-based
  loaders and legacy `load_module()` calls are never observed.
- **Re-entrant activity.** Imports triggered from inside a monitor hook are
  deliberately not observed (the re-entrancy guard that keeps the monitor
  from breaking imports also blinds it there).
- **C-level list changes.** Mutations made through CPython's `PyList_*` C
  API bypass the instrumented lists.
- **Short-lived cache changes.** `sys.path_importer_cache` is snapshotted at
  install, around `sys.path_hooks` changes, and at report time; between
  snapshots only the dictionary's identity and length are checked. A
  same-size replacement or a clear-and-repopulate entirely between snapshots
  can be missed.
- **List replacement stacks.** `sys.meta_path = [...]` is detected at the
  next import, so the exact assignment stack and assignment-time contents
  are unavailable.

Several finding categories depend on deep options: exact failure correlation
needs `--deep-import-outcomes`, module replacement needs `--deep-loaders`,
and path-hook shadowing needs `--deep-path-hooks`. Without them the report
falls back to weaker, clearly labeled inference.

## The report-time comparison is diagnostic, not a replay

The ["modules found by a custom finder"](report.md#modules-found-by-a-custom-finder)
section calls `PathFinder.find_spec()` at report time. The filesystem, path
hooks, importer cache, and finder state may all have changed since the
import, and the call skips other custom finders. It shows what the standard
search finds *now*, not what would have happened during the run.

That call has the standard library's normal side effect of populating
`sys.path_importer_cache`. One comparison runs per custom-found module, with
no cap, so report time grows with the number of such modules.

Finder protocol inspection (`find_spec` / `find_module` availability) reads
raw class and instance dictionaries only. A protocol reachable only through
`__getattr__` or a descriptor is reported as absent or indeterminate rather
than executed.

## Runtime changes and cleanup

While monitoring, `sys.meta_path` and (by default) `sys.path_hooks` are
`list` subclasses, and instance finders have their `find_spec` shadowed. No
finder is replaced by a proxy, so `isinstance()` and identity checks keep
working.

One known behavior change: when foreign code *replaces* a monitored list,
recovery installs a fresh instrumented list and the replacement list goes
stale. Code that kept a reference to the list it assigned and mutates that
reference later no longer affects the live `sys` attribute.

`uninstall()` restores plain lists (same objects, same order) and removes the
finder shadows. Python cannot unregister an audit hook, so that callback
remains as an inactive no-op.

Use this tool during diagnosis, not as permanent application
instrumentation.

## Memory growth

Every recorded event is kept until the monitor is uninstalled — there is no
event limit and nothing is silently dropped. Approximate costs, measured in
the [benchmarks](performance.md):

- an import start or finder call retains a fraction of a kilobyte to ~1 KB;
- a `sys.meta_path` / `sys.path_hooks` change retains ~1.5 KB, because the
  stack trace is stored;
- deep diagnostics retain ~4 KB per import.

For a long-running or import-heavy process, install immediately before the
behavior of interest, then call `write_report()` and `uninstall()` after
capturing it.

Report rendering copies the retained events into one document, so report
time and JSON size are also proportional to captured activity. Reports
tolerate concurrent imports from other threads; a report produced while
daemon threads are importing may be slightly inconsistent but will not
raise.

[audit-events]: https://docs.python.org/3/library/audit_events.html#audit-events
[site-pth]: https://docs.python.org/3/library/site.html
[spec-from-file]: https://docs.python.org/3/library/importlib.html#importlib.util.spec_from_file_location
[exec-module]: https://docs.python.org/3/library/importlib.html#importlib.abc.Loader.exec_module
