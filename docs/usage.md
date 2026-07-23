# Get started

## Run the smallest useful reproduction

Install metapathology in the environment that runs the failing program:

```console
pip install metapathology
```

Run a script:

```console
python -m metapathology app.py --app-argument
```

Run a module:

```console
python -m metapathology -m pytest tests/
```

For scripts, metapathology sets `sys.argv` and puts the script directory at the
front of `sys.path`, matching a normal Python script launch. Module execution
uses Python's [`runpy`](https://docs.python.org/3/library/runpy.html), so the
usual [`__main__` metadata differences](https://docs.python.org/3/reference/import.html#special-considerations-for-main)
and Windows multiprocessing caveats apply.

Installation also provides a `metapathology` command. Prefer
`python -m metapathology` when interpreter selection matters; it guarantees
that the monitor and target use the same Python and virtual environment.

Run without a target to open a monitored interactive interpreter:

```console
python -m metapathology
```

The interactive session preloads `metapathology`, so
`print(metapathology.render_report())` shows progress before exit. It uses
[`code.interact()`](https://docs.python.org/3/library/code.html#code.interact),
not the enhanced Python 3.13+ REPL.

Tool options must come before the target. Everything after the target is passed
to it.

The target's integer `SystemExit` status is preserved. An unhandled exception
prints its traceback and exits with status 1. The report is written in both
cases.

## Save the report

With no report option, text is written to standard error. `--report` infers the
format from `.txt` or `.json` and can be repeated:

```console
python -m metapathology \
  --report diagnosis.txt \
  --report diagnosis.json \
  -m pytest tests/
```

Use `--report-text PATH` or `--report-json PATH` when a filename has no useful
extension. `-` means standard error. File writes are atomic.

Both files describe the same captured run, so their event references match.

## Read only the useful parts

Read the report in this order:

1. Target outcome and verdict.
2. Numbered problems and risks.
3. Finder-result comparisons for affected modules.
4. Event timeline and state-change stacks when the finding needs proof.

Notes are context, not failures. A custom finder result is also context unless
it is connected to an observed effect.

## Add evidence only when needed

Default capture is designed for the first pass. If the report says the missing
evidence requires detailed capture, rerun with:

```console
python -m metapathology --detailed-capture app.py
```

Detailed capture is slower and wraps more of the import machinery. Prefer one
specific option when you know what is missing, for example:

```console
python -m metapathology --capture-loader-calls app.py
```

See [Choosing capture](capture.md) for the decision table.

## Invoke skipped candidates only in a disposable run

If the report shows a skipped candidate and you still need its live answer,
rerun the smallest reproduction with:

```console
python -m metapathology --unsafe-explore-import-branches app.py
```

Run it only in a disposable process or container. It executes skipped
third-party code, and side effects cannot be rolled back. Read the result as
“what it returned now,” not “what would have won.” See
[Choosing capture](capture.md#unsafe-import-branch-exploration).

## Stop after the relevant operation

Long-lived processes retain events until reporting. If the CLI wrapper is not a
good fit, scope capture in code:

```python
import metapathology

with metapathology.monitoring() as monitor:
    reproduce_problem()

metapathology.write_report("diagnosis.json", format="json")
```

The context manager restores import state when it owns the installation.

## Next

- [Reading the report](report.md)
- [Startup timing](startup.md)
- [Configuration reference](configuration.md)
- [Library API](api.md)
