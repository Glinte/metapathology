# metapathology

`metapathology` is a stdlib-only diagnostic tool for
[CPython's import system][python-imports]. It helps answer three questions:

- Which meta-path finder claimed each imported module?
- Where did code mutate or replace `sys.meta_path`?
- Which source modules bypassed the normal `sys.path_hooks` search?

It was created to investigate conflicts where one import customization prevents
another from seeing a module. The monitor observes and delegates: it never
returns a module spec or loads a module itself.

## Start here

Run a script with the same Python interpreter and environment as the target:

```console
python -m metapathology myscript.py --my-args
```

Or run an importable module:

```console
python -m metapathology -m pytest tests/
```

The target runs normally and a diagnostic report is written to standard error
when it finishes. See [Using metapathology](usage.md) for library integration
and lifecycle control.

[python-imports]: https://docs.python.org/3/reference/import.html

## Guide

- [Using metapathology](usage.md) — CLI and library workflows
- [How it works](concepts.md) — module caching, path hooks, and what the monitor records
- [Reading the report](report.md) — sections, finding labels, and investigation order
- [Library API](api.md) — public functions, `Monitor`, and event records
- [Limitations and resource behavior](limitations.md) — observation boundaries and runtime cost
- [Development](development.md) — invariants, checks, and documentation workflow

`metapathology` supports CPython 3.10 and newer and has no runtime
dependencies.
