<h1 align="center">metapathology</h1>

<p align="center">
  Find out which Python import hook handled a module—and what it prevented from running.
</p>

<p align="center">
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/v/metapathology.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/pyversions/metapathology.svg" alt="Supported Python versions"></a>
  <a href="https://github.com/Glinte/metapathology/actions/workflows/test.yml"><img src="https://github.com/Glinte/metapathology/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://glinte.github.io/metapathology/"><img src="https://github.com/Glinte/metapathology/actions/workflows/docs.yml/badge.svg" alt="Documentation"></a>
</p>

Import hooks power assertion rewriting, editable installs, runtime type
checking, tracing, and packaging tools. When two hooks claim the same module,
the first one can quietly bypass the other. `metapathology` runs the failing
program and records enough evidence to show that contention.

By default, it only observes imports: it never returns a module spec or changes
which finder wins. Runtime code uses only the Python standard library and
supports CPython 3.10+.

## Start here

```console
pip install metapathology
python -m metapathology your_script.py --your-args
python -m metapathology -m pytest tests/
```

The program runs normally. A report is written to standard error when it
finishes, including when it exits with an exception.

Use files when the report is long or needs to be shared:

```console
python -m metapathology \
  --report import-report.txt \
  --report import-report.json \
  -m pytest tests/
```

Options belong before the target. Arguments after the target belong to the
target.

## What to read first

Start with the verdict and numbered findings. A result such as:

```text
[module-hides-namespace] 'example.plugins'
```

means the report found evidence of a specific import problem. Each finding
links to the relevant finder calls, state changes, and import searches.

“Found by a custom finder” is not automatically a problem. The default
standard-path check compares that observed result with what `PathFinder`
returns at report time. It describes current state; it does not predict which
finder would have won earlier.

## When wrapping is impossible

The CLI is preferred because it starts monitoring before the target runs. For
notebooks, embedded interpreters, or a `conftest.py`, install explicitly:

```python
import metapathology

metapathology.install()
```

Defaults cover the normal investigation. Detailed capture is an explicit
opt-in:

```python
metapathology.install(
    capture=metapathology.CaptureConfig(detailed=True),
)
```

Use `DetailedCaptureConfig` only when selecting individual detailed
mechanisms. Use `AnalysisConfig` only when changing report-time checks.

## Overhead

In the published synthetic benchmarks, default capture made standard-only
imports a median 1.10× as slow and imports seen by a custom finder 1.35× as
slow. Retained report data was about 342 bytes and 974 bytes per import,
respectively. Enabling every detailed mechanism was much heavier: a median
13.08× import-time ratio and 4.01 KiB retained per import.

See [Speed and memory](https://glinte.github.io/metapathology/performance/) for
the full ranges, methodology, and benchmark runs. Use detailed capture only in
a controlled reproduction.

## Important limitations

- Monitoring starts when metapathology starts. Finders installed earlier,
  including those from already-processed `.pth` files, appear only in the
  initial snapshot.
- Default capture retains every event until reporting, so memory grows with
  import activity.
- Detailed capture wraps more import machinery and has materially higher
  overhead. Use it in a controlled reproduction.
- Reports can contain paths, command-line arguments, and stack filenames.
  Review them before sharing.
- `uninstall()` restores ordinary lists and removes finder instrumentation.
  CPython audit hooks cannot be removed, so the installed hook becomes inert.

Continue with the
[getting-started guide](https://glinte.github.io/metapathology/usage/), then
[reading the report](https://glinte.github.io/metapathology/report/).
Configuration, capture coverage, JSON, startup timing, and limitations each
have focused pages in the
[full documentation](https://glinte.github.io/metapathology/).

For background on the machinery being inspected, see Python's
[import system reference](https://docs.python.org/3/reference/import.html) and
the [`sys.meta_path` documentation](https://docs.python.org/3/library/sys.html#sys.meta_path).
