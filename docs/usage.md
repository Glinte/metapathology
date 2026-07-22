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

Prefer `python -m metapathology` when interpreter selection matters: it
guarantees the monitor is installed in the same interpreter and virtual
environment as the target, while the shorter command uses whichever
installation appears first on `PATH`.

Exit behavior: the target's integer `SystemExit` status is preserved, and an
unhandled exception prints its traceback and exits with status 1; the report
is written to standard error in both cases. A nonexistent script path is a
CLI error (exit status 2, no report).

## Start a monitored interactive interpreter

With no target, the CLI installs the monitor and drops into a standard
interactive interpreter, like `python` itself:

```console
python -m metapathology
```

Type imports at the prompt and inspect what happened as you go — the
`metapathology` module is preloaded, so
`print(metapathology.render_report())` shows the report mid-session. The
usual report is also written when the session ends (Ctrl-D, Ctrl-Z, or
`exit()`). All monitoring options work in this mode:

```console
python -m metapathology --deep
python -m metapathology --report session.json
```

This is a `code.interact()` console, not the full 3.13+ REPL: tab completion
is available where `readline` exists, but multiline editing and syntax
highlighting are not.

## Options and report files

Put metapathology options before the script or `-m` target; everything after
the target is passed to the target.

```console
python -m metapathology --report diagnostics.json path/to/script.py
python -m metapathology --report diagnostics.txt --report diagnostics.json -m package.module
python -m metapathology --report-text - --report-json diagnostics.data path/to/script.py
python -m metapathology --color always path/to/script.py
python -m metapathology --no-path-hook-monitoring path/to/script.py
python -m metapathology --no-importer-cache-monitoring path/to/script.py
python -m metapathology --sys-path-monitoring path/to/script.py
python -m metapathology --deep path/to/script.py
```

- `--report PATH` infers JSON from `.json` and text from `.txt` or `.text`.
  Use `--report-text PATH` or `--report-json PATH` to force a format for any
  other filename. All three options are repeatable, and `-` means standard
  error. With no report option, one text report is written to standard error.
  Files are written atomically and their parent directories must exist.
- Report filenames automatically include the process ID so concurrent
  workers do not overwrite each other: `diagnostics.json` becomes
  `diagnostics.1234.json`. Put `{pid}` in the path to control its position.
- `--color auto` (the default) uses ANSI colors only on a TTY, and never
  when `NO_COLOR` is set or `TERM=dumb`. `--color always` and
  `--color never` override. Color never changes report meaning.
- `--no-path-hook-monitoring` leaves the `sys.path_hooks` list object
  untouched; `--no-importer-cache-monitoring` skips
  `sys.path_importer_cache` snapshots. Both are on by default and neither
  ever replaces the cache dictionary.
- `--sys-path-monitoring` records every ordinary `sys.path` list mutation
  with its caller stack and detects direct reassignment at the next import.
  It is off by default and restores a plain list during cleanup.

### Environment variables

Every option has an environment variable, for situations where you cannot
pass CLI flags (frozen apps, the startup bootstrap below). Explicit CLI or
API values win over the environment; the environment wins over defaults.

```console
METAPATHOLOGY_REPORT=diagnostics-{pid}.txt;diagnostics-{pid}.json
METAPATHOLOGY_COLOR=auto                  # or: always, never
METAPATHOLOGY_MONITOR_PATH_HOOKS=true
METAPATHOLOGY_MONITOR_IMPORTER_CACHE=true
METAPATHOLOGY_MONITOR_SYS_PATH=false
METAPATHOLOGY_DEEP=false
```

`METAPATHOLOGY_REPORT` accepts an `os.pathsep`-separated destination list
(`;` on Windows and `:` on POSIX), using the same extension inference as
`--report`. An unrecognized environment destination falls back to text and is
recorded as a configuration issue so ambient configuration cannot break the
host program. Explicit CLI and API destinations with unknown extensions are
errors; use a format-specific option or argument for those paths.

Boolean variables accept `1/0`, `true/false`, `yes/no`, and `on/off`. These
variables configure a monitor that something installs; they never cause
metapathology to install itself (except `METAPATHOLOGY_EARLY_BOOTSTRAP`,
below).

Reports include command lines, filesystem paths, and stack file names —
treat them as potentially sensitive.

### Deep diagnostics

Deep diagnostics record what the default mode cannot: path hook calls, path
entry finder calls, loader `create_module`/`exec_module` calls, exact import
outcomes, and `builtins.__import__` calls (including cache hits). They are off
by default because they place monitor code inline with foreign imports, and
wrapping a path hook changes its callable identity. Use them in a controlled
reproduction after the default evidence proves insufficient; the report warns
whenever any are active.

`--deep` enables all five delegated boundaries plus `sys.path` mutation
monitoring. Individual switches (`--deep-path-hooks`,
`--deep-path-entry-finders`, `--deep-loaders`, `--deep-import-outcomes`,
`--deep-import-calls`, each with a `--no-` form, and matching
`METAPATHOLOGY_DEEP_*` variables) override it per mechanism.

Notes on individual mechanisms:

- `--deep-loaders` observes existing `create_module` and `exec_module`
  methods only; it never adds missing methods or wraps legacy
  `load_module`. It can catch a loader that replaces a module object.
- `--deep-import-outcomes` records whether each import actually loaded or
  failed, and captures real `PathFinder` results, on CPython 3.10–3.14. It
  uses a profiler slot, so it is refused (without breaking anything) when
  another profiler is already installed. It covers the installing thread and
  threads created later through `threading`; the report states the achieved
  coverage.
- `--deep-import-calls` wraps `builtins.__import__` to record every `import`
  statement, including the `sys.modules` cache hits that leave no other trace
  (no `import` audit event fires and no finder is called). Each record carries
  the imported name, `fromlist`, relative-import level, and the importing
  module, so it answers "which code imported X, and when" even for modules that
  were already loaded. `importlib.import_module()` and lower-level importlib
  entry points bypass `__import__` and are not observed by this mechanism. The
  swap is chain-safe: if another tool already wrapped `__import__`, metapathology
  delegates to it and restores it untouched on uninstall.

## Speculative replay of a displaced cache finder

`--speculative-replay` (env `METAPATHOLOGY_SPECULATIVE_REPLAY`,
`install(speculative_replay=True)`) targets one specific contention shape: a
`sys.path_importer_cache` change removed or replaced the finder for a path
entry, and a later import that traversed that path failed. At report time — and
only then — the tool asks the *retained* displaced finder whether it returns a
spec for the failed module now. This is the beartype#599 shape: a source-file
finder displaces a frozen or archive finder for the same path entry, and the
frozen module can no longer be found.

It is deliberately **not** part of `--deep`: deep capture delegates along the
paths the target actually took, while this replays a path the target did not.
Selection is driven entirely by captured evidence (it needs importer-cache
monitoring and deep path-entry finder capture), it performs at most one foreign
`find_spec()` call per selected candidate, and the whole report is capped at 16
probes. It never touches `sys.path_hooks`, `sys.path_importer_cache`, or
`sys.modules`; a lookup that carried a reload target is declined rather than
answered with a different question. The report states only that the displaced
finder *currently* returns (or does not return) a spec — never that the original
import would have succeeded, since a returned spec does not prove loader success
and current state is not historical state. Because it is recomputed each report,
repeated reports repeat the foreign finder calls.

## Observe later `.pth` files

Normal monitoring starts after Python has already processed the `.pth` files
in site-packages — which is exactly how some import hooks (for example
scikit-build-core's editable-install finder) are installed. To observe those,
an optional bootstrap moves monitoring into interpreter startup on CPython
3.10–3.14:

```console
python -m metapathology.site_bootstrap install
```

Run that with the same interpreter/venv as the target. It creates
`00_metapathology_early.pth` in that interpreter's site-packages. The file
does nothing unless the activation variable is set:

```console
METAPATHOLOGY_EARLY_BOOTSTRAP=1 \
METAPATHOLOGY_REPORT=diagnostics.json \
python path/to/script.py
```

(PowerShell: `$env:METAPATHOLOGY_EARLY_BOOTSTRAP = "1"`.) The usual report
variables apply, including PID-safe filenames. Both variables are inherited
by child processes, which activate themselves and write their own reports.

Inspect or remove the file with:

```console
python -m metapathology.site_bootstrap status
python -m metapathology.site_bootstrap remove
```

All three commands accept `--site-packages DIR` and are idempotent. The
generated file carries an ownership header, and the manager refuses to touch
a file it does not own. Installing the metapathology package normally never
creates this file.

What it can and cannot see: Python processes `.pth` files in one directory
in filename order, so the bootstrap (named `00_...`) observes files sorted
after it in its own directory — not earlier names, not site directories
Python processed first, and not startup under `-S`. The report lists the
`.pth` names that ran before it so the boundary stays visible. Python 3.15
deprecates executable `.pth` lines, so the command rejects 3.15 and newer.

## Install from code

Install the process-wide monitor before the imports under investigation:

```python
import metapathology

monitor = metapathology.install()
```

Installation is idempotent. By default an exit callback writes the report to
standard error. For a bounded capture region with explicit report timing:

```python
import sys

import metapathology

with metapathology.monitoring() as monitor:
    import package_under_investigation

metapathology.write_report(sys.stdout, color="auto")
```

`monitoring()` restores the import machinery even when the block raises and
keeps recorded events available from `monitor.events()`. Nested and overlapping
regions share the process-wide monitor; cleanup occurs after the last region
exits. If monitoring was installed manually before the first region,
`monitoring()` emits a `RuntimeWarning` and leaves it active afterward.
Mechanisms enabled by an inner region remain active for the rest of the shared
installation.

For a lifetime that is not naturally lexical, use `install()` and
`uninstall()`. `uninstall()` is idempotent. It restores plain `sys.meta_path`
and `sys.path_hooks` lists, removes finder wrappers, and unregisters the exit
callback.

The [library API reference](api.md) documents the complete lifecycle and
event types.

## Integrate with another diagnostic

Use `metapathology.render_report(format="json")` when a harness needs a
machine-readable report string, or inspect the [structured event
records](api.md#event-records) returned by `monitor.events()` (a snapshot;
changing it does not alter the monitor). `render_report(color=True)` returns
ANSI-styled text; the default is plain because a returned string has no
destination to auto-detect.

Calling `write_report()` or `render_report()` before the first `install()`
raises `RuntimeError`. See [Reading the report](report.md) for
interpretation.

[runpy]: https://docs.python.org/3/library/runpy.html
[main-metadata]: https://docs.python.org/3/reference/import.html#special-considerations-for-main
