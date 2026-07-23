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
[`sys.meta_path`][sys-meta-path], in order, to return a module spec by calling
its `find_spec()` method. Returning `None` passes the request to the next
finder. Returning a spec means the finder found the module, and the search
stops.

A [module spec](https://docs.python.org/3/library/importlib.html#importlib.machinery.ModuleSpec)
is a small object describing a found module: its name, its *origin* (usually
the file path), and the *loader* that will execute it. Python stores it as
the module's `__spec__` attribute, which is how the report can tell, after
the fact, how a module was loaded. For a package, the spec also carries the
search locations that become the package's `__path__` — a
[namespace package](https://docs.python.org/3/reference/import.html#namespace-packages)
may list several directories there, and submodule imports search all of
them.

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

Metapathology uses several mechanisms because Python exposes different parts of
the import process in different places.

### Imports and import-list reassignment

A CPython [`sys.addaudithook()`][audit-hook] callback observes the documented
[`import` audit event][audit-events]. For normal import statements and
`__import__()` calls, the event says that Python is starting to search for a module that is not in `sys.modules`; it
does not say which finder will find the module or whether the import will
finish. Metapathology immediately copies the module name,
`sys.meta_path` identity and finder type names, plus constant-size fingerprints
from the enabled path-hook and importer-cache mechanisms, into an
`ImportAuditStart` record. A second audit shape used while loading some native
extensions is not a new resolution start and is not recorded as one.
Calling lower-level importlib entry points such as `importlib.import_module()`
can enter resolution without this builtin audit boundary and therefore creates
no audit-start record.

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
cleanup](limitations.md#runtime-changes-and-cleanup).

### Changes to the `sys.path_hooks` list

The default path-hook mechanism installs a separate list subclass with the same
mutation semantics and records safe hook identity/type/name snapshots. It
does not wrap or call hook factories, so identity and membership checks keep
seeing the original hook objects. Use `CaptureConfig(path_hooks=False)` when even the
temporary list replacement is undesirable.

### Changes to `sys.path_importer_cache`

The cache remains the exact dictionary used by importlib. Metapathology copies
string-keyed entries at installation, before and after observed path-hook
mutations, and at report time, then records additions, removals, finder
replacements, and negative (`None`) entries. It holds observed finders strongly
while enabled so recorded identities cannot be reused mid-capture.

At each import audit event it compares only dictionary identity and length and
marks the rolling snapshot dirty. This keeps per-import work independent of
cache size; a later full observation computes the diff.

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

Before attempting that wrapper, metapathology inventories `find_spec` and the
legacy `find_module` protocol through raw instance and class dictionaries. It
does not bind descriptors, invoke dynamic attribute lookup, or call either
protocol. The first observation of each distinct finder is retained, including
the insertion mutation sequence when one was captured, so instrumentation
cannot make the original contract appear more modern than it was.

When no custom finder found a module, the report can still infer which
standard finder handled it by combining import order with module metadata
read at report time. Such entries are labeled `[inferred]` because metadata
may have changed after loading. The opt-in `--deep-import-outcomes` mechanism
instead records real `PathFinder` results, without shadowing or replacing the
shared class.

[find-spec]: https://docs.python.org/3/library/importlib.html#importlib.abc.MetaPathFinder.find_spec

## The report-time comparison

For a module found by a finder other than `PathFinder`, the report records
the spec that finder returned. It then calls `PathFinder.find_spec()` with
the same name and search path to see what the standard path machinery finds.
The two results are shown side by side in the report.

The fresh `PathFinder` call runs at report time — against current path
hooks, importer cache, filesystem, and finder state — and it skips other
custom meta path finders. A difference between the two results is therefore
evidence to investigate, not proof of what would have happened had the
custom finder been absent. Continue with [Reading the report](report.md) for
how these comparisons are presented and when they become findings.
