<h1 align="center">metapathology</h1>

<p align="center">
  Diagnose Python import hooks without changing import outcomes.
</p>

<p align="center">
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/v/metapathology.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/pyversions/metapathology.svg" alt="Supported Python versions"></a>
  <a href="https://github.com/Glinte/metapathology/actions/workflows/test.yml"><img src="https://github.com/Glinte/metapathology/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://glinte.github.io/metapathology/"><img src="https://github.com/Glinte/metapathology/actions/workflows/docs.yml/badge.svg" alt="Documentation"></a>
  <a href="https://sonarcloud.io/summary/new_code?id=Glinte_metapathology"><img src="https://sonarcloud.io/api/project_badges/measure?project=Glinte_metapathology&amp;metric=alert_status" alt="Quality gate status"></a>
</p>

> [!IMPORTANT]
> This project is mostly AI generated (for now). I do understand what it does
> and how it works, but I only skimmed the code.
>
> Slop status:
> - Code is a bit slop but fine
> - README is good
> - the report is genuinely useful
> - docs: usage, reading the report, limitations are good, others are slop

Some Python packages customize how imports work: pytest rewrites test
assertions, editable installs redirect imports to your source tree, beartype
instruments modules as they load. These customizations plug into
[Python's import system](https://docs.python.org/3/reference/import.html)
through `sys.meta_path` and `sys.path_hooks` — and when two of them are active
at once, one can silently prevent the other from ever seeing a module. The
symptom is usually confusing: a feature quietly does nothing, or a module
that clearly exists fails to import.

`metapathology` runs your program and reports what the import system actually
did:

- which finder located each imported module;
- where code changed `sys.meta_path`, `sys.path_hooks`, or
  `sys.path_importer_cache`, with a stack trace; and
- which modules were found without going through the usual `sys.path` search.

It only observes. It never loads a module, returns a spec, or changes which
finder wins, and everything it installs is removed on exit (except a CPython
audit hook, which becomes a no-op because Python cannot unregister it).

Full documentation: [glinte.github.io/metapathology](https://glinte.github.io/metapathology/).

## Usage

Requires CPython 3.10+. No runtime dependencies — it works even in an
environment where other packages fail to import.

```console
$ pip install metapathology
$ python -m metapathology myscript.py --my-args
$ python -m metapathology -m pytest tests/
$ python -m metapathology                      # monitored interactive interpreter
```

Your program runs normally; the report is printed to standard error when it
exits. Use `--report diagnostic.json` to write a JSON file instead. Prefer
`python -m metapathology` over the `metapathology` command so the tool runs in
the same interpreter and virtual environment as your program.

If you cannot wrap the program (a notebook, an embedded interpreter, a
`conftest.py`), call the [library API](https://glinte.github.io/metapathology/api/):

```python
import metapathology

metapathology.install()  # as early as possible
```

See [Using metapathology](https://glinte.github.io/metapathology/usage/) for
all CLI options, report files, environment variables, and lifecycle control.

## Reading the report

A real example: [beartype#556](https://github.com/beartype/beartype/issues/556),
where `beartype.claw` silently did nothing in a scikit-build-core editable
install. The report shows why in its first comparison — the editable-install
finder found the module first, so beartype's path hook never saw it:

```text
== metapathology report ==
target outcome: completed (exit status 0)
verdict: no problems found; some modules were found by a custom finder instead of the standard path search — listed below for review
...
-- modules found by a custom finder (1) --
'myproject':
    during the run: ScikitBuildRedirectingFinder, loader _ScikitBuildLoaderWrapper, origin 'src\myproject\__init__.py'
    standard search at report time: PathFinder, loader BeartypeSourceFileLoader, same origin
    differences: loader type
    note: this finder ran before PathFinder, so the standard path search never saw the module

-- finder calls --
ScikitBuildRedirectingFinder: called 365 times, found 1 module
    myproject
```

The report leads with a verdict, then numbered findings when something looks
wrong, then the supporting evidence: comparisons like the one above, finder
call counts, an event timeline, and stack traces for every `sys.meta_path`
and `sys.path_hooks` change. [Reading the report](https://glinte.github.io/metapathology/report/)
explains every section and finding category.

## Overhead

Rough figures from the [benchmarks](https://glinte.github.io/metapathology/performance/)
(GitHub-hosted runners, CPython 3.10 and 3.14):

- **Default monitoring:** imports take roughly 1.1–1.7× as long (median
  ~1.1× when only standard finders run, ~1.35× with a custom finder), and
  each import retains roughly 0.3–1 KB of memory for the report.
- **`--deep` diagnostics:** imports take roughly 7–15× as long and retain
  ~4 KB each. Use deep mode only in a controlled reproduction.

Every event is kept until the report is written, so memory grows with import
activity. For a long-running process, install just before the behavior of
interest and call `write_report()` and `uninstall()` once it is captured.

## How it works

`metapathology` observes imports through several cooperating mechanisms: a
[`sys.addaudithook()`](https://docs.python.org/3/library/sys.html#sys.addaudithook)
callback records each import as it starts, `sys.meta_path` and
`sys.path_hooks` are temporarily replaced with `list` subclasses that record
every change with a stack trace, each finder's `find_spec()` method is wrapped
to record whether it found the module, and `sys.path_importer_cache` is
snapshotted at key points. At report time, modules found by a custom finder
are compared with a fresh `PathFinder` search to reveal bypasses.

[How it works](https://glinte.github.io/metapathology/concepts/) walks through
Python's import machinery and exactly what each mechanism can and cannot see.

## Caveats

- CPython only. Monitoring on other implementations emits a `RuntimeWarning`
  because it relies on CPython's `import` audit event.
- Monitoring starts when metapathology does. Finders installed earlier by
  `.pth` files (this is how scikit-build-core's finder arrives) appear in the
  initial snapshot without a stack trace. An
  [optional bootstrap](https://glinte.github.io/metapathology/usage/#observe-later-pth-files)
  can start monitoring during interpreter startup to catch some of them.
- This is a debugging tool. It temporarily modifies `sys.meta_path` and
  `sys.path_hooks`; do not leave it enabled in production.
- Reports contain paths, command lines, and stack file names — review before
  sharing.

See [Limitations](https://glinte.github.io/metapathology/limitations/) for the
complete list of observation boundaries. For frozen executables (PyInstaller,
Nuitka, cx_Freeze) and embedded interpreters, see the
[frozen application guide](https://glinte.github.io/metapathology/frozen/).
