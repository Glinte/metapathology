# How it works

## When Python searches for a module

Every loaded module normally has an entry in [`sys.modules`][sys-modules]
under its fully qualified name. For `import package.child`, for example,
`package` and `package.child` have separate entries and are checked
separately.

When an import requests a name, Python checks `sys.modules` first:

- If the name maps to a module, Python reuses that same module object. It does
  not ask any finders and does not execute the module again.
- If the name maps to `None`, the import stops with `ModuleNotFoundError`.
- If the name is absent, Python searches for and loads the module.

Python puts a new module in `sys.modules` before executing its code. This lets
recursive imports find the partly initialized module instead of loading it a
second time. If loading fails, the import machinery removes the entry it
created. See Python's description of [the module cache][module-cache] for the
full behavior.

This distinction matters when reading a metapathology report: an ordinary
`import` that reuses a cached module never reaches `sys.meta_path`, so there is
no finder call for metapathology to record.

[sys-modules]: https://docs.python.org/3/library/sys.html#sys.modules
[module-cache]: https://docs.python.org/3/reference/import.html#the-module-cache

## The meta path and path hooks

For a name that is not cached, Python asks each finder in
[`sys.meta_path`][sys-meta-path], in order, to return a module spec. Returning
`None` passes the request to the next finder. Returning a spec claims the
module and stops the search.

A normal CPython process includes three class-based finders:

- `BuiltinImporter` finds modules compiled into Python.
- `FrozenImporter` finds modules stored as frozen Python bytecode.
- [`PathFinder`][path-finder] performs the familiar filesystem and installed
  package search.

Other tools can insert finders before, between, or after these entries.

`PathFinder` searches `sys.path` for a top-level module, or a package's
`__path__` for a submodule. Each item on that path needs a *path-entry finder*
that understands the item. Python usually gets one from
[`sys.path_importer_cache`][path-importer-cache]. If the path item is not
cached yet, Python calls the factories in [`sys.path_hooks`][sys-path-hooks]
until one accepts it, then caches the resulting path-entry finder. Python's
standard `FileFinder`, for example, understands directories containing source
files, bytecode, and extension modules.

Path hooks are therefore not callbacks that see every import. They are
factories used by `PathFinder` to create finders for path entries, and those
finders are then cached. If an earlier meta-path finder returns a spec,
`PathFinder` is never called, so neither it nor the path-entry finder created
through `sys.path_hooks` sees that module. This is the bypass that
metapathology is designed to expose. Python's [path-based finder
documentation][path-based-finder] describes the complete protocol.

[sys-meta-path]: https://docs.python.org/3/library/sys.html#sys.meta_path
[path-finder]: https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder
[path-importer-cache]: https://docs.python.org/3/library/sys.html#sys.path_importer_cache
[sys-path-hooks]: https://docs.python.org/3/library/sys.html#sys.path_hooks
[path-based-finder]: https://docs.python.org/3/reference/import.html#the-path-based-finder

## What metapathology records

Metapathology uses three mechanisms because Python exposes different parts of
the import process in different places.

### Imports and import-list reassignment

A CPython [`sys.addaudithook()`][audit-hook] callback observes the documented
[`import` audit event][audit-events]. The event says that an uncached import is
starting; it does not say which finder will claim the module.

Most changes to `sys.meta_path` or `sys.path_hooks` mutate the existing list and are recorded
immediately as described below. Direct assignment, such as
`sys.meta_path = new_list` or `sys.path_hooks = new_list`, discards the
instrumented list and cannot be intercepted at
the assignment itself. On the next import audit event, metapathology notices
the different list object, records the old and new contents, and installs
mutation recording around the new list. The reported stack belongs to that
later import, not necessarily to the code that performed the assignment.

Recovery is a copy-and-swap: a plain list cannot be instrumented in place, so
metapathology puts a new instrumented list into `sys.meta_path` and leaves the
assigned list untouched. Code that keeps a reference to the list it assigned
and mutates that reference afterwards is editing a stale list the import
system no longer consults; make further changes through the live `sys`
attribute itself.

[audit-hook]: https://docs.python.org/3/library/sys.html#sys.addaudithook
[audit-events]: https://docs.python.org/3/library/audit_events.html#audit-events

### Changes to the `sys.meta_path` list

While monitoring is enabled, `sys.meta_path` is a `list` subclass that records
ordinary changes when they happen. This includes adding and removing finders,
replacing items or slices, clearing the list, and changing finder order through
methods such as `insert()`, `remove()`, `reverse()`, and `sort()`. Each record
contains the operation, the added or removed finder names, the resulting list,
the thread, and the caller stack. A newly added finder is also prepared for
finder-call recording.

It remains a real `list` rather than a proxy, so code that iterates, slices, or
checks `isinstance(sys.meta_path, list)` continues to work. The exact supported
operations and known blind spots are listed under [runtime perturbation and
cleanup](limitations.md#runtime-perturbation-and-cleanup).

### Changes to the `sys.path_hooks` list

The default path-hook mechanism installs a separate list subclass with the same
mutation semantics and records safe hook identity/type/name snapshots. It
does not wrap or call hook factories, so identity and membership checks keep
seeing the original hook objects. Use `monitor_path_hooks=False` when even the
temporary list replacement is undesirable.

### Finder calls

Where possible, metapathology places a recording wrapper around a finder's
[`find_spec` method][find-spec]. The wrapper calls the original method,
records whether it returned `None` or a spec, and returns the result unchanged.
The finder object itself is never replaced, preserving identity and
`isinstance()` checks made by third-party code.

CPython's `BuiltinImporter`, `FrozenImporter`, and `PathFinder` entries are
classes shared by the interpreter, not finder instances that can be safely
wrapped. The report identifies these as expected standard finders and explains
their roles. Other class entries and slotted objects are reported separately
when direct finder-call recording is unavailable.

[find-spec]: https://docs.python.org/3/library/importlib.html#importlib.abc.MetaPathFinder.find_spec

## Bypass comparison

For a loaded `.py` or `.pyc` module claimed by a finder other than
`PathFinder`, the report asks `PathFinder.find_spec()` what the normal
path-based machinery would find using the search path captured during the
original call. A missing result, or a different loader or origin, shows that
the actual import did not take the usual `PathFinder` route.

This is evidence about import routing, not automatically a defect. Continue
with [Reading the report](report.md) for the finding categories and their
caveats.
