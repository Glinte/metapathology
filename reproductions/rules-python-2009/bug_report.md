# rules_python retains the coverage package directory on `sys.path`, shadowing the `python.runfiles` namespace

## Summary

The dependency resolver imports:

```python
from python.runfiles import runfiles
```

When a rules_python target runs through `bazel coverage`, rules_python uses
`coverage/__main__.py` from a downloaded coverage.py wheel as the coverage tool.
Executing that file directly makes CPython set `sys.path[0]` to the wheel's
`coverage` package directory.

rules_python patches `coverage/__main__.py` with:

```python
sys.path.append(sys.path.pop(0))
```

This moves the package directory to the end of `sys.path`, but does not remove
it. The directory contains coverage.py's internal `python.py`, which is
therefore exposed incorrectly as a top-level `python` module.

With `--incompatible_default_to_explicit_init_py`, the intended
`python/runfiles` tree is a PEP 420 namespace package. `PathFinder` collects
that earlier namespace portion but continues searching for a regular module or
package. It eventually finds the trailing `coverage/python.py`; the regular
module takes precedence over the namespace candidate. The subsequent
`python.runfiles` import fails because the selected `python` is not a package.

This is a rules_python coverage-launcher bug, not a Bazel core or coverage.py
bug. coverage.py's `python.py` is normally importable only as
`coverage.python`; it becomes a top-level candidate because rules_python
retains the package directory itself on `sys.path`.

## Minimal reproduction of the path shape

The reduction in `reproductions/rules-python-2009` contains:

```text
coverage/python.py
runfiles_root/python/runfiles/__init__.py
```

It places the intended `runfiles_root` first and the `coverage` package
directory last, matching the order produced by rules_python's patch, then
executes the dependency resolver's import:

```powershell
uv sync
uv run --no-sync python reproduce.py
```

Observed result:

```text
ModuleNotFoundError: No module named 'python.runfiles'; 'python' is not a package
```

The existing full rules_python reproduction remains:

```console
cd examples/multi_python_versions
bazel coverage --incompatible_default_to_explicit_init_py //requirements:requirements_3_10_test
```

## Diagnostic evidence

I ran the reduction under metapathology (my own tool), a diagnostic wrapper that records the
standard import machinery's finder and loader decisions:

```powershell
uv run --no-sync python -m metapathology --deep reproduce.py
```

The recorded search shows that this is not a custom import-hook conflict.
Python's standard `PathFinder` observes the earlier runfiles namespace portion,
continues searching, and later receives a regular source-module specification
for `coverage/python.py` from the trailing directory's `FileFinder`. The regular
module wins over the namespace candidate.

This also explains why `legacy_create_init = True` works: it turns the intended
`python` directory into a regular package, which wins before the search reaches
the trailing coverage directory.

## Expected behavior

rules_python should make the coverage wheel's parent directory importable and
remove the directly executed `coverage` package directory from `sys.path`.
Running a rules_python target through `bazel coverage` should not expose
coverage.py's internal modules as top-level modules or change the meaning of
`import python`.

## Current status

The coverage patch and `from python.runfiles import runfiles` import both remain
on rules_python `main`. The current bootstrap adds the coverage wheel's parent
directory, but the patch still moves the package directory to the end instead
of removing it.

A complete fix should remove that package-directory entry once the wheel parent
is available. Migrating the dependency resolver to the separately packaged
`bazel-runfiles` import path would also avoid relying on the generic top-level
`python` namespace.
