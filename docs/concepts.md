# Mental model

You do not need to know Python's full import implementation to use
metapathology. Three ideas are enough.

## Meta-path finders run in order

For an uncached import, Python asks entries in
[`sys.meta_path`](https://docs.python.org/3/library/sys.html#sys.meta_path) for
a [module spec](https://docs.python.org/3/library/importlib.html#importlib.machinery.ModuleSpec).
The spec says how the module will be loaded and where it came from. The first
finder that returns one wins; later finders do not see that search.

A tool can therefore work alone and fail when another finder is inserted ahead
of it.

## Path hooks are a second layer

[`PathFinder`](https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder),
normally the last meta-path finder, searches paths using
[`sys.path_hooks`](https://docs.python.org/3/library/sys.html#sys.path_hooks)
and
[`sys.path_importer_cache`](https://docs.python.org/3/library/sys.html#sys.path_importer_cache).
A custom meta-path finder can bypass this machinery entirely. Two path hooks
can also accept the same path, while the importer cache keeps only the selected
finder.

## The module cache can bypass all finders

Python checks
[`sys.modules`](https://docs.python.org/3/reference/import.html#the-module-cache)
before starting a new search. If the module is already present, no finder runs.
This is why default capture cannot see ordinary cache hits and why
`--capture-import-calls` exists.

## Observation and checking happen at different times

Metapathology records import activity while the program runs. When a report is
built, it can also run checks against current interpreter state.

```text
program run                 report construction
-----------                 -------------------
observed finder result  --> standard-path check
cache change            --> displaced-finder check (opt-in)
```

Observed evidence says what happened. A check says what the current state
returns. Neither check predicts an alternative historical winner.

For exact coverage and blind spots, continue with
[Choosing capture](capture.md) and [Limitations](limitations.md).
