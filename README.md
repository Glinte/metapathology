# metapathology

**sys.meta_path diagnostics** — a tiny, stdlib-only debug tool that tells you
*who is messing with Python's import machinery*: which finder claimed each
import, who mutated `sys.meta_path` (and when, with a stack trace), and which
modules were loaded in ways that bypass `sys.path_hooks`.

Built for debugging **import hook contention** — the class of bug where two
libraries (e.g. an editable-install finder and a `sys.path_hooks`-based
instrumenter like `beartype.claw`) both want a say in how a module is loaded,
and one silently loses. See
[beartype#556](https://github.com/beartype/beartype/issues/556) for the
motivating incident.

## Status

First working version: all three layers, CLI, and exit report are implemented
and tested. Published to PyPI.

## Usage

Primary: run your program under observation, no code changes needed —

```console
$ python -m metapathology myscript.py --my-args
$ python -m metapathology -m pytest tests/
```

A report is printed at exit. Prefer `python -m metapathology` over a bare
`metapathology` command: it guarantees the hooks land in the same interpreter
and venv as the code under investigation.

Library API, for when a wrapper isn't possible (notebooks, embedded
interpreters, "I can only touch `conftest.py`"):

```python
import metapathology

metapathology.install()  # as early as possible
```

No dependencies, no configuration required. It's a debug tool: run it, read
the report, remove it.

## How it works

Three layers, escalating in invasiveness, covering each other's blind spots:

1. **Audit hook** (`sys.addaudithook`, passive, irremovable) — snapshots
   `sys.meta_path` on every `import` event; detects wholesale reassignment
   (`sys.meta_path = [...]`), which nothing else can catch.
2. **Instrumented `sys.meta_path`** — a `list` subclass whose mutation methods
   log *who* appended/inserted/removed finders, with stack traces, at mutation
   time. Re-installed automatically if layer 1 sees it blown away.
3. **Finder attribution** — each finder's `find_spec` is shadowed with a
   logging wrapper in its instance dict (so third-party `isinstance` scans of
   `sys.meta_path` still pass), recording exactly which finder claimed each
   module.

The exit report cross-references this log against `sys.modules` and a fresh
`PathFinder.find_spec()` replay to flag modules that a meta-path finder
short-circuited away from the standard `sys.path_hooks` chain.

## Caveats

- CPython only (relies on the `import` audit event and import-system
  internals).
- A debug tool by design: it deliberately perturbs `sys.meta_path` in
  reversible ways. Don't ship it enabled in production.

## Related search terms

sys.meta_path diagnostics, import hook conflict, PEP 302 compliance, meta path
finder debugging, sys.path_hooks bypass, editable install import hook,
who imported this module.
