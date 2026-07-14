# Using metapathology

The CLI is the primary interface. Use the [library API](api.md) only when the
target cannot be wrapped, such as a notebook, embedded interpreter, or a
project where only `conftest.py` can be changed.

## Run a script

```console
python -m metapathology path/to/script.py argument-one argument-two
```

The script sees its path in `sys.argv[0]`, the remaining arguments in
`sys.argv[1:]`, and its directory at the front of `sys.path`, as it would under
a direct Python invocation.

## Run a module

```console
python -m metapathology -m package.module argument-one
```

The module runs as `__main__`. As with tools such as `coverage` and `cProfile`,
execution through Python's [`runpy` module][runpy] has some differences from a
direct invocation, including [`__main__` import metadata][main-metadata] and
Windows multiprocessing re-import behavior.

## Use the shorter command

Installation also provides a `metapathology` executable with the same script
and module modes:

```console
metapathology path/to/script.py argument-one
metapathology -m package.module argument-one
```

Prefer `python -m metapathology` when interpreter selection matters. The `-m`
form guarantees that the monitor is installed in the same interpreter and
virtual environment as the target, while the shorter command uses whichever
installation appears first on the shell's command search path.

The target's integer `SystemExit` status is preserved. An unhandled exception
prints its traceback and produces exit status 1. The diagnostic report is
still written to standard error in both cases.

[runpy]: https://docs.python.org/3/library/runpy.html
[main-metadata]: https://docs.python.org/3/reference/import.html#special-considerations-for-main

## Install from code

Install the process-wide monitor before the imports under investigation:

```python
import metapathology

monitor = metapathology.install()
```

Installation is idempotent. The [library API reference](api.md) documents the
complete lifecycle and event types. By default an exit callback writes the
report to standard error. For explicit control over timing and destination:

```python
import sys

import metapathology

monitor = metapathology.install(report_at_exit=False)
try:
    import package_under_investigation
finally:
    metapathology.report(sys.stdout)
    metapathology.uninstall()
```

Uninstallation is also idempotent. It restores a plain `sys.meta_path`, removes
finder method shadows, and unregisters the exit callback. Recorded events
remain available from `monitor.events()`.

## Integrate with another diagnostic

Use `metapathology.render_report()` when a harness needs the report as a
string, or inspect the [structured event records](api.md#event-records)
returned by `monitor.events()`. The returned event list is a snapshot;
changing it does not alter the monitor.

Calling `report()` or `render_report()` before the first `install()` raises
`RuntimeError`. See the [Library API](api.md) for the complete public surface
and [Reading the report](report.md) for interpretation.
