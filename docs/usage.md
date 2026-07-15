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
still written to standard error in both cases. A nonexistent script path is a
CLI error instead: it exits with status 2 before monitoring starts and does not
produce a report. Module resolution remains monitored because it exercises the
import machinery itself.

## Write a file report

Put metapathology options before the script or `-m` target:

```console
python -m metapathology --report diagnostics.json path/to/script.py
python -m metapathology --report diagnostics.txt --report-format text -m package.module
python -m metapathology --no-path-hook-monitoring path/to/script.py
python -m metapathology --no-importer-cache-monitoring path/to/script.py
```

Automatic file destinations are process-safe. `{pid}` is replaced when it is
present; otherwise the PID is inserted before the final suffix. For example,
`diagnostics.json` becomes `diagnostics.1234.json`. The parent directory must
already exist. Each process writes one selected format, without a collector,
background worker, or retry loop.

Path-hook monitoring is enabled by default. The disable option leaves the
exact `sys.path_hooks` list object untouched; options after the target are
passed through to the target rather than parsed by metapathology.
Importer-cache monitoring is also enabled by default. Its disable option
skips passive cache snapshots and diffs; metapathology never replaces the
cache dictionary in either mode.

For frozen or embedded bootstrap code, configure the same behavior before
calling `install()`:

```console
METAPATHOLOGY_REPORT=diagnostics-{pid}.json
METAPATHOLOGY_REPORT_FORMAT=json
```

Environment variables configure an installed monitor; they do not cause
metapathology to import or install itself. Explicit API or CLI values take
precedence. Reports include argv, origins, filesystem paths, and stack
filenames, so treat them as potentially sensitive diagnostic artifacts.

## Observe later `.pth` files

The ordinary wrapper begins after Python has processed executable `.pth`
lines. In a disposable or explicitly selected diagnostic environment, an
opt-in bootstrap can move monitoring into site initialization on CPython
3.10--3.14:

```console
python -m metapathology.site_bootstrap install
```

Run that command with the same interpreter/venv as the target. It creates
`00_metapathology_early.pth` in that interpreter's `purelib` directory. The
file is inert unless the exact activation value is present:

```console
METAPATHOLOGY_EARLY_BOOTSTRAP=1 \
METAPATHOLOGY_REPORT=diagnostics.json \
python path/to/script.py
```

PowerShell uses `$env:NAME = "value"` to set the two variables. The normal
report-format rules still apply, including PID-safe automatic filenames. Both
variables are inherited by child processes, so a child using the same
environment activates itself and writes its own report without injection by
the parent.

Inspect or remove the generated file with symmetric commands:

```console
python -m metapathology.site_bootstrap status
python -m metapathology.site_bootstrap remove
```

All three commands accept `--site-packages DIR`. Installation and removal are
idempotent. The generated header carries an ownership token; the manager
refuses to overwrite or remove a foreign file at the selected path and can
repair or remove a truncated file that still has a valid ownership header.
Ordinary package installation never creates the bootstrap.

Within one site-packages directory, Python processes `.pth` names in lexical
order. The bootstrap observes later names in that directory, but not earlier
names or files in a site directory Python processed first. Reports record the
selected bootstrap and the lexically earlier `.pth` names in its directory so
the evidence boundary remains visible. `-S`, disabled site processing, and
some isolated or embedded configurations skip the bootstrap. The command
deliberately rejects Python 3.15 and newer because executable `.pth` lines are
deprecated there; the newer `.start` mechanism runs too late to observe `.pth`
execution.

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
    metapathology.write_report(sys.stdout)
    metapathology.uninstall()
```

Uninstallation is also idempotent. It restores plain `sys.meta_path` and
`sys.path_hooks` lists, removes finder method shadows, and unregisters the exit callback. Recorded events
remain available from `monitor.events()`.

## Integrate with another diagnostic

Use `metapathology.render_report(format="json")` when a harness needs a
machine-readable report string, or inspect the [structured event
records](api.md#event-records)
returned by `monitor.events()`. The returned event list is a snapshot;
changing it does not alter the monitor.

Calling `write_report()` or `render_report()` before the first `install()` raises
`RuntimeError`. See the [Library API](api.md) for the complete public surface
and [Reading the report](report.md) for interpretation.
